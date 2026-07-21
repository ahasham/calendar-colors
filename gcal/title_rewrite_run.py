#!/usr/bin/env python3
"""gcal/title_rewrite_run.py — operational driver for LLM title rewriting.

Pairs with the PURE gcal/title_rewrite.py (extraction + gates). This module does
the impure parts: read the per-calendar feature-config, fetch candidate events
via the Calendar REST API, and apply an LLM-proposed rewrite ONLY if
title_rewrite.decide() returns "apply" (logging a reversion first). The LLM lives
in the calendar-title-rewrite.sh claude -p job, which calls this CLI.

Flow (driven by the .sh job):
  1. `--list-candidates`  → JSON [{event_id, calendar_id, title}] for calendars
                            whose feature-config title_rewrite == "llm" (primary).
  2. LLM proposes {rewrite, confidence} per title.
  3. `--apply --event-id … --calendar-id … --original … --proposed … --confidence …`
     → decide(); on "apply" PATCH the summary + record reversion; print the verdict.

Auth: calendar OAuth token (~/.claude/state/calendar/oauth/token.json), raw urllib
(no google-api dep). helper-venv python (has yaml for the config).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # lib/
from gcal import title_rewrite as tr  # noqa: E402

TOKEN = Path("~/.claude/state/calendar/oauth/token.json").expanduser()
API = "https://www.googleapis.com/calendar/v3/calendars"
_LOCKED = {"fromGmail", "birthday", "workingLocation", "outOfOffice", "focusTime"}


def _llm_calendars() -> list[str]:
    """Calendar ids whose unified config sets title_rewrite: llm.
    Source of truth is the unified config (calendar-colors.yml) via gcal/config —
    the old per-skill feature-config.yml is retired."""
    try:
        from gcal import config as _gcfg
    except Exception:  # pragma: no cover
        import config as _gcfg
    return [c["id"] for c in _gcfg.calendars()
            if c.get("id") and c.get("title_rewrite") == "llm"]


def _access_token() -> str:
    t = json.loads(TOKEN.read_text())
    data = urllib.parse.urlencode({
        "client_id": t["client_id"], "client_secret": t["client_secret"],
        "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
    r = urllib.request.urlopen(urllib.request.Request(t["token_uri"], data=data,
                                                      method="POST"), timeout=30)
    return json.loads(r.read())["access_token"]


_AT = None


def _api(method: str, path: str, body: dict = None, params: dict = None):
    global _AT
    if _AT is None:
        _AT = _access_token()
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_AT}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    r = urllib.request.urlopen(req, timeout=60)
    b = r.read()
    return json.loads(b) if b else {}


def list_candidates(window_days: int = 365) -> list[dict]:
    """Events on the llm-enabled calendars eligible for a title rewrite. Skips
    locked eventTypes and skill-managed events (never rewrite those titles)."""
    tmin = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmax = (datetime.now(timezone.utc) + timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, seen = [], set()
    for cal in _llm_calendars():
        page = None
        while True:
            q = {"timeMin": tmin, "timeMax": tmax, "maxResults": 250,
                 "singleEvents": "false"}
            if page:
                q["pageToken"] = page
            r = _api("GET", f"{urllib.parse.quote(cal)}/events", params=q)
            for e in r.get("items", []):
                s = (e.get("summary") or "").strip()
                if not s or s in seen:
                    continue
                if e.get("eventType") in _LOCKED:
                    continue
                if "managed by /" in (e.get("description") or "").lower():
                    continue
                # GLOBAL-EDIT ONLY. A title change must propagate to everyone, not
                # be a per-calendar LOCAL-ONLY override ("changes only reflected on
                # this calendar"). Guaranteed when: you're the organizer; the
                # organizer granted guestsCanModify; the organizer IS this (group)
                # calendar (co-owned shared cal); or the event has no other
                # attendees (your own private event). An event organized by someone
                # ELSE → editing only changes YOUR copy → skip.
                org = e.get("organizer") or {}
                global_editable = (org.get("self") or e.get("guestsCanModify")
                                   or org.get("email") == cal
                                   or not e.get("attendees"))
                if not global_editable:
                    continue
                seen.add(s)
                out.append({"event_id": e.get("id"), "calendar_id": cal, "title": s,
                            "has_attendees": bool(e.get("attendees"))})
            page = r.get("nextPageToken")
            if not page:
                break
    return out


def apply_rewrite(event_id: str, calendar_id: str, original: str,
                  proposed: str, confidence: float,
                  has_attendees: bool = False) -> dict:
    """Gate the proposed rewrite; on 'apply' PATCH the summary + log a reversion."""
    verdict = tr.decide(original, proposed, confidence, has_attendees=has_attendees)
    if verdict["verdict"] == "apply":
        now = datetime.now(timezone.utc).isoformat()
        try:
            _api("PATCH", f"{urllib.parse.quote(calendar_id)}/events/{event_id}",
                 body={"summary": proposed})
            tr.record_reversion(event_id, calendar_id, original, proposed, now)
        except urllib.error.HTTPError as ex:
            # Not ours to retitle after all (403/400) — downgrade, don't crash or
            # log a reversion for a change that didn't happen.
            if ex.code in (400, 403):
                verdict = {"verdict": "not_editable",
                           "reason": f"HTTP {ex.code} — title not editable by us"}
            else:
                raise
    verdict.update({"event_id": event_id, "original": original, "proposed": proposed})
    return verdict


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="LLM title-rewrite driver (gated).")
    p.add_argument("--list-candidates", action="store_true")
    p.add_argument("--window-days", type=int, default=365)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--event-id"); p.add_argument("--calendar-id")
    p.add_argument("--original"); p.add_argument("--proposed")
    p.add_argument("--confidence", type=float, default=0.0)
    p.add_argument("--has-attendees", action="store_true",
                   help="Event has other guests → the configured self_name(s) are must-keep.")
    p.add_argument("--decide", action="store_true",
                   help="Verdict only (no fetch/apply) — for testing a rewrite.")
    a = p.parse_args(argv)
    if a.list_candidates:
        print(json.dumps({"calendars": _llm_calendars(),
                          "candidates": list_candidates(a.window_days)},
                         ensure_ascii=False, indent=2))
        return 0
    if a.decide:
        print(json.dumps(tr.decide(a.original, a.proposed, a.confidence), ensure_ascii=False))
        return 0
    if a.apply:
        print(json.dumps(apply_rewrite(a.event_id, a.calendar_id, a.original,
                                       a.proposed, a.confidence,
                                       has_attendees=a.has_attendees),
                         ensure_ascii=False))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
