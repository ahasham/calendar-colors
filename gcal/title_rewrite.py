#!/usr/bin/env python3
"""gcal/title_rewrite.py — safety + evaluation core for LLM calendar-title rewriting.

Rewriting an event title with an LLM is high-value (cleans "me to replace furnace
filter" → "Replace furnace filter") but dangerous: it can drop a flight code, an
age note "(52 G)", a `/command`, or reword something personal. This module is the
PURE, TESTABLE guard the LLM rewrite must pass before it's applied. The LLM (in a
claude -p job) only PROPOSES {rewrite, confidence}; the decision to apply is made
here, deterministically.

Three gates the user chose:
  1. NO-INFO-LOSS validator — every "must-keep" token in the original (numbers,
     codes, parentheticals, [tags], /commands, proper nouns) must survive in the
     rewrite, or it's REJECTED. This mechanically catches the "(52 G)", "/marriage",
     "AC 541", "DS YYZ" failures.
  2. CONFIDENCE + RISK gating — auto-apply only high-confidence, LOW-risk rewrites;
     sensitive/ambiguous ones are never auto-applied (→ review).
  3. REVERSION log — the original title is stored before any change, so every
     rewrite is one-click reversible (append-only JSONL).

`decide()` is the single entry point: given (original, proposed, confidence) it
returns an APPLY / REVIEW / REJECT verdict + reason. Pure (except record_reversion,
which appends to disk). No MCP, no LLM here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# ── config-derived paths + personals (gcal/config → unified config) ──────────
try:
    from gcal import config as _gcfg
except Exception:  # pragma: no cover — standalone/direct import
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import config as _gcfg
    except Exception:
        _gcfg = None


def _state_dir() -> Path:
    try:
        return _gcfg.state_dir() if _gcfg else Path("~/.claude/state/calendar").expanduser()
    except Exception:
        return Path("~/.claude/state/calendar").expanduser()


REVERSION_LOG = _state_dir() / "title_reversions.jsonl"
ABBREV_PATH = _state_dir() / "abbreviations.json"
CONFIDENCE_FLOOR = 0.75  # set 2026-07-14 after review; the 0.70-0.74 band was only
# uncertain partner-wording rewords → route those to review, auto-apply the rest.


def _load_abbreviations() -> dict[str, str]:
    """Known {abbreviation: expansion} (e.g. sf→San Francisco). Lets the validator
    treat expanding a code as LOSSLESS instead of a dropped must-keep token."""
    try:
        import json as _json
        d = _json.loads(ABBREV_PATH.read_text()).get("abbreviations", {})
        return {k.lower(): v for k, v in d.items()}
    except Exception:
        return {}

# The user's own name(s) — sourced from the unified config (`self_names`).
# Normally redundant on one's own calendar (droppable), BUT kept on events with
# OTHER attendees so invitees know whose event it is: must-keep IFF the event has
# guests. Empty when unconfigured → the self-name gate is simply inactive (no
# personal name is baked into shipped code).
try:
    SELF_NAMES = tuple(_gcfg.self_names()) if _gcfg else ()
except Exception:
    SELF_NAMES = ()

# ── Sensitive / never-auto-rewrite signals (force REVIEW even if valid) ──────────
_SENSITIVE = re.compile(
    r"\b(sex|intima|marriage|private|therapy|health|medical|doctor|surgery|"
    r"self-score|diagnos|prescription|counsel)\b", re.IGNORECASE)

# Capitalized COMMON words that are NOT proper names — a rewrite may legitimately
# reword or drop these. Real names/places NOT in this set stay must-keep (dropping
# a real name like "Gautam" = loss). Structured tokens (codes/parentheticals/tags/
# commands) are must-keep regardless — handled separately below. The user's own
# configured self_names are folded in below (a self-name is droppable on one's own
# calendar; the has-attendees self-name gate re-adds it when guests are present).
_COMMON_WORDS = {
    "the", "and", "with", "this", "that", "week", "day", "night", "date",
    "birthday", "bday", "anniversary", "exercise", "workout", "marriage", "sex",
    "cleaning", "school", "flight", "meeting", "dinner", "lunch", "brunch",
    "coffee", "drinks", "party", "gathering", "catch", "review", "plan", "reminder",
    "connect", "karate", "yoga", "run", "class", "grade", "memory", "visit",
    "schedule", "replace", "fill", "take", "measure", "first", "monthly",
    "biweekly", "weekly", "cup", "final", "match", "dad", "mom", "pops", "self",
    "score", "private", "hold", "reschedule", "call", "check", "morning",
}
# the user's own name(s) are droppable common words — sourced from config, not hardcoded
_COMMON_WORDS |= {n.lower() for n in SELF_NAMES}


# ── Must-keep token extraction (the no-info-loss contract) ───────────────────────

def must_keep_tokens(title: str) -> list[str]:
    """High-value tokens that MUST survive a rewrite. Dropping any = info loss.

    Covers: parentheticals '(52 G)'; bracket tags '[HOLD]'; slash-commands
    '/marriage'; runs of digits / alnum codes 'AC 541', 'YYZ', '541'; and proper
    nouns (Capitalized words, incl. ALL-CAPS acronyms like 'DS'/'YYZ'/'ACME').
    Emoji + lowercase connective words are intentionally NOT must-keep (a rewrite
    may reword those). Returned lower-cased for case-insensitive survival checks.
    """
    t = str(title or "")
    keep: list[str] = []
    keep += re.findall(r"\([^)]*\)", t)                 # (52 G), (private), (AC 541)
    keep += re.findall(r"\[[^\]]*\]", t)                # [HOLD]
    keep += re.findall(r"/\w+", t)                      # /marriage
    keep += re.findall(r"\b[A-Za-z]{0,3}\s?\d{2,}\b", t)  # AC 541, 401, 20260731
    keep += re.findall(r"\b[A-Z]{2,}\b", t)             # DS, YYZ, ACME, AK (acronyms/codes)
    # proper nouns: Capitalized words, EXCLUDING common calendar words + the user's
    # own name(s) (so real names/places like Gautam/Chicago are must-keep, but a
    # reword of "Marriage"/"Sex" or dropping a redundant self-name is allowed).
    for w in re.findall(r"\b[A-Z][a-z]{2,}\b", t):
        if w.lower() not in _COMMON_WORDS:
            keep.append(w)
    # normalize + dedup, drop empties
    out, seen = [], set()
    for k in keep:
        k = k.strip().lower()
        if k and k not in seen:
            seen.add(k); out.append(k)
    return out


def missing_tokens(original: str, rewrite: str) -> list[str]:
    """Must-keep tokens from `original` that DON'T appear in `rewrite` (substring,
    case-insensitive). A token counts as PRESENT if its known expansion is in the
    rewrite (e.g. "sf" satisfied by "san francisco") — a lossless expansion, not a
    drop. Non-empty → info loss → reject."""
    r = str(rewrite or "").lower()
    abbr = _load_abbreviations()
    miss = []
    for tok in must_keep_tokens(original):
        if tok in r:
            continue
        exp = abbr.get(tok)
        if exp and all(w in r for w in exp.lower().split()):
            continue  # abbreviation expanded in the rewrite → lossless
        miss.append(tok)
    return miss


# ── Risk classification ──────────────────────────────────────────────────────

def risk_class(title: str) -> str:
    """'high' if the title is sensitive or carries a command/tag we must not
    disturb; else 'low'. High-risk titles are never AUTO-applied (→ review)."""
    t = str(title or "")
    # /command (slash immediately followed by a word, e.g. "/marriage") is high —
    # but a bare separator "Sam / Shim" is NOT a command, so require /\w.
    if _SENSITIVE.search(t) or re.search(r"/\w", t) or "[" in t:
        return "high"
    return "low"


# ── Reversion log (safety net) ───────────────────────────────────────────────

def record_reversion(event_id: str, calendar_id: str, original: str,
                     rewrite: str, applied_at: str) -> None:
    """Append the pre-rewrite title so any change is reversible. Best-effort."""
    try:
        REVERSION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(REVERSION_LOG, "a") as fh:
            fh.write(json.dumps({
                "event_id": event_id, "calendar_id": calendar_id,
                "original": original, "rewrite": rewrite, "applied_at": applied_at,
            }, ensure_ascii=False) + "\n")
    except Exception:  # pragma: no cover
        pass


# ── The decision ─────────────────────────────────────────────────────────────

def decide(original: str, proposed: str, confidence: float,
           has_attendees: bool = False) -> dict:
    """Deterministic verdict on an LLM-proposed title rewrite.

    Returns {verdict: apply|review|reject|noop, reason, missing:[...]}.
      - noop   : proposed == original (nothing to do)
      - reject : info loss (a must-keep token vanished) — never apply
      - review : valid but not safe to AUTO-apply (low confidence OR high-risk)
      - apply  : high-confidence, low-risk, no info loss → safe to auto-apply

    `has_attendees`: when the event has OTHER guests, the user's own name is
    must-keep (invitees rely on it to know whose event it is) — dropping it →
    reject. On solo events the name stays droppable (redundant).
    """
    orig = str(original or "").strip()
    prop = str(proposed or "").strip()
    if not prop or prop == orig:
        return {"verdict": "noop", "reason": "no change proposed", "missing": []}
    miss = missing_tokens(orig, prop)
    if has_attendees:
        low_orig, low_prop = orig.lower(), prop.lower()
        for nm in SELF_NAMES:
            if re.search(rf"\b{re.escape(nm)}\b", low_orig) and not \
                    re.search(rf"\b{re.escape(nm)}\b", low_prop):
                miss.append(nm)
    if miss:
        return {"verdict": "reject", "reason": f"info loss: dropped {miss}",
                "missing": miss}
    if confidence is None or confidence < CONFIDENCE_FLOOR:
        return {"verdict": "review", "reason": f"confidence {confidence} < {CONFIDENCE_FLOOR}",
                "missing": []}
    if risk_class(orig) == "high":
        return {"verdict": "review", "reason": "sensitive/command/tag — human review",
                "missing": []}
    return {"verdict": "apply", "reason": "high-confidence, low-risk, no info loss",
            "missing": []}
