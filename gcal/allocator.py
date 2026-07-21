#!/usr/bin/env python3
"""allocator.py — the color-allocation solver for the DYNAMIC taxonomy (T3).

Google Calendar events have exactly 11 colorIds (hard cap, no custom hex). So a
taxonomy that grows/shrinks with the corpus cannot give every category its own
color — the 11 colors are a SCARCE RESOURCE that must be allocated. This module
is that allocator. It is deliberately **incremental**: it starts from the current
(hand-tuned, stable) allocation and makes the MINIMAL move needed to accommodate
a new or retired category, rather than re-solving the whole scheme from scratch
(a global re-solve would reshuffle everything → all-recolor → churn, the one
failure mode we most want to avoid — see the plan's HYSTERESIS section).

Three of the 11 colors are SEMANTIC ANCHORS whose meaning must never be
frequency-reassigned (muscle memory depends on it):
    11 Tomato    = PROTECT       (deep_work / DND)
     8 Graphite  = RECEDE        (town halls, working-location banners)
     9 Blueberry = PROFESSIONAL  (career/interview; only purple-family allowed on work)
The other 8 colors are allocated to "content" categories by frequency, with the
low-frequency tail MERGED into nearest semantic siblings.

Placement of a NEW category (graceful degradation, emoji is never scarce):
  1. a genuinely FREE colorId exists            → assign solo        (ADDITIVE)
  2. else join its nearest sibling's color group → share via merge   (ADDITIVE)
  3. solo-promotion (freq ≥ solo_threshold, held HYSTERESIS weeks, a demotable
     incumbent exists) → candidate takes the freed slot, incumbent merges down
                                                    → moves an incumbent's color (RECOLOR)

RISK CLASSES drive the apply autonomy (decision 2026-07-20):
  ADDITIVE  → auto-apply  (no NON-DEAD incumbent's colorId changes)
  RECOLOR   → gate via /calendar-colors AUQ (a live category's color moves)

Pure module: no network, no implicit clock. Callers inject the frequency census,
the loaded config (taxonomy.CONFIG), the LLM candidates, and the hysteresis state.
State I/O helpers (load/save_state) touch only a small JSON file and take an
explicit `now` string so tests stay deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import yaml  # helper venv provides it (system python lacks it)

# Canonical Google Calendar event colorId → display name (fixed by Google; lived
# only in prose before this). Used for readable proposals/dashboards.
COLOR_NAMES: dict[str, str] = {
    "1": "Lavender", "2": "Sage", "3": "Grape", "4": "Flamingo",
    "5": "Banana", "6": "Tangerine", "7": "Peacock", "8": "Graphite",
    "9": "Blueberry", "10": "Basil", "11": "Tomato",
}
ALL_COLOR_IDS: tuple[str, ...] = tuple(str(i) for i in range(1, 12))

# colorIds reserved for a fixed MEANING — allocated by semantics, not frequency.
SEMANTIC_ANCHORS: dict[str, str] = {"11": "protect", "8": "recede", "9": "professional"}

# Defaults for tuning knobs (overridable via cfg['rules']).
_DEFAULTS = {
    "solo_threshold": 40,       # events/window a content category needs to earn a solo color
    "hysteresis_weeks": 3,      # consecutive censuses a change must hold before it fires
    "retire_dead_weeks": 4,     # consecutive zero-event censuses before a solo category retires
}

_STATE_PATH = Path("~/.claude/state/calendar/allocation_state.json").expanduser()


# ── config introspection helpers (all pure) ─────────────────────────────────
def _rule(cfg: dict, key: str) -> Any:
    return (cfg.get("rules", {}) or {}).get(key, _DEFAULTS.get(key))


def _categories(cfg: dict) -> dict:
    return cfg.get("categories", {}) or {}


def _merges(cfg: dict) -> list[set]:
    return [set(g) for g in (cfg.get("rules", {}) or {}).get("color_merges", [])]


def exempt_from_retirement(cfg: dict) -> set:
    """Stakes-based / pinned categories that are low-volume BY DESIGN and must
    never be retired on frequency: the interview/deep_work/meeting anchors plus
    every occasion pin (birthday/anniversary/memorial keep their colors)."""
    stakes = {"interview", "deep_work", "working_location", "broad_meeting", "regular_meeting"}
    occasions = set((cfg.get("occasion_color", {}) or {}).keys())
    return stakes | occasions


def color_groups(cfg: dict) -> dict[str, list[str]]:
    """colorId -> [categories sharing it] (calendar categories only; colorId!=null)."""
    groups: dict[str, list[str]] = {}
    for name, spec in _categories(cfg).items():
        cid = (spec or {}).get("colorId")
        if cid is not None:
            groups.setdefault(str(cid), []).append(name)
    return groups


def free_colors(cfg: dict) -> list[str]:
    """colorIds in 1..11 not currently assigned to ANY category."""
    used = set(color_groups(cfg).keys())
    return [c for c in ALL_COLOR_IDS if c not in used]


def solo_categories(cfg: dict) -> dict[str, str]:
    """category -> colorId for categories that hold their colorId ALONE (a freed
    solo color is what a solo-promotion needs). Excludes semantic anchors — their
    color is reserved for meaning and never handed to a content category."""
    out: dict[str, str] = {}
    for cid, members in color_groups(cfg).items():
        if len(members) == 1 and cid not in SEMANTIC_ANCHORS:
            out[members[0]] = cid
    return out


# ── hysteresis state ─────────────────────────────────────────────────────────
def load_state(path: Optional[Path] = None) -> dict:
    p = path or _STATE_PATH
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"streaks": {}, "last_census_as_of": None}


def save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or _STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def update_streaks(state: dict, frequency: dict, cfg: dict, *, as_of: str) -> dict:
    """Advance per-category streak counters from one weekly census. `solo_worthy`
    counts consecutive censuses at/above solo_threshold; `dead` counts consecutive
    zero-event censuses. Both reset to 0 the moment the condition breaks — so a
    change only fires after it has genuinely HELD (anti-churn). Idempotent per
    as_of date: re-running the same census date does not double-count."""
    if state.get("last_census_as_of") == as_of:
        return state  # already counted this census
    solo_thr = _rule(cfg, "solo_threshold")
    streaks = dict(state.get("streaks", {}))
    # union of categories seen in census + already tracked (so absences reset dead=?)
    names = set(frequency) | set(streaks) | set(_categories(cfg))
    for name in names:
        n = int(frequency.get(name, 0) or 0)
        s = dict(streaks.get(name, {"solo_worthy": 0, "dead": 0}))
        s["solo_worthy"] = s.get("solo_worthy", 0) + 1 if n >= solo_thr else 0
        s["dead"] = s.get("dead", 0) + 1 if n == 0 else 0
        streaks[name] = s
    return {"streaks": streaks, "last_census_as_of": as_of}


def _held(state: dict, category: str, key: str, weeks: int) -> bool:
    return int((state.get("streaks", {}).get(category, {}) or {}).get(key, 0)) >= weeks


# ── the proposer ──────────────────────────────────────────────────────────────
def _change(kind: str, category: str, before: Optional[str], after: Optional[str],
            risk: str, rationale: str, **extra) -> dict:
    d = {"kind": kind, "category": category, "colorId_before": before,
         "colorId_after": after, "risk": risk, "rationale": rationale}
    d.update(extra)
    return d


def propose_changes(cfg: dict, frequency: dict, candidates: list[dict],
                    state: dict) -> dict:
    """Given the loaded config, a frequency census {category: count}, LLM
    `candidates` (add/retire — see plan §Proposal schema), and hysteresis state,
    return {"changes": [...], "warnings": [...]}. Each change carries a `risk`
    ("additive"|"recolor") that drives auto-apply vs. AUQ-gate. PURE — no I/O,
    no config mutation; apply_changes() materializes the end-state separately."""
    changes: list[dict] = []
    warnings: list[str] = []
    cats = _categories(cfg)
    solo_thr = _rule(cfg, "solo_threshold")
    hyst = _rule(cfg, "hysteresis_weeks")
    dead_wk = _rule(cfg, "retire_dead_weeks")
    exempt = exempt_from_retirement(cfg)

    # Track slots we free within this pass so a retirement can feed a promotion.
    freed: list[str] = []

    # 1) RETIREMENTS first (they free slots for promotions in the same pass).
    for c in candidates:
        if c.get("kind") != "retire":
            continue
        name = c["name"]
        if name not in cats or name in exempt:
            continue
        if not _held(state, name, "dead", dead_wk):
            warnings.append(f"retire {name!r}: not held {dead_wk} censuses yet — skipped")
            continue
        cid = str(cats[name].get("colorId")) if cats[name].get("colorId") else None
        is_solo = name in solo_categories(cfg)
        # Retiring a dead category is invisible (≈0 events) → ADDITIVE even though
        # its own colorId "changes": no LIVE incumbent is recolored.
        changes.append(_change("retire", name, cid, None, "additive",
                               f"0 events for ≥{dead_wk} censuses; freeing slot",
                               was_solo=is_solo))
        if is_solo and cid:
            freed.append(cid)

    # 2) ADDITIONS.
    sibling_solos = solo_categories(cfg)
    for c in candidates:
        if c.get("kind") != "add":
            continue
        name, emoji = c["name"], c.get("emoji")
        if name in cats:
            warnings.append(f"add {name!r}: already exists — skipped")
            continue
        freq = int(c.get("frequency", frequency.get(name, 0)) or 0)
        sibling = c.get("nearest_sibling")
        bucket = c.get("semantic_bucket", "content")

        # A semantic-bucket category joins its anchor color directly (recede/pro/
        # protect) — always additive (shares the anchor, moves no incumbent).
        anchor_for = {v: k for k, v in SEMANTIC_ANCHORS.items()}
        if bucket in anchor_for:
            cid = anchor_for[bucket]
            changes.append(_change("add", name, None, cid, "additive",
                                   f"joins {bucket} anchor {COLOR_NAMES[cid]}",
                                   emoji=emoji, gmail=c.get("gmail"),
                                   merge_with=color_groups(cfg).get(cid, [])))
            continue

        # Content category. Prefer a free slot (incl. slots freed by retirements
        # this pass); else join sibling's color; else consider solo-promotion.
        avail = free_colors(cfg) + freed
        if avail:
            cid = avail.pop(0)
            if cid in freed:
                freed.remove(cid)
            changes.append(_change("add", name, None, cid, "additive",
                                   f"free slot {COLOR_NAMES[cid]}",
                                   emoji=emoji, gmail=c.get("gmail")))
            continue

        # No free slot. A solo-promotion moves a LIVE incumbent's color, so WHERE
        # the bumped incumbent lands is a semantic call the SOLVER must not invent
        # (that produced incoherent travel→travel demotions). It fires only when
        # the candidate EXPLICITLY names `demote` (the incumbent to bump) and
        # `demote_into` (an existing category it should share a color with) — both
        # decided by the LLM. The incumbent must be a demotable (solo, non-exempt)
        # category and the candidate must clear the frequency bar + hysteresis.
        demote_name, demote_into = c.get("demote"), c.get("demote_into")
        bar_cleared = freq >= solo_thr and _held(state, name, "solo_worthy", hyst)
        valid_promotion = (
            bar_cleared and demote_name in sibling_solos and demote_name not in exempt
            and demote_into in cats and demote_into != demote_name
            and cats.get(demote_into, {}).get("colorId") is not None)
        if valid_promotion:
            inc_cid = sibling_solos[demote_name]
            tgt_cid = str(cats[demote_into]["colorId"])
            changes.append(_change("recolor", demote_name, inc_cid, tgt_cid, "recolor",
                                   f"demoted to share {COLOR_NAMES[tgt_cid]} with "
                                   f"{demote_into!r} so {name!r} (freq {freq}) can hold "
                                   f"{COLOR_NAMES[inc_cid]}", merge_with=demote_into))
            changes.append(_change("add", name, None, inc_cid, "recolor",
                                   f"promoted to solo {COLOR_NAMES[inc_cid]} "
                                   f"(freq {freq} ≥ {solo_thr}, held {hyst})",
                                   emoji=emoji, gmail=c.get("gmail")))
            continue

        # Additive fallback — merge with nearest sibling, no incumbent recolor.
        if not sibling or sibling not in cats:
            sibling = _nearest_merge_target(cfg, name) or _busiest_category(cfg, frequency)
        cid = str(cats[sibling]["colorId"]) if sibling and cats.get(sibling, {}).get("colorId") else None
        if cid is None:
            warnings.append(f"add {name!r}: no sibling color found — skipped")
            continue
        changes.append(_change("add", name, None, cid, "additive",
                               f"shares {COLOR_NAMES[cid]} with {sibling!r}",
                               emoji=emoji, gmail=c.get("gmail"), merge_with=sibling))
        if bar_cleared and not valid_promotion:
            # high-frequency enough to deserve its own color, but no valid explicit
            # demotion was supplied → SUGGEST a reallocation for human/LLM review
            # rather than fabricate an incumbent recolor.
            warnings.append(
                f"{name!r} is high-frequency ({freq}) and may warrant a SOLO color; "
                f"supply demote + demote_into to reallocate (else it shares {COLOR_NAMES[cid]}).")
    return {"changes": changes, "warnings": warnings}


def _nearest_merge_target(cfg: dict, category: str) -> Optional[str]:
    """A reasonable existing category for `category` to share a color with: the
    other member of an existing merge-group it's declared in, else None. Kept
    simple/deterministic — semantic affinity is the LLM's job (candidate carries
    nearest_sibling); this is the fallback."""
    for g in _merges(cfg):
        if category in g and len(g) > 1:
            return sorted(g - {category})[0]
    return None


def _busiest_category(cfg: dict, frequency: dict) -> Optional[str]:
    solos = solo_categories(cfg)
    if not solos:
        return None
    return max(solos, key=lambda k: int(frequency.get(k, 0) or 0))


# ── materialize end-state config (pure) ────────────────────────────────────────
def apply_changes(cfg: dict, changes: list[dict], *, risk_filter: Optional[str] = None) -> dict:
    """Return a NEW config dict with `changes` applied (does not mutate `cfg`).
    `risk_filter` (e.g. "additive") applies only changes of that risk — the
    weekly loop uses it to auto-apply additive changes while queuing recolors.
    Updates categories{}, calendar_categories[], and rules.color_merges[] so the
    result round-trips through taxonomy.validate_config()."""
    import copy
    out = copy.deepcopy(cfg)
    cats = out.setdefault("categories", {})
    cal_cats = out.setdefault("calendar_categories", [])
    rules = out.setdefault("rules", {})
    merges = [set(g) for g in rules.get("color_merges", [])]

    def _merge_pair(a: str, b: str):
        for g in merges:
            if a in g or b in g:
                g.update({a, b})
                return
        merges.append({a, b})

    for ch in changes:
        if risk_filter and ch.get("risk") != risk_filter:
            continue
        name, kind = ch["category"], ch["kind"]
        if kind == "retire":
            cats.pop(name, None)
            if name in cal_cats:
                cal_cats.remove(name)
            merges = [g - {name} for g in merges]
        elif kind == "add":
            cats[name] = {"colorId": ch["colorId_after"], "emoji": ch.get("emoji"),
                          "gmail": ch.get("gmail")}
            if name not in cal_cats:
                cal_cats.append(name)
            sib = ch.get("merge_with")
            if isinstance(sib, str):
                _merge_pair(name, sib)
        elif kind == "recolor":
            if name in cats:
                cats[name]["colorId"] = ch["colorId_after"]
            sib = ch.get("merge_with")
            if isinstance(sib, str):
                _merge_pair(name, sib)

    merges = [sorted(g) for g in merges if len(g) > 1]
    rules["color_merges"] = merges
    out["calendar_categories"] = cal_cats
    return out


def proposal_context(cfg: dict) -> dict:
    """The context an LLM needs to propose NEW categories from unknown titles:
    existing categories (emoji + color + who they share with), the emoji glyphs
    already taken (so a proposal picks a UNIQUE one), the three semantic buckets a
    content-vs-anchor decision hinges on, and the candidate output schema. Emitted
    alongside --learn-unknowns so the weekly loop can cluster + propose in one shot."""
    groups = color_groups(cfg)
    existing: dict[str, dict] = {}
    taken_emojis: list[str] = []
    for name, spec in _categories(cfg).items():
        cid = spec.get("colorId")
        emoji = spec.get("emoji")
        if emoji:
            taken_emojis.append(emoji)
        existing[name] = {
            "emoji": emoji,
            "colorId": cid,
            "color_name": COLOR_NAMES.get(str(cid)) if cid else None,
            "shares_color_with": sorted(set(groups.get(str(cid), [])) - {name}) if cid else [],
        }
    buckets = {
        "protect": "deep-focus / do-not-disturb only (red Tomato/11)",
        "recede": "low-salience background — town halls, location banners (gray Graphite/8)",
        "professional": "career / hiring / interviews (navy Blueberry/9)",
        "content": "everything else — a normal life/work category competing for the 8 content colors by frequency",
    }
    return {
        "existing_categories": existing,
        "taken_emojis": sorted(set(taken_emojis)),
        "semantic_buckets": buckets,
        "candidate_schema": {
            "kind": "'add' to propose a new category, 'retire' to drop a dead one",
            "name": "short snake_case slug (new; must not collide with existing)",
            "emoji": "a single UNIQUE glyph not in taken_emojis (add only)",
            "semantic_bucket": "one of protect|recede|professional|content",
            "nearest_sibling": "an existing category to share a color with if merged (content adds)",
            "frequency": "integer: count of matching events in the window (sum the cluster's counts)",
            "gmail": "a Gmail label name or null",
            "evidence": "list of the example titles that motivated this category",
            "demote": "(optional) an existing SOLO category to bump so this one can take a solo color — only when this add is high-frequency and truly deserves its own color. Triggers a GATED recolor.",
            "demote_into": "(optional, required with demote) an existing category the bumped one should share a color with — your semantic call for where it lands.",
        },
        "guidance": ("Only propose an 'add' when a cluster of unknown titles is "
                     "semantically coherent, recurs, and is not already served by "
                     "an existing category. Otherwise map the title into an existing "
                     "category via learned_styles.json. Never propose >1 new category "
                     "per coherent cluster."),
    }


# ── user-overlay writer (edits the PERSONAL config; preserves personal sections) ─
_DEFAULT_CFG_PATH = Path(__file__).resolve().parents[1] / "calendar-colors.default.yml"
_USER_CFG_PATH = Path("~/.claude/config/calendar-colors.yml").expanduser()
_PENDING_PATH = Path("~/.claude/state/calendar/pending_recolors.json").expanduser()


def minimal_overlay(new_merged: dict, default_cfg: dict) -> dict:
    """The SMALLEST overlay that, deep-merged over the shipped default, yields
    `new_merged`'s taxonomy. `categories` is dict-merged so only new/changed
    categories are emitted; `calendar_categories` and `rules.color_merges` are
    LISTS (deep_merge replaces lists wholesale) so they are emitted in FULL."""
    dflt_cats = default_cfg.get("categories", {})
    new_cats = new_merged.get("categories", {})
    cat_overlay = {name: spec for name, spec in new_cats.items()
                   if dflt_cats.get(name) != spec}
    overlay: dict = {}
    if cat_overlay:
        overlay["categories"] = cat_overlay
    if new_merged.get("calendar_categories") != default_cfg.get("calendar_categories"):
        overlay["calendar_categories"] = list(new_merged.get("calendar_categories", []))
    new_merges = (new_merged.get("rules", {}) or {}).get("color_merges")
    if new_merges != (default_cfg.get("rules", {}) or {}).get("color_merges"):
        overlay["rules"] = {"color_merges": new_merges}
    return overlay


def write_user_overlay(new_merged: dict, *, user_path: Optional[Path] = None,
                       default_cfg_path: Optional[Path] = None) -> dict:
    """Persist the taxonomy portion of `new_merged` into the PERSONAL config as a
    minimal overlay, PRESERVING every personal-instance section already there
    (paths / accounts / calendars / self_names). Returns the overlay written.
    Reads the shipped default to compute deltas. Unicode emoji are kept literal."""
    up = user_path or _USER_CFG_PATH
    dp = default_cfg_path or _DEFAULT_CFG_PATH
    default_cfg = yaml.safe_load(dp.read_text()) or {}
    overlay = minimal_overlay(new_merged, default_cfg)
    existing = (yaml.safe_load(up.read_text()) if up.exists() else {}) or {}
    # merge overlay onto existing user file: dict-merge categories + rules,
    # replace calendar_categories; never touch paths/accounts/calendars/self_names.
    if "categories" in overlay:
        existing.setdefault("categories", {}).update(overlay["categories"])
    if "calendar_categories" in overlay:
        existing["calendar_categories"] = overlay["calendar_categories"]
    if "rules" in overlay:
        existing.setdefault("rules", {})["color_merges"] = overlay["rules"]["color_merges"]
    up.parent.mkdir(parents=True, exist_ok=True)
    up.write_text(yaml.safe_dump(existing, allow_unicode=True, sort_keys=False))
    return overlay


def queue_recolors(changes: list[dict], *, path: Optional[Path] = None,
                   as_of: Optional[str] = None) -> int:
    """Write the GATED (risk='recolor') changes to the pending-recolors queue that
    /calendar-colors surfaces for AUQ approval. Returns how many were queued."""
    recolors = [c for c in changes if c.get("risk") == "recolor"]
    p = path or _PENDING_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"as_of": as_of, "changes": recolors},
                            indent=2, ensure_ascii=False))
    return len(recolors)


def summarize(result: dict) -> str:
    """Human-readable proposal digest (for the dashboard / AUQ)."""
    lines = []
    for ch in result.get("changes", []):
        arrow = (f"{COLOR_NAMES.get(str(ch['colorId_before']),'—')}→"
                 f"{COLOR_NAMES.get(str(ch['colorId_after']),'—')}")
        tag = "🟢 auto" if ch["risk"] == "additive" else "🟡 gate"
        lines.append(f"  {tag}  [{ch['kind']}] {ch['category']:16} {arrow:20} — {ch['rationale']}")
    for w in result.get("warnings", []):
        lines.append(f"  ⚠️  {w}")
    return "\n".join(lines) if lines else "  (no changes)"


def main(argv: Optional[list[str]] = None) -> int:
    """Weekly dynamic-taxonomy step. Reads LLM candidates + a frequency census,
    advances hysteresis, proposes changes, and (with --apply-additive) writes the
    ADDITIVE ones to the personal config while queuing RECOLORS for AUQ approval.

      allocator.py --candidates cand.json --frequency freq.json --apply-additive

    Pure-ish: config comes from taxonomy.CONFIG (fresh load). Emits a JSON report
    on stdout; the weekly loop restyles afterward only if something was applied."""
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import taxonomy as tax

    p = argparse.ArgumentParser(description="Dynamic-taxonomy color allocator.")
    p.add_argument("--candidates", help="JSON file of LLM candidates ([] or {'candidates':[]}). Default stdin.")
    p.add_argument("--frequency", required=True, help="frequency.py --json output file.")
    p.add_argument("--state", default=str(_STATE_PATH))
    p.add_argument("--apply-additive", action="store_true",
                   help="Write additive changes to the user config + save state + queue recolors.")
    p.add_argument("--pending", default=str(_PENDING_PATH))
    p.add_argument("--user-config", default=str(_USER_CFG_PATH),
                   help="Target user-config path for --apply-additive writes "
                        "(default the live personal config; override for testing).")
    args = p.parse_args(argv)

    freq_doc = json.loads(Path(args.frequency).read_text())
    frequency = {k: int(v) for k, v in (freq_doc.get("total") or {}).items()}
    as_of = freq_doc.get("as_of")

    raw = (Path(args.candidates).read_text() if args.candidates else sys.stdin.read()).strip()
    doc = json.loads(raw) if raw else []
    candidates = doc.get("candidates", doc) if isinstance(doc, dict) else doc

    state = load_state(Path(args.state))
    state = update_streaks(state, frequency, tax.CONFIG, as_of=as_of or "")
    result = propose_changes(tax.CONFIG, frequency, candidates, state)

    applied_overlay, queued = None, 0
    if args.apply_additive:
        additive = [c for c in result["changes"] if c["risk"] == "additive"]
        if additive:
            new_cfg = apply_changes(tax.CONFIG, result["changes"], risk_filter="additive")
            problems = validate_config_or_import(new_cfg)
            if problems:
                result["warnings"].append(f"additive apply FAILED validation, skipped: {problems}")
            else:
                applied_overlay = write_user_overlay(new_cfg, user_path=Path(args.user_config))
        queued = queue_recolors(result["changes"], path=Path(args.pending), as_of=as_of)
        save_state(state, Path(args.state))

    print(json.dumps({
        "as_of": as_of,
        "proposed": result["changes"],
        "warnings": result["warnings"],
        "applied_overlay": applied_overlay,
        "recolors_queued": queued,
        "summary": summarize(result),
    }, indent=2, ensure_ascii=False))
    return 0


def validate_config_or_import(cfg: dict) -> list[str]:
    """validate_config against the taxonomy module (kept out of import scope of the
    pure section so allocator stays importable without a taxonomy on sys.path)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import taxonomy as tax
    return tax.validate_config(cfg, strict=False)


if __name__ == "__main__":
    raise SystemExit(main())
