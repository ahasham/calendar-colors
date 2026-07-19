#!/usr/bin/env python3
"""gcal/config.py — resolution of the INSTANCE-only config sections.

taxonomy.py owns loading + merging + validating the whole config (shipped
`calendar-colors.default.yml` + the user overlay `~/.claude/config/calendar-colors.yml`)
and exposes the merged dict as `taxonomy.CONFIG`. This module reads the
instance-only sections from it — the calendar list, accounts/tokens, paths, and
the title-rewrite self-names — and derives the small views the styling engine,
the schedulers, and the title-rewrite subsystem need. Kept separate from the
styling taxonomy so there is exactly one load + one validate for the system.

A fresh clone with NO user config yields empty calendars/accounts (the styling
engine still works read-only against `--calendars` CLI args); a configured
instance yields the real list.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:                                        # mirror calendar_maintenance's import
    import taxonomy as _tax
except ImportError:                          # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import taxonomy as _tax

_DEFAULT_OAUTH_DIR = "~/.claude/state/calendar/oauth"
_DEFAULT_STATE_DIR = "~/.claude/state/calendar"


def _cfg() -> dict:
    return _tax.CONFIG or {}


def calendars() -> list[dict]:
    """The full ordered list of configured calendar rows (may be empty)."""
    return list(_cfg().get("calendars") or [])


def calendars_for(account: str = "default") -> list[dict]:
    return [c for c in calendars() if (c.get("account") or "default") == account]


def calendar_ids(account: str | None = None) -> list[str]:
    """Calendar ids, optionally filtered to one account. Feeds the schedulers'
    `--calendars` arg so the plists no longer hardcode ids."""
    rows = calendars() if account is None else calendars_for(account)
    return [c["id"] for c in rows if c.get("id")]


def hard_color_only_calendars() -> set[str]:
    """Ids flagged `hard_color_only: true` — the work calendar(s) that must NEVER
    receive an emoji/title write regardless of other flags. Derived from config
    (replaces a previously hardcoded work-calendar id)."""
    return {c["id"] for c in calendars() if c.get("hard_color_only") and c.get("id")}


def paths() -> dict:
    return _cfg().get("paths") or {}


def accounts() -> dict:
    return _cfg().get("accounts") or {}


def oauth_dir() -> Path:
    return Path(paths().get("oauth_dir") or _DEFAULT_OAUTH_DIR).expanduser()


def state_dir() -> Path:
    return Path(paths().get("state_dir") or _DEFAULT_STATE_DIR).expanduser()


def token_for(account: str = "default") -> str | None:
    """Absolute OAuth token path for an account, or None if unconfigured."""
    tok = (accounts().get(account) or {}).get("token")
    return str(oauth_dir() / tok) if tok else None


def self_names() -> list[str]:
    """Names the title-rewrite gate must preserve on invited events (step 5)."""
    return list(_cfg().get("self_names") or [])


# ── tiny CLI so the launchd plists / shell jobs can read config values ────────
# e.g.  python3 gcal/config.py --calendar-ids --account default
#       python3 gcal/config.py --token --account work
if __name__ == "__main__":  # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--calendar-ids", action="store_true")
    ap.add_argument("--token", action="store_true")
    ap.add_argument("--account", default=None)
    a = ap.parse_args()
    if a.calendar_ids:
        print(",".join(calendar_ids(a.account)))
    elif a.token:
        print(token_for(a.account or "default") or "")
