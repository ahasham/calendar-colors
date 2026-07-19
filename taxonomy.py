#!/usr/bin/env python3
"""taxonomy.py — the SINGLE canonical category→style source for every surface.

Before this module, five places defined colors independently (calendar_icons,
gcal/style, gcal/calendar_maintenance via calendar_icons, gmail/hygiene, and
events/occasions_sync) and they DRIFTED. This module is the one place a
category's color/emoji/label lives; consumers import their slice.

As of the T1 publishability refactor the VALUES no longer live in this file —
they are loaded from `calendar-colors.default.yml` (shipped defaults, no personal
data) with an optional user overlay deep-merged on top:

    default:  <this dir>/calendar-colors.default.yml
    user:     $CALENDAR_COLORS_CONFIG  (default ~/.claude/config/calendar-colors.yml)

The module then rebuilds the SAME public names it always exported, so the six
importers (calendar_icons, gcal/style, gcal/calendar_maintenance,
events/occasions_sync, gmail/hygiene, tests) are unaffected:

    CATEGORIES, CALENDAR_CATEGORY_STYLE, OCCASION_COLOR, GMAIL_LABELS,
    GMAIL_LABEL_COLORS, OPERATIONAL_LABEL_COLORS, color_id(), emoji()

Color spaces differ by surface, so each category carries both a Google Calendar
`colorId` ("1".."11" or None) and a Gmail label NAME (or None). Calendar colorIds
are distinct across the 11 calendar categories EXCEPT for deliberate merges
declared in `rules.color_merges` (errands+household share Graphite/8). Sub-types
(anniversary/memorial/working_location/broad_meeting/regular_meeting/interview)
reuse a calendar color by design (only 11 slots). Recolor a category by editing
the yaml — never hardcode a color anywhere else.

Change history / design rationale lives in
`gold/Research/Google Calendar beautification methodology.md`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import yaml  # provided by the helper venv (system python lacks it)

_DEFAULT_CFG = Path(__file__).with_name("calendar-colors.default.yml")
_USER_CFG = Path(os.environ.get(
    "CALENDAR_COLORS_CONFIG", "~/.claude/config/calendar-colors.yml")).expanduser()


def _deep_merge(base: dict, over: dict) -> dict:
    """Recursively overlay `over` onto `base` (dicts merge key-wise; scalars and
    lists replace). Used to apply the user overlay onto the shipped defaults."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def validate_config(cfg: dict, *, strict: bool = False,
                    frequency: Optional[dict] = None) -> list[str]:
    """Check the taxonomy invariants declared in `cfg['rules']`. Returns a list of
    problem strings ([] if clean). `strict=True` raises ValueError on any problem
    (setup / editor / tests); the default warns to stderr so a headless cron run
    never crashes on a bad user overlay (matches load_feature_config's ethos).

    `frequency` (optional {category: events_per_window}) enables the frequency
    rule: it soft-warns when a declared merge groups two HIGH-frequency categories
    — the allocation principle is that frequent categories hold solo colorIds and
    only low-frequency and/or cross-calendar pairs may share. Supplied by the
    `--frequency` report; omitted by the plain load (which has no live counts)."""
    problems: list[str] = []
    cats = cfg.get("categories", {})
    cal_cats = list(cfg.get("calendar_categories", []))
    rules = cfg.get("rules", {}) or {}

    for c in cal_cats:
        if c not in cats:
            problems.append(f"calendar_categories references unknown category {c!r}")

    # distinct colorIds EXCEPT declared merge-groups; no UNDECLARED collision.
    # Group EVERY colored category (main + sub-types) by colorId; any group with
    # >1 category that is not a subset of a declared merge-group is an illegal
    # collision. (Pre-2026-07 this only checked the 11 calendar_categories, so the
    # sub-type pile-up on Graphite went unflagged — now everything is covered.)
    merges = [set(g) for g in rules.get("color_merges", [])]
    by_color: dict[str, list[str]] = {}
    for name, spec in cats.items():
        cid = (spec or {}).get("colorId")
        if cid is not None:
            by_color.setdefault(cid, []).append(name)
    for cid, members in by_color.items():
        if len(members) > 1 and not any(set(members) <= g for g in merges):
            problems.append(f"undeclared colorId collision on {cid!r}: {sorted(members)} "
                            f"(add to rules.color_merges if intentional)")

    # frequency rule (only when live counts are supplied): a declared merge that
    # groups two HIGH-frequency categories is a soft warning — frequent categories
    # deserve their own color; sharing is for the low-frequency / cross-cal tail.
    if frequency:
        thr = rules.get("high_frequency_threshold", 40)
        for g in merges:
            hi = sorted(c for c in g if (frequency.get(c) or 0) >= thr)
            if len(hi) >= 2:
                problems.append(
                    f"high-frequency categories {hi} share a colorId "
                    f"(each ≥{thr}/window) — consider giving one its own color")

    if problems and strict:
        raise ValueError("taxonomy config invalid:\n  - " + "\n  - ".join(problems))
    if problems:
        print("taxonomy.py: config warnings:\n  - " + "\n  - ".join(problems),
              file=sys.stderr)
    return problems


def _load() -> dict:
    cfg = yaml.safe_load(_DEFAULT_CFG.read_text()) or {}
    if _USER_CFG.exists():
        try:
            user = yaml.safe_load(_USER_CFG.read_text()) or {}
            cfg = _deep_merge(cfg, user)
        except Exception as e:  # never let a bad user file crash a headless job
            print(f"taxonomy.py: ignoring unreadable user config {_USER_CFG}: {e}",
                  file=sys.stderr)
    validate_config(cfg, strict=False)
    return cfg


_CFG = _load()

# ── the public surface (rebuilt from config; identical shape to pre-refactor) ──
# category -> {colorId, emoji, gmail}
CATEGORIES: dict[str, dict] = _CFG["categories"]

# The 11 calendar categories (distinct colorIds save declared merges).
_CALENDAR_CATS = tuple(_CFG["calendar_categories"])

# category -> (colorId, emoji) for calendar consumers (calendar_icons / style).
CALENDAR_CATEGORY_STYLE: dict[str, tuple[Optional[str], Optional[str]]] = {
    c: (CATEGORIES[c]["colorId"], CATEGORIES[c]["emoji"]) for c in _CALENDAR_CATS
}

# /events occasion type -> colorId (occasions_sync.COLOR).
OCCASION_COLOR: dict[str, str] = dict(_CFG["occasion_color"])

# category -> Gmail label name (for gmail/hygiene DOMAIN_RULES cross-reference).
GMAIL_LABELS: dict[str, str] = {
    c: v["gmail"] for c, v in CATEGORIES.items() if v.get("gmail")
}

# category -> (backgroundColor, textColor) from Gmail's fixed label palette.
GMAIL_LABEL_COLORS: dict[str, tuple[str, str]] = {
    k: tuple(v) for k, v in _CFG["gmail_label_colors"].items()
}

# Gmail OPERATIONAL labels (GTD action tier + read-later queue).
OPERATIONAL_LABEL_COLORS: dict[str, tuple[str, str]] = {
    k: tuple(v) for k, v in _CFG["operational_label_colors"].items()
}

# invariant declarations (consumed by gcal/config.py at calendar-assignment time).
RULES: dict = _CFG.get("rules", {}) or {}

# Full merged config (default + user overlay). gcal/config.py reads the
# instance-only sections (calendars / accounts / paths / self_names) from here so
# there is a single load + single validate for the whole system.
CONFIG: dict = _CFG


def color_id(category: str) -> Optional[str]:
    return CATEGORIES.get(category, {}).get("colorId")


def emoji(category: str) -> Optional[str]:
    return CATEGORIES.get(category, {}).get("emoji")
