#!/usr/bin/env python3
"""calendar_icons.py — central content→emoji map for calendar styling.

SINGLE SOURCE OF TRUTH for event-content icons, importable by any skill that
styles calendar events (today: lib/gcal/style.py; future: any skill that wants
a content-appropriate glyph). Keeps icon choices researched + consistent across
skills instead of each one inventing its own.

Two layers:
  - CATEGORY palette/emoji — the 8 colorblind-safe colorId categories + their
    default emoji (mirrors lib/gcal/style.py CATEGORY_STYLE; re-exported here so
    consumers have one import).
  - CONTENT_ICONS — keyword → glyph overrides that pick a *content-appropriate*
    icon (soccer ⚽, bbq 🍖, movie 🎬) while the category COLOR is unchanged.
    `content_icon(text)` returns the override for the first matching keyword,
    else None (caller falls back to the category emoji).

RESEARCH NOTE (2026-05-29): icons are deliberately a single BASE codepoint,
widely supported (Unicode ≤ 12.0) glyphs that render reliably across Google
Calendar web / iOS / Android. AVOIDED: ZWJ sequences (🧑‍🍳), flags, skin-tone
modifiers, and very-new emoji — these tofu (□) on older Android/webview clients.
Web convention scan (emojipedia / emojiterra / emojicombos events sets + general
calendar-emoji guides) informed the picks; rendering-reliability is the harder
constraint and is the reason for the single-base-codepoint rule. Methodology
saved to gold/Research/Google Calendar beautification methodology.md
§"Content-icon layer".

CAVEAT (the single base codepoint is NOT always a single Python char): a handful
of glyphs (✈️ 🍽️ 🏋️ 🛋️) carry a trailing U+FE0F VARIATION SELECTOR to force
emoji (vs. text) presentation, so `len(glyph) == 2`. Calendar clients sometimes
add or drop that selector on round-trip, which would break a naive
`title.startswith(glyph)` strip and stack prefixes (e.g. "✈️ ✈ Flight"). Use
`strip_leading_icon()` below for all prefix stripping — it matches on the base
codepoint and tolerates a present-or-absent VS16.
"""
from __future__ import annotations

from typing import Iterable, Optional

# U+FE0F — emoji-presentation variation selector. Trails the base codepoint on
# the few non-text-default glyphs above; clients may add/drop it on round-trip.
VARIATION_SELECTOR = "️"

# ── Category layer — SOURCED FROM taxonomy.py (the single canonical color source) ──
# category -> (colorId, default emoji), the 11-color standard. Do NOT hardcode it
# here anymore; edit lib/taxonomy.py so calendar + gmail + /events stay in lock-step
# (the 2026-07-14 birthday=Banana-vs-Peacock drift is what motivated centralizing).
try:
    from taxonomy import CALENDAR_CATEGORY_STYLE as CATEGORY_STYLE
except ImportError:  # pragma: no cover — direct-CLI invocation (lib/ not on path)
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from taxonomy import CALENDAR_CATEGORY_STYLE as CATEGORY_STYLE

# ── Content-icon overrides (ordered; FIRST matching keyword wins) ──────────────
# Each entry: (keywords tuple, glyph). Keyword match is case-insensitive
# substring against the event title/text. Order matters — more specific first.
CONTENT_ICONS: list[tuple[tuple[str, ...], str]] = [
    # — Sports (a soccer match should never be a wine glass) —
    (("soccer", "world cup", "fifa", "premier league", "champions league"), "⚽"),
    (("baseball", "blue jays", "mlb", "yankees", "red sox", "ball game"), "⚾"),
    (("basketball", "nba", "march madness"), "🏀"),
    (("football", "nfl", "super bowl"), "🏈"),
    (("hockey", "nhl"), "🏒"),
    (("tennis",), "🎾"),
    (("golf",), "⛳"),
    (("ski", "snowboard"), "🎿"),
    (("swim", "pool"), "🏊"),
    (("gym", "workout", "exercise", "training session", "weightlift", "deadlift"), "🏋️"),
    (("yoga", "pilates", "barre", "stretch"), "🧘"),
    (("karate", "martial art", "taekwondo", "judo", "jiu-jitsu", "bjj"), "🥋"),
    # run-specific PHRASES only — no bare " run"/"5k"/"10k" (they hit "grocery
    # run", "raise 10k"; must stay in lockstep with calendar_maintenance fitness).
    (("marathon", "half marathon", "jog", "fun run", "morning run", "evening run",
      "trail run", "long run", "training run", "easy run", "go for a run",
      "5k run", "10k run", "5k race", "10k race", "parkrun", "terry fox"), "🏃"),
    (("hike", "hiking", "trail"), "🥾"),
    (("cycling", "bike ride", "spin class", "peloton"), "🚴"),
    # — Food / drink —
    (("bbq", "barbecue", "cookout", "grill"), "🍖"),
    (("brunch",), "🥐"),
    (("dinner", "supper"), "🍽️"),
    (("lunch",), "🥗"),
    (("coffee", "café", "cafe"), "☕"),
    (("drinks", "cocktail", "happy hour", "bar crawl", "wine", "beer"), "🍷"),
    # — Celebrations / culture —
    (("birthday", "bday", "b-day"), "🎂"),
    (("anniversary",), "💝"),
    (("wedding", "nuptial"), "💒"),
    (("baby shower", "christening", "baptism"), "🍼"),
    (("graduation", "commencement"), "🎓"),
    (("carnival", "fair", "festival", "fête", "fete"), "🎡"),
    (("concert", "gig", "live music", "symphony", "recital"), "🎵"),
    (("movie", "film", "cinema", "screening"), "🎬"),
    (("party", "celebration", "housewarming", "reunion"), "🎉"),
    # — Travel / logistics —
    (("flight", "airport", "boarding", "layover", "yyz", "sfo", "lax", "jfk", "sea-tac"), "✈️"),
    (("hotel", "airbnb", "check-in", "checkin"), "🏨"),
    (("road trip", "drive to", "driving to"), "🚗"),
    # — Health —
    (("dentist", "dental"), "🦷"),
    (("doctor", "clinic", "physical", "checkup", "ultrasound"), "🩺"),
    (("therapy", "counseling"), "🛋️"),
    # — Work —
    (("interview", "onsite", "recruiter"), "💼"),
    (("standup", "stand-up", "sync", "1:1", "one-on-one"), "👥"),
    (("deadline", "due:"), "⏰"),
    # — Kids / school —
    (("field day", "field trip"), "🚌"),
    (("parent-teacher", "ptc", "back to school"), "🎒"),
]

# Every glyph this module can emit — used by stylers to strip a stale icon
# prefix before re-applying (idempotent re-styling across re-categorization).
CONTENT_ICON_SET: frozenset[str] = frozenset(g for _, g in CONTENT_ICONS)
CATEGORY_EMOJI_SET: frozenset[str] = frozenset(e for _, e in CATEGORY_STYLE.values())
ALL_ICONS: frozenset[str] = CONTENT_ICON_SET | CATEGORY_EMOJI_SET


def content_icon(text: Optional[str]) -> Optional[str]:
    """Return a content-appropriate glyph for an event title/text, or None.

    Case-insensitive substring match against CONTENT_ICONS in declared order
    (first match wins). None means 'no content override' — the caller uses the
    category emoji. Pure function; safe to call on any string.
    """
    t = str(text or "").lower()
    if not t:
        return None
    for keywords, glyph in CONTENT_ICONS:
        if any(k in t for k in keywords):
            return glyph
    return None


def strip_leading_icon(
    text: Optional[str],
    icons: Iterable[str],
) -> tuple[str, Optional[str]]:
    """Strip a single leading glyph drawn from `icons` (+ trailing space).

    VS16-tolerant: matches on the BASE codepoint, so a glyph stored with or
    without its U+FE0F variation selector strips cleanly either way (clients
    round-trip the selector inconsistently — see module CAVEAT). Without this,
    a dropped selector would defeat `startswith(glyph)` and re-styling would
    stack prefixes ("✈️ ✈ Flight").

    Returns (stripped_text, matched_icon) — matched_icon is the icon AS DECLARED
    in `icons` (with its selector, if any), or None when nothing led. Each icon
    is one base codepoint, so no icon is a prefix of another and iteration order
    doesn't change the result. Pure function.
    """
    s = str(text or "").lstrip()
    if not s:
        return s, None
    for icon in icons:
        base = icon.replace(VARIATION_SELECTOR, "")
        if base and s.startswith(base):
            rest = s[len(base):]
            if rest.startswith(VARIATION_SELECTOR):  # original carried the VS16
                rest = rest[len(VARIATION_SELECTOR):]
            return rest.lstrip(), icon
    return s, None
