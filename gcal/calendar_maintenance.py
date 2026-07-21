#!/usr/bin/env python3
"""calendar_maintenance.py — periodic calendar styling + duplicate surfacing.

The `/calendar-silver --maintain` routine (scheduled weekly). Skill-created
events are styled at creation, but MANUALLY-added events are not — nothing
watches the calendar. This routine closes that gap: it scans future events and

  1. STYLES UNSTYLED events (gap-fill) — events with NO colorId get a content
     icon + category color. Events that already have a color are left ALONE
     (not recomputed): a color means a skill styled it with real task context,
     or a prior pass did — re-deriving weekly from generic title keywords would
     churn and downgrade those. An event whose title ALREADY leads with one of
     our glyphs gets the COLOR only — the leading emoji is an expressed user
     preference, so we add the missing color signal without stacking a second
     icon or overriding their choice. Purely additive + idempotent. Descriptions
     are never touched (no data loss). Recurring events are styled at the series
     MASTER.
  2. SURFACES duplicates for REVIEW — true same-calendar duplicates (≥2 distinct
     event ids, same title+date) are proposed; the routine NEVER auto-deletes
     (shared-calendar safety — a "dup" is often one family event mirrored onto
     primary via attendance, which must not be deleted).

`build_maintenance_plan(events_by_cal)` is PURE (no MCP) + tested. The CLI
fetches via the REST transport, prints the plan, and `--apply` dispatches the
RESTYLES only (deletions stay propose-only).

Categorization is GENERIC (no proper nouns — keeps the lib shareable + passes
spec-lint). It prefers an event's own provenance (extendedProperties.private
.binding_class from a skill that created it) over title keywords; unknown titles
fall back to `household` (neutral graphite). Personal-term routing belongs in
user-config, not here.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

# Central icon map (SSOT) — dual-mode import (works as gcal submodule + CLI).
try:
    import calendar_icons as _ci
    import taxonomy as _tax
except ImportError:  # pragma: no cover — direct invocation
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import calendar_icons as _ci
    import taxonomy as _tax
try:                                      # sibling module in gcal/
    from gcal import config as _gcfg
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config as _gcfg


# ── Generic title → category routing (NO proper nouns) ────────────────────────
# First keyword match wins; unknown → "household" (neutral graphite fallback).
# ORDER MATTERS (11-color standard, 2026-07-14): birthday + fitness sit BEFORE
# social/school so "Mom Bday" → birthday (not social) and "yoga class" → fitness
# (not school's "class "). Proper-noun exceptions (others' anniversaries, partner-
# partition reminders) live in the learned-rules file, not here.
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("birthday", ("birthday", "bday", "b-day")),
    ("partner", ("date night", "date-night", "anniversary", "couples",
                 "connection night", "marriage")),
    # NOTE: no bare " run"/"5k"/"10k" — those substrings hit "grocery run",
    # "school run", "airport run", "raise 10k" etc. Use exercise-specific run
    # PHRASES instead (substring matcher can't tell "morning run" from "beer run").
    ("fitness", ("gym", "workout", "exercise", "yoga", "pilates", "barre",
                 "karate", "martial art", "taekwondo", "judo", "jiu-jitsu",
                 "spin class", "peloton", "cycling", "bike ride", "hike",
                 "hiking", "marathon", "half marathon", "jog", "fun run",
                 "morning run", "evening run", "trail run", "long run",
                 "training run", "easy run", "go for a run", "5k run", "10k run",
                 "5k race", "10k race",
                 "parkrun", "terry fox", "swim", "weightlift", "deadlift",
                 "crossfit", "training session")),
    # health. Bare "dr"/"dr." is NOT a substring here (it hits "hundred",
    # "children", "address", "Andrew"); a "dr" + appointment-context match lives
    # in _DR_APPT_RE, checked at this position in category_for_title. The lexicon
    # below is unambiguous medical terms only (nothing that collides with common
    # words — e.g. "mri scan" not bare "mri", which is inside "Amrit").
    ("health", ("doctor", "dentist", "dental", "clinic", "physical", "therapy",
                "checkup", "check-up", "ultrasound", "vaccine", "bloodwork",
                "blood test", "surgery", "physio", "chiro", "optometrist",
                "optician", "eye exam", "pediatric", "paediatric", "obgyn",
                "ob/gyn", "gynecolog", "mammogram", "colonoscopy", "x-ray",
                "mri scan", "ct scan", "pharmacy", "prescription", "flu shot",
                "immuniz")),
    # NOTE: no bare "boarding" — that substring hits "onboarding", "snowboarding",
    # "skateboarding", "surfboarding" (all wrongly → travel). Use "boarding pass"
    # instead; genuine flights are already caught by flight/airport/layover +
    # _FLIGHT_ROUTE_RE, so the phrase loses no real itinerary.
    ("travel", ("flight", "airport", "trip", "vacation", "hotel", "airbnb",
                "layover", "boarding pass", "yyz", "sfo", "sea-tac")),
    # deep-work / focus blocks — BEFORE career so a work "HOLD"/focus block →
    # Basil, while ordinary meetings → Blueberry. (fitness is earlier, so
    # "[HOLD] karate" still routes to fitness, not here.) NOTE: bare "hold" is
    # matched by _HOLD_RE (word-boundary) in category_for_title, NOT here — as a
    # substring it hit "houseHOLD", "placeHOLDer", "stakeHOLDer", "shareHOLDer".
    ("deep_work", ("focus block", "deep work", "heads-down",
                   "heads down", "do not disturb", "dnd", "focus time")),
    # career/work-meetings. SPECIFIC terms only (generic work meetings are caught
    # by the work calendar's career DEFAULT, so no need for broad words like
    # "weekly"/"review" here — those would mis-hit personal events on the global run).
    ("career", ("interview", "recruiter", "onsite", "standup", "stand-up",
                "1:1", "1-1", "one-on-one", "sync", "sprint", "retro",
                "offsite", "all-hands", "all hands", "kickoff", " demo", "demo ",
                "catch-up", "catchup", "forecast", "sprint planning",
                "roadmap", "all-hands")),
    ("school", ("school", "practice", "field day", "field trip", "carnival",
                "recital", "teacher", "pta", "parent-teacher", "tutor",
                "class ", "grade ", "no school", "midwinter", "spring break",
                "winter break", "conferences", "graduation")),
    ("social", ("bbq", "barbecue", "dinner", "drinks", "party", "wedding",
                "game", "movie", "concert", "brunch", "lunch", "hosting",
                "reunion", "housewarming", "coffee", "festival", "fair",
                "gathering", "world cup", "soccer", "nba", "nfl", "mlb",
                "blue jays", "sangeet", "reception")),
    ("errands", ("errand", "grocery", "groceries", "pickup", "pick up",
                 "dropoff", "drop off", "dry clean")),
]


# A flight itinerary titled "SEA (Seattle) to SFO (San Francisco)" (a 3-letter
# airport code + a parenthetical, "to", then another code + parenthetical) is
# unmistakably travel — but the city/airport words themselves rarely hit the
# travel keyword list (only a handful of codes like yyz/sfo/sea-tac are in it),
# so identical flights routed inconsistently (SFO→travel by luck, ORD/DFW→default).
# Match the ROUTE SHAPE directly instead. ANCHORED to the start (after an optional
# leading emoji) so the WHOLE title is the route — a mid-sentence handoff like
# "Send RFP (draft) to CEO (review)" won't false-positive (it doesn't START with a
# bare 3-caps code). A "Flight: …"-prefixed title still routes via the "flight"
# keyword, so anchoring loses no real itinerary.
_FLIGHT_ROUTE_RE = re.compile(
    r"^\s*(?:[^\w\s(]+\s*)?[A-Z]{3}\b\s*\([^)]+\)\s+to\s+\b[A-Z]{3}\b\s*\([^)]+\)")

# Google auto-imports hotel/restaurant confirmation emails as calendar events with
# MACHINE-GENERATED titles: lodging → "Stay at <hotel>" (→ travel) and dining →
# "Reservation at <venue>" (→ social). No human keyword anticipates the venue name;
# the PREFIX is the signal (the travel list has "hotel"/"airbnb", but Google uses
# neither word). ANCHORED to the start (after an optional leading emoji) so a
# mid-title "kids stay at school" / "make a reservation at the DMV" can't
# false-positive. A campsite "Reservation at <state park>" lands in social — 1
# rare event, accepted rather than maintaining a park-name exception. Discovered
# via a --learn-unknowns census (2026-07-15): 7 lodging + 5 dining events sat in
# the household gray fallback because the prefix pattern was unrouted.
_LODGING_RE = re.compile(r"^\s*(?:[^\w\s]+\s*)?stay at\b", re.IGNORECASE)
_DINING_RESERVATION_RE = re.compile(
    r"^\s*(?:[^\w\s]+\s*)?reservation at\b", re.IGNORECASE)

# deep_work "HOLD" as a WHOLE WORD only — as a substring "hold" hit "household",
# "placeholder", "stakeholder", "shareholder" (all → deep_work/Basil wrongly).
# Checked at deep_work's position in the loop so precedence is preserved (fitness
# before → "[HOLD] karate" stays fitness; career after → a plain meeting wins).
_HOLD_RE = re.compile(r"\bhold\b", re.IGNORECASE)

# ── Work-calendar meeting refinement (work cal only — gated on career default) ──
# Generic 'career' meetings split into: broad / regular(=career). (interview is
# detected earlier, on BOTH calendars — see classify_category.)
_INTERVIEW_KW = ("interview", "candidate", "phone screen", "hiring")

# ── Description signals (GENERAL) ─────────────────────────────────────────────
# The classifier normally reads only the TITLE. A few event kinds carry their real
# signal in the DESCRIPTION instead (a candidate interview you run is titled just
# "Name - Role"; the giveaway is the ATS interview link in the body). Description
# routing is deliberately HIGH-PRECISION only — known ATS domains / exact
# boilerplate — because descriptions are noisy (signatures, agendas, links) and a
# loose keyword match would over-classify. First-match wins; add new (category,
# pattern) rows as high-confidence signals are found.
_DESC_CATEGORY_SIGNALS: list[tuple[str, "re.Pattern[str]"]] = [
    ("interview", re.compile(
        r"hire\.lever\.co/interviews|greenhouse\.io|ashbyhq\.com"
        r"|view resume and leave feedback", re.IGNORECASE)),
]


def category_from_description(description: str) -> Optional[str]:
    """First high-precision description signal → its category, else None."""
    desc = description or ""
    for cat, rx in _DESC_CATEGORY_SIGNALS:
        if rx.search(desc):
            return cat
    return None
# broadcast-style meetings that invite a group/room (so the attendee list is tiny
# even though the meeting is company-wide, e.g. "All Hands" shows 1 attendee) —
# caught by keyword since the count signal misses them.
_BROADCAST_KW = ("all-hands", "all hands", "town hall", "townhall",
                 "all-company", "all company")
_BROAD_MEETING_MIN_ATTENDEES = 17  # ≥ this many invitees ⇒ broad. Set to 17
# (2026-07-15) for a "broadcast vs participant" split: only company/customer-wide
# meetings (all-hands, forecast, customer update, strategic expansion — all ≥17 or
# caught by _BROADCAST_KW) read as broad; your ≤16-person TEAM meetings (standups,
# pod planning) stay regular, since you actively participate. Bumped 16→17 after a
# 90-day census: your real broadcasts sit at 17–70 attendees, while working pod
# meetings ("Schedule pod weekly planning") sit at exactly 16 — 17 keeps those blue.

# "dr"/"dr." → health ONLY in an appointment context (so "Dr. Seuss"/"Dr. Dre"
# honorific names DON'T mis-route to health, and bare-substring collisions like
# "hundred"/"children"/"address" are impossible). Covers "Dr appt", "Dr. visit",
# "Dr's office", and the reverse "visit/appt to (the) Dr". A bare "Dr Patel" with
# no appointment word is intentionally left unclassified (genuinely ambiguous).
_DR_APPT_RE = re.compile(
    # "Dr appt", "Dr. Patel appointment", "Dr visit" — appt word within a short
    # span after "dr" (a name may sit between). Bounded so it can't span a whole
    # sentence. "office" is NOT in this arm ("Dr. Seuss story — office party"
    # would leak) — only the explicit possessive "Dr's office" arm below.
    r"\bdr\.?\b[^,;\n]{0,25}?\b(?:appt|appointment|visit)\b"
    r"|\bdr\.?'?s\s+office\b"
    r"|\b(?:visit|appointment|appt)\s+to\s+(?:the\s+)?dr\.?\b",
    re.IGNORECASE)


def category_for_title(title: Optional[str]) -> str:
    raw = str(title or "")
    if _FLIGHT_ROUTE_RE.search(raw):
        return "travel"
    if _LODGING_RE.search(raw):          # "Stay at <hotel>" (Gmail-imported lodging)
        return "travel"
    if _DINING_RESERVATION_RE.search(raw):  # "Reservation at <venue>" (dining)
        return "social"
    t = raw.lower()
    for cat, kws in _CATEGORY_KEYWORDS:
        if any(k in t for k in kws):
            return cat
        if cat == "health" and _DR_APPT_RE.search(raw):
            return "health"
        if cat == "deep_work" and _HOLD_RE.search(raw):
            return "deep_work"
    return "household"


# Event types whose summary is system/source-owned — any title PATCH returns
# HTTP 400, only a colorId patch is accepted. Styled COLOR-ONLY so a title write
# is never attempted (which would 400 + churn every run).
_LOCKED_EVENT_TYPES = {"fromGmail", "birthday", "workingLocation",
                       "outOfOffice", "focusTime"}

# HARD RULE (2026-07-15, user directive): the work calendar is ALWAYS color-only —
# NEVER write an emoji into a title there, regardless of config or CLI flags.
# Colors are a private per-user overlay colleagues never see; an emoji would be a
# visible edit on colleague-organized meetings. Enforced in code (not just config)
# so it can't drift. FOREIGN (invited) events on PERSONAL calendars, by contrast,
# ARE emoji-styled: the emoji is a private local override the organizer never sees,
# and if they edit the event Google re-syncs it (wiping the emoji) — the next
# styling run simply re-adds it (self-healing). An event whose title write is
# actually REJECTED is remembered in the summary-locked cache and downgraded to
# color-only from then on (see _load_summary_locked / the apply-path fallback).
# Derived from config (`hard_color_only: true` rows) — the work calendar(s) that
# must NEVER get an emoji/title write. Falls back to empty if no config is present
# (a fresh clone has no work calendar). Replaces the old hardcoded personal set.
try:
    _HARD_COLOR_ONLY_CALENDARS = _gcfg.hard_color_only_calendars()
except Exception:  # pragma: no cover
    _HARD_COLOR_ONLY_CALENDARS = set()


def _strip_vs16(s: Optional[str]) -> str:
    """Drop U+FE0F selectors for a round-trip-tolerant title comparison. Clients
    add/drop the selector inconsistently ('🏋️'↔'🏋'); comparing raw would flag
    a phantom diff and re-write the event every run."""
    return str(s or "").replace(_ci.VARIATION_SELECTOR, "")


# ── Learned-rules layer (self-learning from calendar ENTRIES) ─────────────────
# The keyword tables above are the general standard; the learned-rules file is
# where per-title exceptions live — seeded by an LLM classification pass over the
# real corpus and grown as NEW event types appear (see --learn-unknowns). This is
# the "self-learning from the entries, not from manual color overrides" design:
# the file maps a NORMALIZED title (lowercased, our leading glyph stripped) to a
# category. Exact-normalized-title match wins over keywords.
import os as _os
_LEARNED_PATH = Path("~/.claude/state/calendar/learned_styles.json").expanduser()

# ── Per-calendar feature flags (skills/calendar-silver/feature-config.yml) ────
# The single place that declares WHICH styling features run on WHICH calendar:
# `color` (apply the category colorId), `emoji` (true → prepend the glyph;
# false / "color_only" → never touch the title), and `default_category` (the
# fallback for un-classifiable events). Wired here 2026-07-14 so the config is
# the REAL source of truth (previously only title_rewrite read it, and the
# maintain job's color/emoji were governed by CLI flags + heuristics — the file
# claimed otherwise). CLI flags still override per-run for the headless jobs.
_DEFAULT_FEATURE_CONFIG = Path(
    "~/.claude/skills/calendar-silver/feature-config.yml").expanduser()


_STYLE_KEYS = ("color", "emoji", "default_category")


def load_feature_config(path: Optional[Path] = None) -> dict[str, dict]:
    """Return {calendar_id: {color, emoji, default_category}} with
    `calendar_defaults` merged under each calendar, plus a "__defaults__" entry
    for calendars not explicitly listed.

    Source of truth is the unified config (`taxonomy.CONFIG`, i.e.
    calendar-colors.default.yml + the user overlay) via gcal/config. Only STYLING
    keys are surfaced here — `title_rewrite` is owned by the title-rewrite
    subsystem, not the styling path. Best-effort: `{__defaults__: builtins}` if
    the config is empty/unreadable, so the caller falls back to CLI flags rather
    than crashing the headless job. `path` is accepted for signature compat and
    ignored (the old per-file feature-config.yml is superseded)."""
    builtins = {"color": True, "emoji": True}
    try:
        cfg = _tax.CONFIG or {}
        defaults = {**builtins, **(cfg.get("calendar_defaults") or {})}
        out: dict[str, dict] = {"__defaults__": defaults}
        for row in _gcfg.calendars():
            cid = row.get("id")
            if not cid:
                continue
            style = {k: row[k] for k in _STYLE_KEYS if k in row}
            out[str(cid)] = {**defaults, **style}
        return out
    except Exception:
        return {"__defaults__": builtins}


def _cal_style_settings(cal_id: str, feature_config: Optional[dict],
                        *, emoji_off: bool, default_category: str) -> dict:
    """Resolve the effective per-calendar styling knobs. Precedence:
    explicit CLI override (emoji_off) > feature-config entry > built-ins.
    Returns {color_only: bool, color_enabled: bool, default_category: str}.
    `emoji` is True → prepend glyph; anything else ("color_only"/False) → title
    left untouched. A CLI --emoji-off forces color_only regardless of config."""
    fc = feature_config or {}
    cfg = fc.get(cal_id) or fc.get("__defaults__") or {}
    emoji_val = cfg.get("emoji", True)
    color_only = emoji_off or (emoji_val is not True)
    return {
        "color_only": color_only,
        "color_enabled": bool(cfg.get("color", True)),
        "default_category": cfg.get("default_category") or default_category,
    }


def _normalize_title(summary: str) -> str:
    """Lowercased title with any leading OWNED glyph stripped — the join key for
    the learned-rules file (so '🏋️ Exercise' and 'exercise' hash the same)."""
    stripped, _ = _ci.strip_leading_icon(str(summary or ""), _ci.ALL_ICONS)
    return stripped.strip().lower()


def load_learned(path: Optional[Path] = None) -> dict[str, str]:
    """Load {normalized_title: category} learned rules, or {} if absent/bad."""
    p = path or _LEARNED_PATH
    try:
        data = json.loads(Path(p).expanduser().read_text())
        rules = data.get("rules", data) if isinstance(data, dict) else {}
        out: dict[str, str] = {}
        for k, v in rules.items():
            cat = v.get("category") if isinstance(v, dict) else v
            if cat in _ci.CATEGORY_STYLE:
                out[str(k).strip().lower()] = cat
        return out
    except Exception:
        return {}


def load_learned_icons(path: Optional[Path] = None) -> dict[str, str]:
    """Load {normalized_title: icon} overrides from learned rules that carry an
    explicit `icon` — an escape hatch for when the CONTENT icon disagrees with a
    chosen category color and you'd rather the glyph match the color. No rule sets
    one today (a child's Karate keeps its activity glyph 🥋 even on the School color,
    since the color already signals the partition) — the hook stays for future
    cross-category cases. {} if absent/bad."""
    p = path or _LEARNED_PATH
    try:
        data = json.loads(Path(p).expanduser().read_text())
        rules = data.get("rules", data) if isinstance(data, dict) else {}
        out: dict[str, str] = {}
        for k, v in rules.items():
            if isinstance(v, dict) and v.get("icon"):
                out[str(k).strip().lower()] = str(v["icon"])
        return out
    except Exception:
        return {}


# Existing-glyph → category hint. A leading emoji already on an event encodes
# prior intent (a skill's 🎒, a manual 🩺); when keyword/learned can't classify,
# honor it rather than downgrading to household. Covers category default emojis +
# the unambiguous content icons.
_EMOJI_TO_CATEGORY: dict[str, str] = {
    # category defaults
    "✅": "errands", "🏋️": "fitness", "💜": "partner", "🍷": "social",
    "🎒": "school", "✈️": "travel", "🎂": "birthday", "🏠": "household",
    "💼": "career", "🟢": "deep_work", "🩺": "health",
    # unambiguous content icons
    "🎓": "school", "🚌": "school", "🦷": "health", "🛋️": "health",
    "🧘": "fitness", "🥋": "fitness", "🏃": "fitness", "🚴": "fitness", "🥾": "fitness",
    # occasion glyphs (/events): 💍 anniversary → partner=Grape(3); 🙏 memorial →
    # household=Graphite(8) — chosen so the color equals taxonomy.OCCASION_COLOR,
    # so the daily job AGREES with /events instead of recoloring its events.
    "💍": "partner", "🙏": "household",
    "💝": "partner", "💒": "social", "🎉": "social", "🎵": "social", "🎬": "social",
    "🍖": "social", "🍽️": "social", "☕": "social", "🥐": "social", "🥗": "social",
    "⚽": "social", "⚾": "social", "🏀": "social", "🏈": "social", "🏒": "social",
    "🎾": "social", "⛳": "social", "🎿": "social", "🏊": "social", "🏨": "travel",
}


def _leading_category_hint(summary: str) -> Optional[str]:
    """Category implied by a leading emoji already on the title, or None.

    Strips against ALL_ICONS ∪ the hint keys — so occasion glyphs like 💍/🙏 that
    aren't in the calendar icon set (they live in /events) are still recognized
    and mapped (💍→partner/Grape, 🙏→household/Graphite), letting the daily job
    AGREE with /events' colors instead of recoloring them to household."""
    icons = _ci.ALL_ICONS | set(_EMOJI_TO_CATEGORY)
    _stripped, glyph = _ci.strip_leading_icon(str(summary or ""), icons)
    if glyph is None:
        return None
    return _EMOJI_TO_CATEGORY.get(glyph) or _EMOJI_TO_CATEGORY.get(
        glyph.replace(_ci.VARIATION_SELECTOR, ""))


def classify_category(summary: str, learned: dict[str, str],
                      priv: Optional[dict] = None,
                      default: str = "household",
                      description: str = "") -> str:
    """Resolve an event to a style category. Precedence (matches the code below):
       learned (exact title) > skill provenance > interview/description signal >
       keyword table > existing-emoji hint > household fallback.
    Learned wins so curated per-title exceptions (others' anniversaries → social,
    partner-partition reminders → partner) override everything. Provenance beats the
    keyword table so a skill that categorized WITH context isn't overridden by a
    generic keyword (a /health "Coffee with Dr. Smith" stays health, not social-
    on-"coffee"). The interview/description signal (title keyword OR an ATS link in
    the body) then wins over the generic keyword table so a candidate interview
    titled just "Name - Role" is caught on BOTH calendars. The existing-emoji hint
    keeps a full re-style from DOWNGRADING an already-correct event (🎒 school →
    household) when its title has no keyword."""
    norm = _normalize_title(summary)
    if norm in learned:
        return learned[norm]
    # Skill provenance beats generic keywords: a skill categorized WITH context
    # (a /health "Coffee with Dr. Smith" must not route to social on "coffee").
    if priv:
        bc = priv.get("binding_class")
        if bc in _ci.CATEGORY_STYLE:
            return bc
    # interview — title keyword OR a high-precision ATS link in the DESCRIPTION.
    # Runs on every calendar (the candidate interviews you run are titled just
    # "Name - Role" with the Lever link in the body; your own interviews carry an
    # "interview" title keyword). Both land on the same interview category/color.
    if any(k in summary.lower() for k in _INTERVIEW_KW):
        return "interview"
    desc_cat = category_from_description(description)
    if desc_cat:
        return desc_cat
    cat = category_for_title(summary)
    if cat != "household":
        return cat
    hint = _leading_category_hint(summary)
    if hint:
        return hint
    return default  # per-calendar fallback (household normally; career on work cal)


def _private(event: dict[str, Any]) -> dict[str, Any]:
    ep = event.get("extendedProperties") or {}
    p = ep.get("private") if isinstance(ep, dict) else {}
    return p if isinstance(p, dict) else {}


def _strip_owned_prefix(summary: str) -> str:
    """Strip a single leading known-icon — used ONLY to NORMALIZE titles for
    dup-matching (so '🍖 BBQ' and 'BBQ' compare equal). NOT used to build the
    restyled title (that would clobber a user's own leading emoji — see
    `_prefix_with_icon`). VS16-tolerant (calendar_icons.strip_leading_icon)."""
    stripped, _ = _ci.strip_leading_icon(summary, _ci.ALL_ICONS)
    return stripped


def _prefix_with_icon(summary: str, icon: str) -> str:
    """Prepend `icon`, stripping only a leading copy of THAT SAME icon (so
    re-runs don't double it). A user's *different* leading emoji is preserved —
    we never silently rewrite user-authored titles, only prepend category
    context. Gap-fill runs only on un-colored events (never styled by us), so
    there is no prior owned-icon to strip beyond an exact repeat. The same-icon
    strip is VS16-tolerant, so a selector dropped on round-trip can't double the
    glyph ("✈️ ✈ Flight")."""
    s = str(summary or "").lstrip()
    if icon:
        s, _ = _ci.strip_leading_icon(s, (icon,))
    return f"{icon} {s}".strip()


def _desired_style(event: dict[str, Any], learned: dict[str, str],
                   default_category: str = "household",
                   learned_icons: Optional[dict[str, str]] = None) -> tuple[str, str, str]:
    """(category, colorId, icon) for an event via the learned/keyword classifier.
    `default_category` overrides the fallback (e.g. 'career' on the work calendar,
    so generic work meetings → Blueberry instead of household). `learned_icons`
    is the {normalized_title: icon} override map — when a title has an entry, that
    glyph wins over the content icon (used where the content icon disagrees with
    the category color, e.g. karate → School color but 🥋 content glyph → 🎒)."""
    summ = event.get("summary") or ""
    # Working-location events (Home / Office / a city on the work calendar) are
    # AMBIENT status, not a trip — Graphite (working_location sub-type) so they
    # recede behind the Blueberry meeting wall instead of shouting in travel's
    # Tangerine (flights keep Tangerine). eventType is the reliable signal (the
    # title is a bare place name like "Home" no keyword would catch). Color-only
    # in practice (workingLocation is a locked eventType) → emoji never shown.
    if event.get("eventType") == "workingLocation":
        return ("working_location", _tax.color_id("working_location"),
                _tax.emoji("working_location"))
    category = classify_category(summ, learned, _private(event),
                                 default=default_category,
                                 description=event.get("description") or "")
    # Work-calendar meeting refinement: split the generic 'career' meeting bucket
    # into broad / regular. Gated on the career DEFAULT (i.e. this is the work
    # calendar) so a personal event that merely hits a work keyword — or a 100-guest
    # wedding — is never dragged in. Only refines 'career'; interview is already
    # resolved in classify_category (title/description signal), HOLD (deep_work/red)
    # and flights (travel) keep their colors.
    if default_category == "career" and category == "career":
        low = summ.lower()
        n_attendees = len(event.get("attendees") or [])
        if (n_attendees >= _BROAD_MEETING_MIN_ATTENDEES
                or any(k in low for k in _BROADCAST_KW)):
            category = "broad_meeting"
        else:
            category = "regular_meeting"   # your 1:1s / small sessions → Peacock (clean blue)
    # Sub-types (interview/broad_meeting/working_location) live in taxonomy, not
    # the 11-category CATEGORY_STYLE — fall through to the taxonomy lookup.
    if category in _ci.CATEGORY_STYLE:
        color, cat_emoji = _ci.CATEGORY_STYLE[category]
    else:
        color, cat_emoji = _tax.color_id(category), _tax.emoji(category)
    icon_override = (learned_icons or {}).get(_normalize_title(summ))
    icon = icon_override or _ci.content_icon(summ) or cat_emoji
    # Enforce rules.forbidden_on_work ("no purple on work"). On the work calendar
    # (career default) a keyword hit can yield a purple color — birthday→Lavender(1),
    # partner→Grape(3) — that the career meeting-refinement above never sees (it only
    # re-buckets the career fallback). Recede those to Graphite (broad_meeting) so the
    # declared invariant actually holds. Personal calendars keep purple: their
    # default_category is 'household', not 'career', so this branch is skipped.
    if default_category == "career":
        forbidden = set(_tax.RULES.get("forbidden_on_work") or [])
        if color in forbidden:
            category, color, icon = ("broad_meeting",
                                     _tax.color_id("broad_meeting"),
                                     _tax.emoji("broad_meeting"))
    return category, color, icon


def _restyle_summary(summary: str, icon: str) -> str:
    """Full-restyle title: strip a leading glyph WE own (replacing a stale/wrong
    category emoji) and prepend the desired one. A foreign glyph the user added
    (e.g. 🏆) is preserved after ours (→ '⚽ 🏆 World Cup FINAL'). Idempotent."""
    stripped, _ = _ci.strip_leading_icon(str(summary or ""), _ci.ALL_ICONS)
    s = stripped.strip()
    return f"{icon} {s}".strip() if icon else s


def _event_start_date(event: dict[str, Any]) -> str:
    st = event.get("start") or {}
    return str(st.get("dateTime") or st.get("date") or "")[:10]


def _event_start_key(event: dict[str, Any]) -> str:
    """FULL start for dup-keying: the dateTime for timed events, the date for
    all-day. Truncating to date (as display does) would flag two genuinely
    distinct same-title meetings on one day (9am 1:1 vs 3pm 1:1) as a 'dup'."""
    st = event.get("start") or {}
    return str(st.get("dateTime") or st.get("date") or "")


def _is_recurring(event: dict[str, Any]) -> bool:
    return bool(event.get("recurringEventId") or event.get("recurrence"))


def build_maintenance_plan(
    events_by_cal: dict[str, list[dict[str, Any]]],
    *,
    skip_ids: Optional[set] = None,
    learned: Optional[dict[str, str]] = None,
    restyle_all: bool = False,
    emoji_off: bool = False,
    default_category: str = "household",
    feature_config: Optional[dict] = None,
    learned_icons: Optional[dict[str, str]] = None,
    summary_locked_ids: Optional[set] = None,
) -> dict[str, Any]:
    """Pure plan: styling + duplicate surfacing. No MCP, no mutation.

    `feature_config` is the {cal_id: {color, emoji, default_category}} map from
    load_feature_config(). When provided, each calendar's color-only/default-
    category behavior comes from its config entry (so the events calendar is
    color-only by DECLARATION, not by relying on a description marker). When
    None, the global `emoji_off`/`default_category` apply to every calendar
    (back-compat: the tests + any caller that hasn't loaded the config). A CLI
    --emoji-off still force-overrides to color-only regardless of config.

    `skip_ids` is a negative cache of event ids that permanently failed to
    patch (external-owned / 403 / 404) — NOT re-proposed, so the daily run
    doesn't churn the same un-patchable events forever.

    `learned` is the {normalized_title: category} self-learning map (loaded from
    disk when None).

    `restyle_all` (2026-07-14): FULL re-style mode — recompute the desired color
    AND emoji for EVERY event and emit a change wherever the live style differs
    from the standard, OVERRIDING prior colors (a skill's, a manual tweak, an old
    pass). Idempotent: an event already matching the standard is skipped, so
    re-runs don't churn. When False, the original additive gap-fill (only touch
    un-colored events) is used.
    """
    skip = skip_ids or set()
    summary_locked = summary_locked_ids or set()
    learned = learned if learned is not None else load_learned()
    learned_icons = learned_icons if learned_icons is not None else load_learned_icons()
    restyle: list[dict[str, Any]] = []
    by_cal_title_start: dict[tuple, set] = defaultdict(set)  # true-dup detection
    dup_meta: dict[tuple, str] = {}                          # key -> display date
    by_id_cals: dict[str, set] = defaultdict(set)            # cross-cal mirror detection
    id_meta: dict[str, dict] = {}
    scanned = 0
    seen_master: set[tuple] = set()
    master_rows: dict[tuple, dict[str, Any]] = {}  # (cal,master) -> its restyle row

    for cal_id, events in (events_by_cal or {}).items():
        # Resolve this calendar's styling knobs once (config entry, or the
        # global CLI flags when no config is supplied).
        cs = _cal_style_settings(cal_id, feature_config, emoji_off=emoji_off,
                                 default_category=default_category)
        cal_color_only = cs["color_only"]
        cal_default_cat = cs["default_category"]
        cal_color_enabled = cs["color_enabled"]
        # HARD RULE: the work calendar is color-only, always — no emoji, ever,
        # regardless of what the config/flags say (can't drift out of code).
        if cal_id in _HARD_COLOR_ONLY_CALENDARS:
            cal_color_only = True
        for e in events:
            if not isinstance(e, dict):
                continue
            scanned += 1
            summ = e.get("summary") or ""
            date = _event_start_date(e)
            eid = str(e.get("id") or "")
            norm = _strip_owned_prefix(summ).lower()
            # Dup detection: keyed on FULL start, and only over STANDALONE events
            # — recurring instances/masters legitimately repeat a title and must
            # never be flagged as deletable dups.
            if norm and not _is_recurring(e):
                key = (cal_id, norm, _event_start_key(e))
                by_cal_title_start[key].add(eid)
                dup_meta[key] = date
            by_id_cals[eid].add(cal_id)
            id_meta[eid] = {"summary": summ, "date": date}

            if not summ.strip():
                continue  # can't style an empty title
            # Negative cache — known-unpatchable, don't churn. Match the recurring
            # MASTER id too: a series' per-run INSTANCE ids differ, so checking only
            # the instance id let recurring invited events re-fail forever.
            rec_id = e.get("recurringEventId")
            if eid in skip or (rec_id and str(rec_id) in skip):
                continue
            if not cal_color_enabled:
                continue  # calendar opted out of coloring entirely (config color:false)

            # Recurring: restyle the MASTER once. But keep every in-window
            # INSTANCE id too — for a series you only ATTEND (organizer ≠ you),
            # the master/`_R…` id 404s on PATCH; the apply path falls back to
            # patching those instance ids directly (each one IS patchable). For a
            # series you OWN, the master patch succeeds and the instances are
            # never needed.
            recurring_event_id = e.get("recurringEventId")
            target_id = recurring_event_id or eid
            if recurring_event_id:
                if (cal_id, target_id) in seen_master:
                    # already decided this master — just collect the extra
                    # instance id onto its row (if one was emitted).
                    prior = master_rows.get((cal_id, target_id))
                    if prior is not None:
                        prior["instance_ids"].append(eid)
                    continue
                seen_master.add((cal_id, target_id))

            _cat, color, icon = _desired_style(e, learned, cal_default_cat,
                                               learned_icons)
            # Summary-LOCKED event types: any title change returns HTTP 400, but
            # a colorId-only patch is accepted. `fromGmail` (email-extracted),
            # `birthday` (Google-Contacts synced), and the status/location types
            # all lock their summary — so they are ALWAYS color-only. Retrying a
            # title write on them would 400 every run (churn); color-only avoids it.
            is_locked_type = e.get("eventType") in _LOCKED_EVENT_TYPES
            # Skill-MANAGED events (e.g. /events occasions: "Managed by /events" in
            # the description) already carry an authored title + a taxonomy color.
            # When the Events calendar is in scope, style them COLOR-ONLY so the
            # generic classifier never rewrites their titles (which would stack a
            # 💝 in front of an authored 💍, etc.). The color already matches the
            # shared taxonomy → idempotent.
            is_managed = "managed by /" in (e.get("description") or "").lower()
            # Learned summary-lock: a foreign/invited event whose title write Google
            # already REJECTED (recorded in the summary-locked cache, keyed on the
            # stable/master id) → color-only from now on. Everything else — including
            # foreign invites we haven't tried — attempts the emoji: on a personal
            # calendar it's a private local override the organizer never sees, and if
            # the write is rejected the apply path records it here so the NEXT run
            # downgrades it to color-only (one failed attempt, then quiet).
            is_summary_locked = target_id in summary_locked or eid in summary_locked

            if restyle_all:
                # FULL RE-STYLE: recompute color + emoji for EVERY event and
                # override whatever's live. Replace a stale/wrong OWNED emoji
                # (foreign glyphs the user added are preserved). Skip when
                # already correct → idempotent, no churn.
                color_only = (is_locked_type or is_managed or is_summary_locked
                              or cal_color_only)
                new_summary = summ if color_only else _restyle_summary(summ, icon)
                color_matches = e.get("colorId") == color
                # VS16-tolerant: a client that drops/re-adds the U+FE0F selector
                # on round-trip ("🏋 Exercise" vs "🏋️ Exercise") must NOT read as
                # drift, or fitness/travel/dinner events re-write every run.
                summary_matches = color_only or (_strip_vs16(new_summary)
                                                  == _strip_vs16(summ))
                if color_matches and summary_matches:
                    continue
            else:
                # GAP-FILL ONLY: style genuinely UNSTYLED events; leave colored
                # ones (a skill/manual pass owns those). Additive + idempotent.
                if e.get("colorId"):
                    continue
                # COLOR-ONLY also when the title already leads with one of our
                # glyphs (an expressed preference — don't stack a 2nd icon).
                _stripped, leading_icon = _ci.strip_leading_icon(summ, _ci.ALL_ICONS)
                color_only = (is_locked_type or is_summary_locked
                              or leading_icon is not None or cal_color_only)
                new_summary = summ if color_only else _prefix_with_icon(summ, icon)

            row = {
                "calendar_id": cal_id,
                "event_id": target_id,
                "current_summary": summ,
                "new_summary": new_summary,
                "color_id": color,
                "category": _cat,
                "color_only": color_only,
                # in-window instance ids for the invited-recurring apply fallback;
                # empty for standalone events (target_id is itself patchable).
                "instance_ids": [eid] if recurring_event_id else [],
            }
            if recurring_event_id:
                master_rows[(cal_id, target_id)] = row
            restyle.append(row)

    # True same-calendar duplicates: ≥2 DISTINCT ids under one (cal,title,start).
    dup_candidates = [
        {"calendar_id": cal, "title": norm, "date": dup_meta[(cal, norm, start)],
         "event_ids": sorted(ids)}
        for (cal, norm, start), ids in by_cal_title_start.items() if len(ids) > 1
    ]
    # Cross-calendar mirrors: one id on >1 calendar (informational — NOT a dup;
    # usually a family event you attend, mirrored onto primary). Never deleted.
    mirror_candidates = [
        {"event_id": eid, "calendars": sorted(cals),
         "summary": id_meta[eid]["summary"], "date": id_meta[eid]["date"]}
        for eid, cals in by_id_cals.items() if len(cals) > 1
    ]
    return {
        "scanned": scanned,
        "restyle": restyle,
        "dup_candidates": dup_candidates,        # propose-only
        "mirror_candidates": mirror_candidates,  # informational
        "totals": {
            "scanned": scanned,
            "restyle": len(restyle),
            "dup_candidates": len(dup_candidates),
            "mirror_candidates": len(mirror_candidates),
        },
    }


# ── /inbox handoff — surface TRUE duplicates for a user delete-decision ───────
# The styling half of this routine is autonomous (gap-fill, no data loss), so it
# runs headless via launchd with no Claude session. But a dup is a DECISION
# (delete which copy?) that needs a human — and deletions touch shared calendars.
# Rather than block the headless run, register a `doc_review` obligation so the
# next interactive /inbox session surfaces it. Idempotent: a dup already in
# /inbox (any state, incl. terminal) is not re-registered on the next daily run.

CHANNEL_CALENDAR = "calendar"
KIND_DOC_REVIEW = "doc_review"


def _inbox_api():  # pragma: no cover — thin lazy importer (mirrors rsvp_register)
    """Lazy import of lib/inbox/api so this module imports without inbox."""
    try:
        from inbox import api as inbox_api  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from inbox import api as inbox_api  # type: ignore
    return inbox_api


def _dup_token(d: dict[str, Any]) -> str:
    return f"gcal-dup:{d.get('calendar_id')}:{d.get('title')}:{d.get('date')}"


def register_dup_obligations(dup_candidates: list[dict[str, Any]], *,
                             source_skill: str = "calendar-silver",
                             state_root: Optional[Path] = None) -> list[str]:
    """Register a doc_review /inbox row per TRUE duplicate (propose-only).

    Idempotent against source_event tokens already in /inbox. Returns the list
    of newly-created obligation_ids. NEVER registers mirror_candidates (those
    are informational cross-calendar copies, not deletable dups). Best-effort:
    if /inbox is unavailable, returns [] without raising (styling still ran).
    """
    if not dup_candidates:
        return []
    from datetime import datetime, timezone
    try:
        inbox = _inbox_api()
        rows = inbox.query(channel=CHANNEL_CALENDAR, kind=KIND_DOC_REVIEW,
                           discovered_by=source_skill, include_terminal=True,
                           state_root=state_root)
        already = {str(r.get("source_event") or "") for r in rows}
        received_at = datetime.now(timezone.utc).isoformat()
        new_ids: list[str] = []
        for d in dup_candidates:
            token = _dup_token(d)
            if token in already:
                continue
            n = len(d.get("event_ids") or [])
            obligation_id = inbox.register(
                kind=KIND_DOC_REVIEW,
                source_event=token,
                counterparty="self",
                counterparty_partition="self",
                channel=CHANNEL_CALENDAR,
                discovered_by=source_skill,
                received_at=received_at,
                topic_class="logistics",
                body_anchor=f"Possible duplicate calendar event: "
                            f"{d.get('title')!r} ×{n} on {d.get('date')} — "
                            f"review + delete a copy (NEVER auto-deleted).",
                state_root=state_root,
            )
            new_ids.append(obligation_id)
            already.add(token)
        return new_ids
    except Exception as e:  # pragma: no cover — best-effort handoff
        print(f"  WARN: dup /inbox handoff failed: {e}", file=sys.stderr)
        return []


# ── CLI ───────────────────────────────────────────────────────────────────────

_DEFAULT_TOKEN = "~/.claude/state/calendar/oauth/token.json"
_DEFAULT_SKIP_CACHE = "~/.claude/state/calendar/maint_skip.json"
_DEFAULT_SUMMARY_LOCKED_CACHE = "~/.claude/state/calendar/maint_summary_locked.json"


def _is_auth_error(ex: Exception) -> bool:
    """Token-wide failure (expired/revoked/insufficient scope) — fatal for a
    LIST. Distinguished from a per-event patch rejection so the headless run
    SIGNALS failure instead of silently producing an empty (looks-healthy) plan.
    """
    if type(ex).__name__ == "CredentialsError":
        return True
    return getattr(ex, "status", None) in (401, 403)


def _load_skip(path: str) -> set:
    """Negative cache of permanently-unpatchable event ids (so the daily run
    doesn't re-attempt + re-fail the same external-owned events forever)."""
    try:
        p = Path(path).expanduser()
        if p.exists():
            data = json.loads(p.read_text())
            return {str(x.get("event_id")) for x in data.get("skip", [])
                    if x.get("event_id")}
    except Exception:  # pragma: no cover — cache is best-effort
        pass
    return set()


def _save_skip(path: str, existing_ids: set, new_failures: list[dict]) -> None:
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        merged = {eid: {"event_id": eid} for eid in existing_ids}
        for f in new_failures:
            merged[f["event_id"]] = {"event_id": f["event_id"],
                                     "reason": f.get("reason", "")}
        p.write_text(json.dumps({"skip": list(merged.values())}, indent=2))
    except Exception as e:  # pragma: no cover
        print(f"  WARN: could not persist skip cache: {e}", file=sys.stderr)


def _load_summary_locked(path: str) -> set:
    """Ids of events whose TITLE write Google rejected (so we style them COLOR-ONLY
    from now on — the color still applies + updates; only the emoji is skipped).

    Distinct from the full skip cache: a summary-locked event is still processed
    every run for its COLOR (so a category/color correction still lands), we just
    never re-attempt the doomed title write (no churn, no failed-apply). Keyed on
    the STABLE id (the recurring MASTER id for a series, else the event id) so a
    recurring invited series — whose per-run instance ids differ — is matched
    reliably (the bug that made "Weekly OKR Status" churn forever)."""
    try:
        p = Path(path).expanduser()
        if p.exists():
            data = json.loads(p.read_text())
            return {str(x.get("event_id")) for x in data.get("locked", [])
                    if x.get("event_id")}
    except Exception:  # pragma: no cover — cache is best-effort
        pass
    return set()


def _save_summary_locked(path: str, existing_ids: set,
                         new_locked: list[dict]) -> None:
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        merged = {eid: {"event_id": eid} for eid in existing_ids}
        for f in new_locked:
            merged[f["event_id"]] = {"event_id": f["event_id"],
                                     "reason": f.get("reason", "")}
        p.write_text(json.dumps({"locked": list(merged.values())}, indent=2))
    except Exception as e:  # pragma: no cover
        print(f"  WARN: could not persist summary-locked cache: {e}", file=sys.stderr)


def _alert_failure(reason: str, *, state_root=None) -> None:
    """Surface a headless-run failure where a human will see it: stderr (the
    cron log) AND a single /inbox doc_review obligation (idempotent on OPEN
    rows — re-fires if it breaks again after being resolved). Without this, an
    expired token would silently no-op the daily job for days."""
    print(f"FAILURE: calendar maintenance — {reason}", file=sys.stderr)
    try:
        from datetime import datetime, timezone
        inbox = _inbox_api()
        token = "gcal-maint-failure"
        open_rows = inbox.query(channel=CHANNEL_CALENDAR, kind=KIND_DOC_REVIEW,
                                discovered_by="calendar-silver",
                                include_terminal=False, state_root=state_root)
        if any(str(r.get("source_event")) == token for r in open_rows):
            return  # an open alert already exists — don't pile up daily
        inbox.register(
            kind=KIND_DOC_REVIEW, source_event=token, counterparty="self",
            counterparty_partition="self", channel=CHANNEL_CALENDAR,
            discovered_by="calendar-silver",
            received_at=datetime.now(timezone.utc).isoformat(),
            topic_class="logistics",
            body_anchor=f"Daily calendar maintenance FAILED: {reason}. "
                        f"Log: ~/.claude/cache/cron-logs/calendar-maintain-daily.log. "
                        f"Token may need re-auth: python3 ~/.claude/lib/gcal/authorize.py.",
            state_root=state_root)
    except Exception as e:  # pragma: no cover
        print(f"  WARN: failure /inbox alert failed: {e}", file=sys.stderr)


def _fetch(invoker, calendar_ids: list[str], time_min: str, time_max: str) -> dict[str, list]:
    """Paginated fetch. Auth-class errors RE-RAISE (fatal — must signal); a
    single calendar's non-auth error degrades that calendar to [] and continues.
    """
    out: dict[str, list] = {}
    for cid in calendar_ids:
        items: list = []
        page_token = None
        pages = 0
        while True:
            params = {"calendar_id": cid, "time_min": time_min,
                      "time_max": time_max, "max_results": 250}
            if page_token:
                params["page_token"] = page_token
            try:
                r = invoker("mcp__claude_ai_Google_Calendar__list_events", params)
            except Exception as e:
                if _is_auth_error(e):
                    raise  # token expired/revoked/scope — don't mask as empty
                print(f"  WARN: list {cid} failed: {e}", file=sys.stderr)
                break
            items.extend(r.get("items", []))
            page_token = r.get("nextPageToken")
            pages += 1
            if not page_token:
                break
            if pages >= 40:  # 40 × 250 = 10k events — runaway guard, not silent
                print(f"  WARN: {cid} exceeded 10k events in window; truncated.",
                      file=sys.stderr)
                break
        out[cid] = items
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Style unstyled future calendar events + surface duplicates "
                    "(propose-only). /calendar-silver --maintain.")
    p.add_argument("skill", nargs="?", default=None,
                   help="Chassis-helper positional (e.g. 'calendar-silver'). Optional.")
    p.add_argument("--apply", action="store_true",
                   help="Dispatch the RESTYLE updates via REST. Deletions are "
                        "NEVER applied — dup_candidates are propose-only.")
    p.add_argument("--restyle-all", action="store_true",
                   help="FULL re-style: recompute color+emoji for EVERY event "
                        "and override to the standard (not just gap-fill "
                        "un-colored). Idempotent; the daily job uses this.")
    p.add_argument("--emoji-off", action="store_true",
                   help="COLOR-ONLY: never change titles/emoji (colorId only). For "
                        "the work calendar — colors are a safe per-user overlay.")
    p.add_argument("--default-category", default="household",
                   help="Fallback category for un-classifiable events (default "
                        "household; use 'career' for the work calendar).")
    p.add_argument("--learn-unknowns", action="store_true",
                   help="Self-learning surface: print the distinct event titles "
                        "that fall through to the household FALLBACK (no learned "
                        "rule, keyword, or emoji hint). A Claude-driven run "
                        "classifies these into learned_styles.json.")
    p.add_argument("--calendars", default="primary",
                   help="Comma-separated calendar IDs to scan (default: primary). "
                        "Add the family/shared calendar id to include it.")
    p.add_argument("--window-days", type=int, default=90)
    p.add_argument("--time-min", default=None, help="ISO; default now.")
    p.add_argument("--token", default=_DEFAULT_TOKEN)
    p.add_argument("--skip-cache", default=_DEFAULT_SKIP_CACHE,
                   help="Negative-cache path for permanently-unpatchable events.")
    p.add_argument("--summary-locked-cache", default=_DEFAULT_SUMMARY_LOCKED_CACHE,
                   help="Cache path for events whose TITLE write is rejected → "
                        "color-only from then on (color still applies/updates).")
    p.add_argument("--feature-config", default=str(_DEFAULT_FEATURE_CONFIG),
                   help="Per-calendar color/emoji/default_category flags "
                        "(feature-config.yml). Governs color-only per calendar; "
                        "--emoji-off still force-overrides. Set 'none' to ignore.")
    p.add_argument("--events-json", default=None,
                   help="Pre-fetched {cal_id: [events]} JSON (skips REST; for tests).")
    args = p.parse_args(argv)

    skip_ids = set() if args.events_json else _load_skip(args.skip_cache)
    summary_locked_ids = set() if args.events_json \
        else _load_summary_locked(args.summary_locked_cache)

    if args.events_json:
        events_by_cal = json.loads(Path(args.events_json).read_text())
    else:
        try:
            try:
                from . import rest  # type: ignore
            except ImportError:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
                from gcal import rest  # type: ignore
            from datetime import datetime, timedelta, timezone
            # Default window starts 2 days BACK, not at "now" — so events that
            # already started today (and yesterday) get styled instead of sitting
            # on the calendar's default color until they scroll out of view. Small
            # + idempotent (restyle_all skips already-correct events). A deeper
            # one-time backfill is done via an explicit --time-min.
            tmin = args.time_min or (
                datetime.now(timezone.utc) - timedelta(days=2)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            tmax = (datetime.now(timezone.utc) + timedelta(days=args.window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            inv = rest.make_invoker(args.token)
            cals = [c.strip() for c in args.calendars.split(",") if c.strip()]
            events_by_cal = _fetch(inv, cals, tmin, tmax)
        except Exception as e:
            reason = f"cannot fetch events (REST token at {args.token}?): {e}"
            print(f"ERROR: {reason}", file=sys.stderr)
            if args.apply:
                _alert_failure(reason)
            return 1

    if args.learn_unknowns:
        learned = load_learned()
        unknowns: dict[str, dict] = {}
        for cal_id, events in events_by_cal.items():
            for e in events:
                if not isinstance(e, dict):
                    continue
                summ = (e.get("summary") or "").strip()
                if not summ:
                    continue
                norm = _normalize_title(summ)
                if (norm in learned or category_for_title(summ) != "household"
                        or _leading_category_hint(summ)):
                    continue
                u = unknowns.setdefault(norm, {"example": summ, "count": 0,
                                               "calendars": set()})
                u["count"] += 1
                u["calendars"].add(cal_id)
        out = [{"normalized": k, "example": v["example"], "count": v["count"],
                "calendars": sorted(v["calendars"])}
               for k, v in sorted(unknowns.items())]
        # Enriched with dynamic-taxonomy proposal context: the LLM can either slot
        # a title into an existing category (learned_styles.json) OR propose a NEW
        # category / retirement per allocator.proposal_context().
        try:
            from . import allocator as _alloc  # type: ignore
        except ImportError:
            from gcal import allocator as _alloc  # type: ignore
        payload = {"unknown_titles": out, "count": len(out),
                   "categories": sorted(_ci.CATEGORY_STYLE.keys())}
        payload.update(_alloc.proposal_context(_tax.CONFIG))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    fc = None if str(args.feature_config).lower() == "none" \
        else load_feature_config(args.feature_config)
    plan = build_maintenance_plan(events_by_cal, skip_ids=skip_ids,
                                  restyle_all=args.restyle_all,
                                  emoji_off=args.emoji_off,
                                  default_category=args.default_category,
                                  feature_config=fc,
                                  summary_locked_ids=summary_locked_ids)

    exit_code = 0
    if args.apply and plan["restyle"]:
        try:
            try:
                from . import rest  # type: ignore
            except ImportError:
                from gcal import rest  # type: ignore
            inv = rest.make_invoker(args.token)
            applied = 0
            failed: list[dict] = []
            new_skip: list[dict] = []
            new_locked: list[dict] = []  # title-write rejected → color-only henceforth
            for r in plan["restyle"]:
                # `fromGmail` (locked summary) → colorId only from the start.
                color_only = r.get("color_only")
                base = {"calendar_id": r["calendar_id"], "event_id": r["event_id"]}
                payload = {**base, "colorId": r["color_id"]}
                if not color_only:
                    payload["summary"] = r["new_summary"]
                last_ex: Optional[Exception] = None
                try:
                    inv("mcp__claude_ai_Google_Calendar__update_event", payload)
                    applied += 1
                    continue
                except Exception as ex:
                    last_ex = ex
                    status = getattr(ex, "status", None)
                    # (1) INVITED recurring series: the master/`_R…` target 404s
                    # — it lives on the organizer's calendar, not yours. Color the
                    # in-window INSTANCES directly; each instance id IS patchable
                    # in your own calendar. Color only (invited events reject a
                    # title change); ≥1 colored instance counts the series as
                    # applied. Newly-in-window instances are picked up on the next
                    # run (the colored ones already carry colorId → skipped).
                    if status in (400, 403, 404, 410) and r.get("instance_ids"):
                        inst_ok = 0
                        for iid in r["instance_ids"]:
                            try:
                                inv("mcp__claude_ai_Google_Calendar__update_event",
                                    {"calendar_id": r["calendar_id"],
                                     "event_id": iid, "colorId": r["color_id"]})
                                inst_ok += 1
                            except Exception as iex:
                                last_ex = iex
                        if inst_ok:
                            applied += 1
                            # We WANTED an emoji but the invited series' master isn't
                            # title-patchable (instances took color only) → summary-
                            # lock the master so the next run styles it color-only and
                            # doesn't re-attempt the doomed title write (the churn fix).
                            if not color_only:
                                new_locked.append({"event_id": r["event_id"],
                                                   "reason": "invited_recurring"})
                            continue
                    # (2) Locked-summary STANDALONE (fromGmail-like not flagged as
                    # such): rejects summary+colorId together but accepts colorId
                    # alone. Retry color-only on the same id.
                    elif not color_only and "summary" in payload:
                        try:
                            inv("mcp__claude_ai_Google_Calendar__update_event",
                                {**base, "colorId": r["color_id"]})
                            applied += 1
                            # The title write was rejected but the color stuck —
                            # this event's summary is un-writable (invited/locked
                            # but not a known locked eventType). Summary-LOCK it (not
                            # full-skip) so we don't re-propose the doomed title write
                            # every run, yet still keep its COLOR updatable.
                            new_locked.append({"event_id": r["event_id"],
                                               "reason": "summary_rejected"})
                            continue
                        except Exception as ex2:
                            last_ex = ex2
                # Per-event resilience: an event you genuinely can't patch
                # (don't own / external invite) — record it, don't abort.
                status = getattr(last_ex, "status", None)
                failed.append({"event_id": r["event_id"],
                               "summary": r["current_summary"],
                               "error": str(last_ex)[:120], "status": status})
                # Permanent rejections (bad-request/forbidden/gone) → negative
                # cache so we don't re-attempt + re-fail this event every day.
                # 400 included: Google-Contacts birthday + other locked events
                # that aren't flagged via eventType reject with 400, not 403/404.
                if status in (400, 403, 404, 410):
                    new_skip.append({"event_id": r["event_id"],
                                     "reason": f"HTTP {status}"})
            plan["applied"] = applied
            plan["apply_failed"] = failed
            if new_skip and not args.events_json:
                _save_skip(args.skip_cache, skip_ids, new_skip)
                plan["skip_cache_added"] = [s["event_id"] for s in new_skip]
            if new_locked and not args.events_json:
                _save_summary_locked(args.summary_locked_cache,
                                     summary_locked_ids, new_locked)
                plan["summary_locked_added"] = [s["event_id"] for s in new_locked]
            # Total apply failure is a REAL failure (likely token/scope) — a
            # silent exit-0 here is how a broken job hides. Alert + exit nonzero.
            if failed and applied == 0:
                _alert_failure(
                    f"all {len(failed)} style applies failed "
                    f"(first: {failed[0].get('error')})")
                exit_code = 1
        except Exception as e:
            print(f"ERROR: apply failed: {e}", file=sys.stderr)
            _alert_failure(f"apply phase crashed: {e}")
            return 1

    # Headless dup handoff: under --apply (the daily launchd path), surface TRUE
    # duplicates to /inbox for a human delete-decision. NEVER auto-delete.
    if args.apply:
        plan["dup_registered"] = register_dup_obligations(
            plan.get("dup_candidates") or [])

    # Always log a one-line summary to stderr so the headless cron log carries a
    # signal even on a clean run (no need to parse the JSON to know it worked).
    t = plan["totals"]
    print(f"SUMMARY scanned={t['scanned']} restyle={t['restyle']} "
          f"applied={plan.get('applied', 0)} failed={len(plan.get('apply_failed', []))} "
          f"dups={t['dup_candidates']} mirrors={t['mirror_candidates']}", file=sys.stderr)

    print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
