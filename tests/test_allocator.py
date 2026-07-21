"""Tests for gcal/allocator.py — the dynamic-taxonomy color solver.

Run: python3 -m pytest <this dir>/test_allocator.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Layout-agnostic bootstrap: walk up to the dir that holds taxonomy.py, so this
# file is byte-identical in the monorepo (lib/gcal/tests/) and the public bundle
# (tests/) and can be synced verbatim.
for _up in Path(__file__).resolve().parents:
    if (_up / "taxonomy.py").exists():
        sys.path.insert(0, str(_up))
        break
from gcal import allocator as A  # noqa: E402
import taxonomy as tax  # noqa: E402


# A compact, FULL scheme mirroring the real one's shape (all 11 colors used).
def _cfg():
    return {
        "categories": {
            "birthday": {"colorId": "1", "emoji": "🎂", "gmail": None},
            "memorial": {"colorId": "1", "emoji": "🙏", "gmail": None},
            "errands": {"colorId": "2", "emoji": "✅", "gmail": None},
            "household": {"colorId": "2", "emoji": "🏠", "gmail": "🏠 Household"},
            "partner": {"colorId": "3", "emoji": "💜", "gmail": "💜 Family"},
            "anniversary": {"colorId": "3", "emoji": "💍", "gmail": None},
            "social": {"colorId": "4", "emoji": "🍷", "gmail": "🤝 Friends"},
            "school": {"colorId": "5", "emoji": "🎒", "gmail": "🎒 School"},
            "travel": {"colorId": "6", "emoji": "✈️", "gmail": "✈️ Travel"},
            "regular_meeting": {"colorId": "7", "emoji": "🗓️", "gmail": None},
            "working_location": {"colorId": "8", "emoji": "🏢", "gmail": None},
            "broad_meeting": {"colorId": "8", "emoji": "👥", "gmail": None},
            "career": {"colorId": "9", "emoji": "💼", "gmail": "💼 Work"},
            "interview": {"colorId": "9", "emoji": "🧑‍💼", "gmail": None},
            "fitness": {"colorId": "10", "emoji": "🏋️", "gmail": None},
            "health": {"colorId": "10", "emoji": "🩺", "gmail": "🩺 Health"},
            "deep_work": {"colorId": "11", "emoji": "🧠", "gmail": None},
        },
        "calendar_categories": ["birthday", "errands", "household", "partner",
                                "social", "school", "travel", "regular_meeting",
                                "career", "fitness", "health", "deep_work"],
        "occasion_color": {"birthday": "1", "anniversary": "3", "memorial": "1"},
        "rules": {
            "color_merges": [["errands", "household"], ["health", "fitness"],
                             ["career", "interview"], ["broad_meeting", "working_location"],
                             ["partner", "anniversary"], ["birthday", "memorial"]],
            "forbidden_on_work": ["1", "3"],
            "high_frequency_threshold": 40,
            "solo_threshold": 40, "hysteresis_weeks": 3, "retire_dead_weeks": 4,
        },
    }


def _state(streaks=None, as_of=None):
    return {"streaks": streaks or {}, "last_census_as_of": as_of}


# ── introspection ─────────────────────────────────────────────────────────────
def test_full_scheme_has_no_free_colors():
    assert A.free_colors(_cfg()) == []


def test_solo_excludes_anchors():
    solos = A.solo_categories(_cfg())
    assert "deep_work" not in solos          # 11 is a semantic anchor
    assert "regular_meeting" in solos        # 7 is a plain content solo
    assert set(solos) == {"social", "school", "travel", "regular_meeting"}


def test_exempt_includes_occasions_and_stakes():
    ex = A.exempt_from_retirement(_cfg())
    assert {"birthday", "anniversary", "memorial", "deep_work", "interview"} <= ex


# ── hysteresis ─────────────────────────────────────────────────────────────────
def test_streak_increments_and_resets():
    cfg = _cfg()
    s = _state()
    s = A.update_streaks(s, {"podcast": 50}, cfg, as_of="2026-07-06")
    assert s["streaks"]["podcast"]["solo_worthy"] == 1
    s = A.update_streaks(s, {"podcast": 50}, cfg, as_of="2026-07-13")
    assert s["streaks"]["podcast"]["solo_worthy"] == 2
    # a dip below threshold resets the streak
    s = A.update_streaks(s, {"podcast": 3}, cfg, as_of="2026-07-20")
    assert s["streaks"]["podcast"]["solo_worthy"] == 0


def test_streak_idempotent_per_census_date():
    cfg = _cfg()
    s = A.update_streaks(_state(), {"x": 50}, cfg, as_of="2026-07-06")
    s2 = A.update_streaks(s, {"x": 50}, cfg, as_of="2026-07-06")  # same date
    assert s2["streaks"]["x"]["solo_worthy"] == 1  # not doubled


def test_dead_streak_counts_zero_events():
    cfg = _cfg()
    s = A.update_streaks(_state(), {"social": 0}, cfg, as_of="2026-07-06")
    assert s["streaks"]["social"]["dead"] == 1


# ── additions ──────────────────────────────────────────────────────────────────
def test_low_freq_add_joins_sibling_additive():
    cfg = _cfg()
    cand = [{"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 5,
             "nearest_sibling": "social", "semantic_bucket": "content"}]
    r = A.propose_changes(cfg, {"podcast": 5}, cand, _state())
    adds = [c for c in r["changes"] if c["kind"] == "add"]
    assert len(adds) == 1
    assert adds[0]["risk"] == "additive"
    assert adds[0]["colorId_after"] == "4"      # shares social's Flamingo
    assert adds[0]["merge_with"] == "social"


def test_semantic_bucket_add_joins_anchor():
    cfg = _cfg()
    cand = [{"kind": "add", "name": "focus_block", "emoji": "🎧", "frequency": 60,
             "semantic_bucket": "protect"}]
    r = A.propose_changes(cfg, {"focus_block": 60}, cand, _state())
    add = [c for c in r["changes"] if c["kind"] == "add"][0]
    assert add["risk"] == "additive"
    assert add["colorId_after"] == "11"          # protect anchor (Tomato)


def test_high_freq_add_without_hysteresis_falls_back_to_merge():
    cfg = _cfg()
    # high frequency but streak not yet held → must NOT recolor an incumbent
    cand = [{"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 99,
             "nearest_sibling": "social", "semantic_bucket": "content"}]
    r = A.propose_changes(cfg, {"podcast": 99}, cand, _state())
    assert all(c["risk"] == "additive" for c in r["changes"])
    assert not any(c["kind"] == "recolor" for c in r["changes"])


def test_high_freq_add_without_explicit_demotion_stays_additive_and_suggests():
    """The solver must NOT invent an incumbent demotion (that produced incoherent
    self-demotions). A high-freq newcomer with no demote hint → additive + a
    reallocation SUGGESTION for human/LLM review."""
    cfg = _cfg()
    st = _state(streaks={"podcast": {"solo_worthy": 3, "dead": 0}})
    freq = {"podcast": 99, "travel": 4}
    cand = [{"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 99,
             "nearest_sibling": "social", "semantic_bucket": "content"}]
    r = A.propose_changes(cfg, freq, cand, st)
    assert all(c["risk"] == "additive" for c in r["changes"])
    assert not any(c["kind"] == "recolor" for c in r["changes"])
    assert any("SOLO color" in w for w in r["warnings"])


def test_explicit_demotion_promotes_and_gates_and_applies_cleanly():
    """With an explicit demote/demote_into, a solo-promotion IS proposed as a
    gated recolor — and it MUST round-trip through validate_config when applied
    (the invariant the end-to-end run caught the solver violating)."""
    cfg = _cfg()
    st = _state(streaks={"podcast": {"solo_worthy": 3, "dead": 0}})
    freq = {"podcast": 99, "travel": 4, "social": 80}
    cand = [{"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 99,
             "semantic_bucket": "content", "demote": "travel", "demote_into": "school"}]
    r = A.propose_changes(cfg, freq, cand, st)
    recolors = [c for c in r["changes"] if c["risk"] == "recolor"]
    assert len(recolors) == 2
    demoted = next(c for c in recolors if c["kind"] == "recolor")
    promoted = next(c for c in recolors if c["kind"] == "add")
    assert demoted["category"] == "travel" and demoted["colorId_after"] == "5"  # joins school
    assert promoted["category"] == "podcast" and promoted["colorId_after"] == "6"  # takes freed Tangerine
    # the applied end-state must be VALID (no undeclared collision)
    new = A.apply_changes(cfg, r["changes"])
    assert tax.validate_config(new, strict=False) == [], tax.validate_config(new)
    assert new["categories"]["travel"]["colorId"] == "5"
    assert new["categories"]["podcast"]["colorId"] == "6"


def test_invalid_demotion_hint_falls_back_to_additive():
    cfg = _cfg()
    st = _state(streaks={"podcast": {"solo_worthy": 3, "dead": 0}})
    # demote target 'deep_work' is a semantic anchor / not a plain solo → invalid
    cand = [{"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 99,
             "nearest_sibling": "social", "semantic_bucket": "content",
             "demote": "deep_work", "demote_into": "school"}]
    r = A.propose_changes(cfg, {"podcast": 99}, cand, st)
    assert not any(c["kind"] == "recolor" for c in r["changes"])


# ── retirements ──────────────────────────────────────────────────────────────
def test_retire_requires_dead_hysteresis():
    cfg = _cfg()
    cand = [{"kind": "retire", "name": "social"}]
    r = A.propose_changes(cfg, {"social": 0}, cand, _state())  # no held streak
    assert not any(c["kind"] == "retire" for c in r["changes"])
    assert any("not held" in w for w in r["warnings"])


def test_retire_dead_solo_is_additive_and_frees_slot():
    cfg = _cfg()
    st = _state(streaks={"social": {"dead": 4, "solo_worthy": 0}})
    r = A.propose_changes(cfg, {"social": 0}, [{"kind": "retire", "name": "social"}], st)
    ret = [c for c in r["changes"] if c["kind"] == "retire"][0]
    assert ret["risk"] == "additive"            # retiring a dead cat is invisible
    assert ret["was_solo"] is True


def test_exempt_category_never_retired():
    cfg = _cfg()
    st = _state(streaks={"interview": {"dead": 9, "solo_worthy": 0}})
    r = A.propose_changes(cfg, {"interview": 0}, [{"kind": "retire", "name": "interview"}], st)
    assert not any(c["kind"] == "retire" for c in r["changes"])


def test_retirement_frees_slot_for_addition_same_pass():
    cfg = _cfg()
    st = _state(streaks={"social": {"dead": 4, "solo_worthy": 0}})
    cand = [{"kind": "retire", "name": "social"},
            {"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 10,
             "semantic_bucket": "content"}]
    r = A.propose_changes(cfg, {"social": 0, "podcast": 10}, cand, st)
    add = [c for c in r["changes"] if c["kind"] == "add"][0]
    assert add["risk"] == "additive"
    assert add["colorId_after"] == "4"          # took social's freed slot


# ── apply → must round-trip through validate_config ─────────────────────────────
def test_apply_additive_passes_validation():
    cfg = _cfg()
    cand = [{"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 5,
             "nearest_sibling": "social", "semantic_bucket": "content"}]
    r = A.propose_changes(cfg, {"podcast": 5}, cand, _state())
    new = A.apply_changes(cfg, r["changes"], risk_filter="additive")
    problems = tax.validate_config(new, strict=False)
    assert problems == [], problems
    assert new["categories"]["podcast"]["colorId"] == "4"
    assert ["podcast", "social"] in [sorted(g) for g in new["rules"]["color_merges"]] \
        or any({"podcast", "social"} <= set(g) for g in new["rules"]["color_merges"])


def test_apply_retire_removes_category_and_validates():
    cfg = _cfg()
    st = _state(streaks={"social": {"dead": 4, "solo_worthy": 0}})
    r = A.propose_changes(cfg, {"social": 0}, [{"kind": "retire", "name": "social"}], st)
    new = A.apply_changes(cfg, r["changes"])
    assert "social" not in new["categories"]
    assert "social" not in new["calendar_categories"]
    assert tax.validate_config(new, strict=False) == []


def test_apply_risk_filter_skips_recolors():
    cfg = _cfg()
    st = _state(streaks={"podcast": {"solo_worthy": 3, "dead": 0}})
    freq = {"podcast": 99, "travel": 4}
    # explicit demotion → podcast's add is risk=recolor (gated), so additive-only
    # apply must NOT introduce it
    cand = [{"kind": "add", "name": "podcast", "emoji": "🎙️", "frequency": 99,
             "semantic_bucket": "content", "demote": "travel", "demote_into": "school"}]
    r = A.propose_changes(cfg, freq, cand, st)
    new = A.apply_changes(cfg, r["changes"], risk_filter="additive")
    assert "podcast" not in new["categories"]


# ── overlay writer: must PRESERVE personal sections + round-trip via loader ─────
import yaml  # noqa: E402


def test_minimal_overlay_emits_only_deltas():
    default_cfg = _cfg()
    new = A.apply_changes(default_cfg,
                          [{"kind": "add", "category": "podcast", "colorId_after": "4",
                            "emoji": "🎙️", "gmail": None, "merge_with": "social"}])
    ov = A.minimal_overlay(new, default_cfg)
    assert set(ov["categories"]) == {"podcast"}      # only the NEW category
    assert "podcast" in ov["calendar_categories"]    # full list emitted
    assert any({"podcast", "social"} <= set(g) for g in ov["rules"]["color_merges"])


def test_write_user_overlay_preserves_personal_sections(tmp_path):
    # a user config that already carries personal-instance data
    user = tmp_path / "user.yml"
    user.write_text(yaml.safe_dump({
        "paths": {"state_dir": "~/x"},
        "accounts": {"default": {"token": "token.json"}},
        "calendars": [{"id": "primary", "alias": "Personal"}],
        "self_names": ["adam"],
    }, allow_unicode=True))
    default = tmp_path / "default.yml"
    default.write_text(yaml.safe_dump(_cfg(), allow_unicode=True))

    new = A.apply_changes(_cfg(),
                          [{"kind": "add", "category": "podcast", "colorId_after": "4",
                            "emoji": "🎙️", "gmail": None, "merge_with": "social"}])
    A.write_user_overlay(new, user_path=user, default_cfg_path=default)

    back = yaml.safe_load(user.read_text())
    # personal sections untouched
    assert back["calendars"] == [{"id": "primary", "alias": "Personal"}]
    assert back["self_names"] == ["adam"]
    assert back["accounts"]["default"]["token"] == "token.json"
    # taxonomy delta written
    assert back["categories"]["podcast"]["emoji"] == "🎙️"
    assert "podcast" in back["calendar_categories"]


def test_write_overlay_deep_merges_over_default_and_validates(tmp_path):
    user = tmp_path / "user.yml"
    default = tmp_path / "default.yml"
    default.write_text(yaml.safe_dump(_cfg(), allow_unicode=True))

    new = A.apply_changes(_cfg(),
                          [{"kind": "add", "category": "podcast", "colorId_after": "4",
                            "emoji": "🎙️", "gmail": None, "merge_with": "social"}])
    A.write_user_overlay(new, user_path=user, default_cfg_path=default)

    # simulate taxonomy._load's deep-merge: default + overlay → effective config
    merged = tax._deep_merge(_cfg(), yaml.safe_load(user.read_text()))
    assert merged["categories"]["podcast"]["colorId"] == "4"
    assert tax.validate_config(merged, strict=False) == []


def test_queue_recolors_writes_only_gated(tmp_path):
    changes = [
        {"kind": "add", "category": "a", "risk": "additive", "colorId_after": "4"},
        {"kind": "recolor", "category": "travel", "risk": "recolor", "colorId_after": "6"},
    ]
    q = tmp_path / "pending.json"
    n = A.queue_recolors(changes, path=q, as_of="2026-07-20")
    assert n == 1
    doc = yaml.safe_load(q.read_text())
    assert len(doc["changes"]) == 1 and doc["changes"][0]["category"] == "travel"
