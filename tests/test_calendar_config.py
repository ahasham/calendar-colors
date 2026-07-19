"""T1 config-system tests — invariants + loader + validation for the YAML-backed
taxonomy and the unified calendar config. Complements test_calendar_style.py
(which asserts the concrete scheme) by guarding the STRUCTURE for any user's
config, not just one user's."""
import copy
import sys
from pathlib import Path

import pytest
import yaml

_LIB = str(Path(__file__).resolve().parent.parent)
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import taxonomy as tax  # noqa: E402

DEFAULT_YAML = Path(_LIB) / "calendar-colors.default.yml"


def _default_cfg():
    return yaml.safe_load(DEFAULT_YAML.read_text())


# ── shipped default is loadable + valid ──────────────────────────────────────
def test_default_yaml_loads():
    cfg = _default_cfg()
    assert cfg["categories"] and cfg["calendar_categories"] and cfg["rules"]


def test_shipped_default_passes_validation():
    assert tax.validate_config(_default_cfg(), strict=False) == []


def test_public_surface_rebuilt_from_default():
    """CATEGORIES etc. must equal the shipped defaults (the loader must not drop
    or reshape anything the six consumers import)."""
    d = _default_cfg()
    assert tax.CATEGORIES == d["categories"]
    assert tuple(d["calendar_categories"]) == tax._CALENDAR_CATS
    for c in tax.CATEGORIES:
        assert tax.color_id(c) == tax.CATEGORIES[c]["colorId"]
        assert tax.emoji(c) == tax.CATEGORIES[c]["emoji"]


# ── the invariants (the whole point of validate_config) ──────────────────────
def test_distinct_calendar_colors_except_declared_merges():
    cfg = _default_cfg()
    cats, cal_cats = cfg["categories"], cfg["calendar_categories"]
    merges = [set(g) for g in cfg["rules"].get("color_merges", [])]
    by_color = {}
    for c in cal_cats:
        cid = cats[c]["colorId"]
        by_color.setdefault(cid, []).append(c)
    for cid, members in by_color.items():
        if len(members) > 1:
            assert any(set(members) <= g for g in merges), \
                f"undeclared collision on {cid}: {members}"


def test_validate_catches_undeclared_collision():
    bad = copy.deepcopy(_default_cfg())
    bad["categories"]["fitness"]["colorId"] = bad["categories"]["career"]["colorId"]
    probs = tax.validate_config(bad, strict=False)
    assert any("collision" in p for p in probs)
    with pytest.raises(ValueError):
        tax.validate_config(bad, strict=True)


def test_validate_catches_undeclared_subtype_collision():
    # The tightened check (2026-07) covers sub-types too, not just the 11 calendar
    # categories — this is what would have caught the old 5-way Graphite pile-up.
    bad = copy.deepcopy(_default_cfg())
    bad["categories"]["broad_meeting"]["colorId"] = bad["categories"]["career"]["colorId"]
    assert any("collision" in p for p in tax.validate_config(bad, strict=False))


def test_frequency_rule_warns_on_two_high_freq_sharing():
    # A declared merge of two HIGH-frequency categories soft-warns when live counts
    # are supplied (frequency-aware allocation). With no frequency map: no warning.
    cfg = _default_cfg()
    assert not any("high-frequency" in p for p in tax.validate_config(cfg))
    # force both members of the [career, interview] merge above the threshold
    freq = {"career": 99, "interview": 99}
    warns = tax.validate_config(cfg, frequency=freq)
    assert any("high-frequency" in p for p in warns)


# ── loader mechanics ─────────────────────────────────────────────────────────
def test_deep_merge_is_per_key_recursive():
    base = {"categories": {"fitness": {"colorId": "2", "emoji": "A"}}}
    over = {"categories": {"fitness": {"emoji": "B"}}}
    merged = tax._deep_merge(base, over)
    assert merged["categories"]["fitness"] == {"colorId": "2", "emoji": "B"}


# ── gcal/config surface (robust to whatever instance config is present) ───────
def test_no_purple_on_work_calendar():
    """rules.forbidden_on_work must be enforced: on the work calendar (career
    default) a keyword hit like 'birthday'/'date night' must NOT land on a purple
    colorId — it recedes to Graphite. (Regression guard for the review's I1.)"""
    from gcal import calendar_maintenance as cm
    forbidden = set(tax.RULES.get("forbidden_on_work") or [])
    assert forbidden, "forbidden_on_work should be declared"
    for title in ["Sarah birthday lunch", "Date night", "anniversary dinner"]:
        _, color, _ = cm._desired_style({"summary": title}, {}, default_category="career")
        assert color not in forbidden, f"{title!r} got forbidden color {color}"


def test_personal_calendar_keeps_purple():
    """Purple is fine on personal calendars — the rule is work-only."""
    from gcal import calendar_maintenance as cm
    _, color, _ = cm._desired_style({"summary": "Sarah birthday lunch"}, {},
                                    default_category="household")
    assert color == tax.color_id("birthday")   # Lavender(1) — purple allowed here


def test_gcal_config_helpers_consistent():
    from gcal import config as gc
    ids = set(gc.calendar_ids())
    assert set(gc.calendar_ids("default")) | set(gc.calendar_ids("work")) <= ids
    assert gc.hard_color_only_calendars() <= ids
    for c in gc.calendars():
        assert "id" in c
