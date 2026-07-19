#!/usr/bin/env python3
"""frequency.py — per-category calendar frequency census.

Answers "how often does each style category actually occur on my calendars?" by
fetching a trailing window of events across every configured calendar and running
each through the SAME classifier the styling engine uses (`_desired_style`), so
the counts line up exactly with the colors. This is the data behind the
frequency-aware allocation rule (taxonomy `rules.color_merges` / the B3 principle):
the most-frequent categories should hold solo colorIds; sharing is for the
low-frequency and/or cross-calendar tail. Running it also surfaces the frequency
soft-warning (validate_config with a live frequency map) when two high-frequency
categories share a color.

    ~/.claude/lib/helper-venv/bin/python3 -m gcal.frequency            # 6mo + 90d
    ~/.claude/lib/helper-venv/bin/python3 -m gcal.frequency --window-days 365

Read-only: never writes to the calendar. Requires the same OAuth token the daily
styling jobs use (per-account, from the config).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from . import config as _gcfg
    from . import calendar_maintenance as _cm
    from . import rest as _rest
except ImportError:  # pragma: no cover — direct invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gcal import config as _gcfg
    from gcal import calendar_maintenance as _cm
    from gcal import rest as _rest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import taxonomy as _tax  # noqa: E402


def _start(event: dict) -> Optional[datetime]:
    s = event.get("start") or {}
    ds = s.get("dateTime") or s.get("date")
    if not ds:
        return None
    try:
        d = datetime.fromisoformat(str(ds).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def census(window_days: int = 180, recent_days: int = 90,
           now: Optional[datetime] = None) -> dict:
    """Return {'total': Counter, 'recent': Counter, 'by_cal': {alias: Counter},
    'window_days','recent_days','scanned','errors'}. `now` is injectable for tests
    (the module never calls datetime.now implicitly elsewhere)."""
    now = now or datetime.now(timezone.utc)
    tmin = (now - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmax = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff = now - timedelta(days=recent_days)
    learned, learned_icons = _cm.load_learned(), _cm.load_learned_icons()

    total: Counter = Counter()
    recent: Counter = Counter()
    by_cal: dict[str, Counter] = defaultdict(Counter)
    errors: list[str] = []
    scanned = 0

    for cal in _gcfg.calendars():
        if cal.get("color") is False:
            continue
        cal_id = cal.get("id")
        alias = cal.get("alias") or cal_id
        default_cat = cal.get("default_category") or "household"
        token = _gcfg.token_for(cal.get("account") or "default")
        if not (cal_id and token):
            errors.append(f"{alias}: no id/token configured")
            continue
        try:
            inv = _rest.make_invoker(token)
            events = _cm._fetch(inv, [cal_id], tmin, tmax).get(cal_id, [])
        except Exception as e:  # noqa: BLE001 — one bad calendar shouldn't abort
            errors.append(f"{alias}: fetch failed ({e})")
            continue
        for e in events:
            if not isinstance(e, dict) or not (e.get("summary") or "").strip():
                continue
            scanned += 1
            cat, _color, _icon = _cm._desired_style(e, learned, default_cat, learned_icons)
            total[cat] += 1
            by_cal[alias][cat] += 1
            st = _start(e)
            if st and st >= cutoff:
                recent[cat] += 1
    return {"total": total, "recent": recent, "by_cal": by_cal,
            "window_days": window_days, "recent_days": recent_days,
            "scanned": scanned, "errors": errors, "as_of": now.strftime("%Y-%m-%d")}


def _bar(n: int, top: int, width: int = 20) -> str:
    if top <= 0:
        return ""
    filled = round(n / top * width)
    return "█" * filled if filled else ("▏" if n else "")


def render(c: dict) -> str:
    total, recent, by_cal = c["total"], c["recent"], c["by_cal"]
    grand = sum(total.values()) or 1
    top = max(total.values(), default=0)
    aliases = list(by_cal.keys())
    lines = [f"Calendar frequency census — as of {c['as_of']}, "
             f"trailing {c['window_days']//30}mo ({c['scanned']} events)",
             ""]
    lines.append(f"{'category':18}{'count':>6}{'%':>7}{'recent':>8}   bar")
    for cat, n in total.most_common():
        pct = 100.0 * n / grand
        lines.append(f"{cat:18}{n:>6}{pct:>6.1f}%{recent[cat]:>8}   {_bar(n, top)}")
    # per-calendar breakdown
    lines.append("")
    for alias in aliases:
        parts = ", ".join(f"{k}={v}" for k, v in by_cal[alias].most_common())
        lines.append(f"  [{alias}] {parts}")
    # frequency soft-warnings (B5): two high-frequency categories sharing a color
    warns = _tax.validate_config(_tax.CONFIG, frequency=dict(total))
    freq_warns = [w for w in warns if "high-frequency" in w]
    if freq_warns:
        lines.append("")
        lines.append("Frequency warnings:")
        lines += [f"  - {w}" for w in freq_warns]
    if c["errors"]:
        lines.append("")
        lines.append("Errors:")
        lines += [f"  - {e}" for e in c["errors"]]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Per-category calendar frequency census.")
    p.add_argument("--window-days", type=int, default=180)
    p.add_argument("--recent-days", type=int, default=90)
    args = p.parse_args(argv)
    print(render(census(window_days=args.window_days, recent_days=args.recent_days)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
