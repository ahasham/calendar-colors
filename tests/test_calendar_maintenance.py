#!/usr/bin/env python3
"""Tests for lib/gcal/calendar_maintenance.py build_maintenance_plan (pure).

Run: python3 -m pytest ~/.claude/lib/gcal/tests/test_calendar_maintenance.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent   # repo root (bundle layout)
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "calendar_maintenance", str(REPO / "gcal" / "calendar_maintenance.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def cm():
    return _load()


def test_restyle_drift_styles_bare_event(cm):
    ev = {"id": "e1", "summary": "Backyard BBQ",
          "start": {"dateTime": "2026-07-01T17:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]})
    assert len(plan["restyle"]) == 1
    r = plan["restyle"][0]
    assert r["new_summary"] == "🍖 Backyard BBQ"   # bbq content icon
    assert r["color_id"] == "4"                    # social color (Flamingo, 11-color standard)


def test_idempotent_already_styled_is_noop(cm):
    ev = {"id": "e1", "summary": "🍖 Backyard BBQ", "colorId": "7",
          "start": {"dateTime": "2026-07-01T17:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]})
    assert plan["restyle"] == []                   # has color → left alone


def test_colored_event_not_recomputed_or_downgraded(cm):
    # An event with a color but a "wrong"/different style is STILL left alone —
    # the routine never churns or downgrades existing categorizations.
    ev = {"id": "e1", "summary": "Quarterly offsite", "colorId": "9",  # career, set elsewhere
          "start": {"dateTime": "2026-07-01T10:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]})
    assert plan["restyle"] == []                   # color present → not touched


def test_true_same_calendar_duplicate_surfaced(cm):
    evs = [{"id": "a", "summary": "1:1", "start": {"dateTime": "2026-06-02T08:00:00-07:00"}},
           {"id": "b", "summary": "1:1", "start": {"dateTime": "2026-06-02T08:00:00-07:00"}}]
    plan = cm.build_maintenance_plan({"primary": evs})
    assert len(plan["dup_candidates"]) == 1
    assert set(plan["dup_candidates"][0]["event_ids"]) == {"a", "b"}


def test_cross_calendar_mirror_is_not_a_dup(cm):
    # SAME event id on both calendars (family event you attend → shows on primary)
    # is a mirror, NOT a deletable duplicate.
    ev = {"id": "same", "summary": "World Cup", "start": {"date": "2026-06-19"}}
    plan = cm.build_maintenance_plan({"primary": [ev], "family": [dict(ev)]})
    assert plan["dup_candidates"] == []
    assert len(plan["mirror_candidates"]) == 1
    assert set(plan["mirror_candidates"][0]["calendars"]) == {"primary", "family"}


def test_empty_title_skipped(cm):
    ev = {"id": "e", "summary": "", "start": {"date": "2026-06-01"}}
    plan = cm.build_maintenance_plan({"primary": [ev]})
    assert plan["restyle"] == []


def test_provenance_category_preferred_over_title(cm):
    # binding_class from a skill that created the event wins over title routing.
    ev = {"id": "e", "summary": "Strategy review",
          "extendedProperties": {"private": {"binding_class": "career"}},
          "start": {"dateTime": "2026-06-01T10:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]})
    assert plan["restyle"][0]["color_id"] == "9"   # career, from provenance


def test_recurring_restyled_at_master_once(cm):
    evs = [{"id": "i1", "summary": "Standup", "recurringEventId": "master",
            "start": {"dateTime": "2026-06-01T09:00:00-07:00"}},
           {"id": "i2", "summary": "Standup", "recurringEventId": "master",
            "start": {"dateTime": "2026-06-02T09:00:00-07:00"}}]
    plan = cm.build_maintenance_plan({"primary": evs})
    masters = [r for r in plan["restyle"] if r["event_id"] == "master"]
    assert len(masters) == 1                       # one update for the series


def test_recurring_row_carries_all_instance_ids_for_fallback(cm):
    # The invited-recurring apply fallback patches instances directly when the
    # master/`_R…` id 404s, so the row must collect EVERY in-window instance id.
    evs = [{"id": "i1", "summary": "Weekly OKR Status", "recurringEventId": "m_R1",
            "start": {"dateTime": "2026-06-08T15:00:00Z"}},
           {"id": "i2", "summary": "Weekly OKR Status", "recurringEventId": "m_R1",
            "start": {"dateTime": "2026-06-15T15:00:00Z"}},
           {"id": "i3", "summary": "Weekly OKR Status", "recurringEventId": "m_R1",
            "start": {"dateTime": "2026-06-22T15:00:00Z"}}]
    r = cm.build_maintenance_plan({"primary": evs})["restyle"][0]
    assert r["event_id"] == "m_R1"                       # master targeted first
    assert set(r["instance_ids"]) == {"i1", "i2", "i3"}  # all instances for fallback


def test_standalone_event_has_empty_instance_ids(cm):
    # Non-recurring events are patchable by their own id — no fallback needed.
    ev = {"id": "x", "summary": "Backyard BBQ",
          "start": {"dateTime": "2026-07-01T18:00:00-07:00"}}
    r = cm.build_maintenance_plan({"primary": [ev]})["restyle"][0]
    assert r["instance_ids"] == []


def test_from_gmail_event_is_color_only_no_summary_change(cm):
    # fromGmail events lock their summary (HTTP 400 on any title change) but
    # accept colorId — so the plan styles COLOR ONLY, leaving the title intact.
    ev = {"id": "g1", "summary": "Seattle Mariners vs. Toronto Blue Jays",
          "eventType": "fromGmail",
          "start": {"dateTime": "2026-07-05T13:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]})
    assert len(plan["restyle"]) == 1
    r = plan["restyle"][0]
    assert r["color_only"] is True
    assert r["new_summary"] == ev["summary"]   # title unchanged (no emoji prefix)
    assert r["color_id"]                        # but a category color is set


def test_normal_event_is_not_color_only(cm):
    ev = {"id": "n1", "summary": "Backyard BBQ",
          "start": {"dateTime": "2026-07-01T17:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]})
    assert plan["restyle"][0]["color_only"] is False
    assert plan["restyle"][0]["new_summary"] == "🍖 Backyard BBQ"


def test_unknown_title_falls_back_to_household(cm):
    assert cm.category_for_title("Resilient") == "household"
    assert cm.category_for_title("Dentist appt") == "health"
    assert cm.category_for_title("Soccer practice") == "school"


def test_distinct_timed_events_same_day_not_dup(cm):
    # Two REAL meetings titled "1:1" at 9am and 3pm same day are NOT duplicates
    # (date-only keying would wrongly flag them).
    evs = [{"id": "a", "summary": "1:1", "start": {"dateTime": "2026-06-02T09:00:00-07:00"}},
           {"id": "b", "summary": "1:1", "start": {"dateTime": "2026-06-02T15:00:00-07:00"}}]
    plan = cm.build_maintenance_plan({"primary": evs})
    assert plan["dup_candidates"] == []           # different start times → distinct


def test_recurring_instances_same_day_not_dup(cm):
    # Recurring series instances legitimately share a title; never flag as dup.
    evs = [{"id": "i1", "summary": "Standup", "recurringEventId": "m",
            "start": {"dateTime": "2026-06-01T09:00:00-07:00"}},
           {"id": "i2", "summary": "Standup", "recurringEventId": "m",
            "start": {"dateTime": "2026-06-01T09:00:00-07:00"}}]
    plan = cm.build_maintenance_plan({"primary": evs})
    assert plan["dup_candidates"] == []           # recurring excluded from dup map


def test_user_authored_leading_emoji_preserved(cm):
    # A title that already leads with one of our glyphs is an expressed user
    # preference: add COLOR only, leave the title byte-for-byte intact (don't
    # stack a second icon, don't override their choice).
    ev = {"id": "e", "summary": "🎉 Mom's birthday party",
          "start": {"dateTime": "2026-07-01T18:00:00-07:00"}}
    r = cm.build_maintenance_plan({"primary": [ev]})["restyle"][0]
    assert r["color_only"] is True
    assert r["new_summary"] == "🎉 Mom's birthday party"   # unchanged
    assert r["color_id"]                                    # color still applied


def test_leading_owned_emoji_not_overridden_by_different_icon(cm):
    # The canonical cross-emoji case: title leads with 🩺 but our content-icon
    # for "dentist" is 🦷. We must NOT produce "🦷 🩺 Dentist" (stacked) nor
    # swap their 🩺 for 🦷 — apply the health color only, title untouched.
    ev = {"id": "e", "summary": "🩺 Dentist",
          "start": {"dateTime": "2026-07-01T09:00:00-07:00"}}
    r = cm.build_maintenance_plan({"primary": [ev]})["restyle"][0]
    assert r["color_only"] is True
    assert r["new_summary"] == "🩺 Dentist"
    assert r["new_summary"].count("🩺") == 1 and "🦷" not in r["new_summary"]
    import taxonomy as t
    assert r["color_id"] == t.color_id("health")   # health color still applied


def test_no_double_icon_when_title_starts_with_our_icon(cm):
    # If the title already leads with the exact icon we'd add, don't double it.
    ev = {"id": "e", "summary": "🎂 Birthday cake order",
          "start": {"dateTime": "2026-07-01T18:00:00-07:00"}}
    r = cm.build_maintenance_plan({"primary": [ev]})["restyle"][0]
    assert r["new_summary"].count("🎂") == 1
    assert r["new_summary"] == "🎂 Birthday cake order"  # color-only, intact


def test_skip_ids_negative_cache_honored(cm):
    ev = {"id": "extern1", "summary": "External team meeting",
          "start": {"dateTime": "2026-06-10T10:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]}, skip_ids={"extern1"})
    assert plan["restyle"] == []                   # skipped — not re-proposed


def test_fetch_paginates(cm):
    # _fetch must follow nextPageToken, not silently truncate at one page.
    pages = {
        None: {"items": [{"id": "1"}], "nextPageToken": "p2"},
        "p2": {"items": [{"id": "2"}], "nextPageToken": "p3"},
        "p3": {"items": [{"id": "3"}]},  # no token → last page
    }
    def inv(tool, params):
        return pages[params.get("page_token")]
    out = cm._fetch(inv, ["primary"], "tmin", "tmax")
    assert [e["id"] for e in out["primary"]] == ["1", "2", "3"]


def test_fetch_reraises_auth_error_but_degrades_other(cm):
    class Auth(Exception):
        status = 401
    class NotFound(Exception):
        status = 404
    def inv_auth(tool, params):
        raise Auth("token expired")
    with pytest.raises(Auth):
        cm._fetch(inv_auth, ["primary"], "t", "t")   # auth → fatal, re-raised
    def inv_404(tool, params):
        raise NotFound("no such calendar")
    out = cm._fetch(inv_404, ["weird-cal"], "t", "t")  # per-cal → degrade to []
    assert out == {"weird-cal": []}


def test_skip_cache_roundtrip(cm, tmp_path):
    path = str(tmp_path / "skip.json")
    assert cm._load_skip(path) == set()
    cm._save_skip(path, set(), [{"event_id": "x", "reason": "HTTP 403"}])
    assert cm._load_skip(path) == {"x"}
    cm._save_skip(path, {"x"}, [{"event_id": "y", "reason": "HTTP 404"}])
    assert cm._load_skip(path) == {"x", "y"}        # merges, doesn't clobber


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── 11-color standard: full re-style + learned-rules + emoji-hint (2026-07-14) ──

def test_restyle_all_overrides_wrong_existing_color(cm):
    # A colored event whose color is WRONG for the standard is overridden in
    # --restyle-all mode (gap-fill would have skipped it).
    ev = {"id": "e1", "summary": "🏋️ Exercise", "colorId": "8",  # Graphite, wrong for fitness
          "start": {"dateTime": "2026-07-01T07:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]}, learned={}, restyle_all=True)
    assert len(plan["restyle"]) == 1
    assert plan["restyle"][0]["color_id"] == "10"   # fitness / Basil

def test_restyle_all_idempotent_when_correct(cm):
    ev = {"id": "e1", "summary": "🏋️ Exercise", "colorId": "10",
          "start": {"dateTime": "2026-07-01T07:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]}, learned={}, restyle_all=True)
    assert plan["restyle"] == []   # already matches → no churn

def test_restyle_all_preserves_category_via_emoji_hint(cm):
    # 🎒 school event with NO school keyword must NOT be downgraded to household.
    ev = {"id": "e1", "summary": "🎒 Meet & Greet", "colorId": "5",
          "start": {"dateTime": "2026-09-01T09:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]}, learned={}, restyle_all=True)
    assert plan["restyle"] == []   # stays school (Banana 5) via emoji hint

def test_learned_rule_overrides_keyword(cm):
    # "anniversary" keyword → partner, but a learned rule pins it to social.
    learned = {"nadia and ak anniversary": "social"}
    assert cm.classify_category("Nadia and AK Anniversary", learned) == "social"
    assert cm.classify_category("Nadia and AK Anniversary", {}) == "partner"

def test_fitness_and_birthday_keywords(cm):
    assert cm.classify_category("Yoga class", {}) == "fitness"
    assert cm.classify_category("Mom Bday", {}) == "birthday"
    assert cm.classify_category("World Cup FINAL", {}) == "social"


# ── Fresh-eyes review fixes: churn prevention (2026-07-14) ─────────────────────

def test_locked_eventtype_is_color_only(cm):
    # A Google-Contacts birthday (eventType=birthday) locks its summary — style
    # COLOR-ONLY so we never attempt a title write that would 400 every run.
    ev = {"id": "b1", "summary": "Mom", "eventType": "birthday",
          "start": {"date": "2026-09-01"}}
    plan = cm.build_maintenance_plan({"primary": [ev]}, learned={}, restyle_all=True)
    assert len(plan["restyle"]) == 1
    r = plan["restyle"][0]
    assert r["color_only"] is True
    assert r["new_summary"] == "Mom"          # title untouched

def test_working_location_is_ambient_graphite_not_travel_or_career(cm):
    # A workingLocation event ("Home"/"Office"/a city) is AMBIENT status. It must
    # route to the working_location sub-type (Graphite 8) — NOT travel's Tangerine
    # (6, reserved for flights now) and NOT the career default (Blueberry 9).
    # Locked eventType → color-only (bare place name, no emoji prepended).
    ev = {"id": "wl1", "summary": "Home", "eventType": "workingLocation",
          "start": {"date": "2026-09-01"}}
    plan = cm.build_maintenance_plan({"work@example.com": [ev]}, learned={},
                                     restyle_all=True, emoji_off=True,
                                     default_category="career")
    assert len(plan["restyle"]) == 1
    r = plan["restyle"][0]
    assert r["category"] == "working_location"
    assert r["color_id"] == "8"               # Graphite (ambient), not 6 or 9
    assert r["color_only"] is True
    assert r["new_summary"] == "Home"         # title untouched

def test_foreign_event_gets_emoji_optimistically(cm):
    # A colleague-organized meeting mirrored onto the PERSONAL calendar (you're an
    # attendee). We DO style it with an emoji: it's a private local override the
    # organizer never sees, and if they edit the event Google re-syncs (wiping it)
    # and the next run re-adds it. Only a summary-locked id is downgraded (below).
    foreign = {"id": "f1", "summary": "Coffee with Priya",
               "organizer": {"email": "priya@gmail.com"},   # organizer.self absent
               "attendees": [{"email": "priya@gmail.com"}, {"email": "me@x"}],
               "start": {"dateTime": "2026-07-01T09:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [foreign]}, learned={}, restyle_all=True)
    r = plan["restyle"][0]
    assert r["color_only"] is False                 # emoji attempted, not suppressed
    assert r["new_summary"].startswith("☕")         # social content icon prepended

def test_summary_locked_event_is_color_only(cm):
    # Once a title write has been REJECTED (id recorded in the summary-locked cache),
    # the event is styled color-only: its color still applies + updates, but we never
    # re-attempt the doomed title write (the churn fix for "Weekly OKR Status").
    locked = {"id": "L1", "summary": "Weekly OKR Status",
              "organizer": {"email": "boss@example.com"},
              "attendees": [{"email": "boss@example.com"}, {"email": "me@x"}],
              "start": {"dateTime": "2026-07-01T09:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [locked]}, learned={},
                                     restyle_all=True, summary_locked_ids={"L1"})
    r = plan["restyle"][0]
    assert r["color_only"] is True
    assert r["new_summary"] == "Weekly OKR Status"   # NO emoji
    assert r["color_id"] == "2"                       # household (Sage) still colored

def test_summary_locked_matches_recurring_master(cm):
    # The lock is keyed on the STABLE master id: a recurring invited series whose
    # per-run instance ids differ must still be recognized as locked (else it churns
    # forever — the original bug). Master id 'M' is locked; the instance is 'M_R123'.
    inst = {"id": "M_R20260701", "recurringEventId": "M", "summary": "Team broadcast",
            "organizer": {"email": "boss@example.com"},
            "attendees": [{"email": f"p{i}@example.com"} for i in range(20)],
            "start": {"dateTime": "2026-07-01T09:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [inst]}, learned={},
                                     restyle_all=True, summary_locked_ids={"M"})
    r = plan["restyle"][0]
    assert r["color_only"] is True                    # matched via master id
    assert r["new_summary"] == "Team broadcast"

def test_apply_records_recurring_rejection_into_summary_locked(cm, tmp_path, monkeypatch):
    # END-TO-END of the CHURN FIX (apply-path recording). An invited recurring
    # master rejects the write (404); instances get colored; the MASTER id is
    # written to the summary-locked cache so the NEXT run styles it color-only and
    # never re-attempts the doomed title write. The consumption-side tests only
    # verify build_maintenance_plan honoring the cache — this covers the write.
    from gcal import rest as _rest

    def stub_make_invoker(token):
        def inv(tool, payload):
            if payload.get("event_id") == "MASTER":       # master un-patchable
                err = Exception("not found"); err.status = 404
                raise err
            return {"id": payload.get("event_id")}         # instance color-only OK
        return inv

    events = {"primary": [{
        "id": "MASTER_R20260720", "recurringEventId": "MASTER",
        "summary": "Team broadcast", "organizer": {"email": "boss@example.com"},
        "attendees": [{"email": f"p{i}@example.com"} for i in range(20)],
        "start": {"dateTime": "2026-07-20T09:00:00-07:00"}}]}
    monkeypatch.setattr(_rest, "make_invoker", stub_make_invoker)
    monkeypatch.setattr(cm, "_fetch", lambda inv, cals, tmin, tmax: events)
    monkeypatch.setattr(cm, "register_dup_obligations", lambda *a, **k: [])

    locked_path = tmp_path / "locked.json"
    rc = cm.main(["--calendars", "primary", "--restyle-all", "--apply",
                  "--feature-config", "none",
                  "--skip-cache", str(tmp_path / "skip.json"),
                  "--summary-locked-cache", str(locked_path)])
    assert rc == 0
    import json
    saved = json.loads(locked_path.read_text())
    assert any(x["event_id"] == "MASTER" for x in saved.get("locked", [])), \
        "rejected recurring master must be recorded in the summary-locked cache"
    # and a second run now treats it color-only (no re-propose): the churn is gone
    locked_ids = cm._load_summary_locked(str(locked_path))
    plan = cm.build_maintenance_plan(events, restyle_all=True,
                                     summary_locked_ids=locked_ids)
    assert plan["restyle"][0]["color_only"] is True

def test_work_calendar_emoji_off_is_color_only(cm):
    # The work calendar runs with --emoji-off (build_maintenance_plan emoji_off=True):
    # colors are applied but titles never get an emoji. Color only, always.
    ev = {"id": "w1", "summary": "1:1 with Sam", "organizer": {"self": True},
          "start": {"dateTime": "2026-07-01T09:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"work@example.com": [ev]}, learned={},
                                     restyle_all=True, default_category="career",
                                     emoji_off=True, feature_config=None)
    r = plan["restyle"][0]
    assert r["color_only"] is True
    assert r["new_summary"] == "1:1 with Sam"       # no emoji on work, ever

def test_owned_event_still_gets_emoji(cm):
    # Control: your OWN event (organizer.self True) with attendees stays fully
    # editable — the fix must not over-trigger and strip emoji from real events.
    owned = {"id": "o1", "summary": "Dentist appt",
             "organizer": {"self": True},
             "attendees": [{"email": "me@x"}],
             "start": {"dateTime": "2026-07-01T09:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [owned]}, learned={}, restyle_all=True)
    r = plan["restyle"][0]
    assert r["color_only"] is False
    # an emoji IS prepended (content icon 🦷 overrides the 🩺 category glyph, as designed)
    assert r["new_summary"].startswith("🦷")
    assert r["color_id"] == "10"                 # health = Basil

def test_solo_event_no_attendees_still_editable(cm):
    # organizer.self can be absent on a solo single-user event — with NO attendees
    # it is still yours, so it must remain editable (emoji prepended), not color-only.
    solo = {"id": "s1", "summary": "Gym session",
            "start": {"dateTime": "2026-07-01T07:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [solo]}, learned={}, restyle_all=True)
    r = plan["restyle"][0]
    assert r["color_only"] is False
    assert r["new_summary"].startswith("🏋")

def _work_ev(summary, n_attendees=0, **kw):
    e = {"id": "x", "summary": summary, "start": {"date": "2026-09-01"}, **kw}
    if n_attendees:
        e["attendees"] = [{"email": f"p{i}@example.com"} for i in range(n_attendees)]
    return e

def test_work_meeting_refinement(cm):
    import taxonomy as t
    ln = {}
    def cat(ev):
        return cm._desired_style(ev, ln, "career")[0]   # work-calendar context
    # interview by keyword (any size) — Blueberry (navy, with career), detected in classify_category
    assert cat(_work_ev("Deep Dive interview - Wally", 5)) == "interview"
    assert cat(_work_ev("Phone screen - candidate", 2)) == "interview"
    # broad by attendee count (≥16 = broadcasts) — Blueberry (navy)
    assert cat(_work_ev("Customer Update Meeting", 60)) == "broad_meeting"
    assert cat(_work_ev("Weekly OKR Status", 17)) == "broad_meeting"
    # broad by broadcast keyword even when the attendee list is tiny (All Hands)
    assert cat(_work_ev("All Hands- Please have camera on", 1)) == "broad_meeting"
    # ≤16-person TEAM meetings stay regular (below the 17 broadcast threshold).
    # 16 is the boundary: a working pod meeting at exactly 16 must stay blue.
    assert cat(_work_ev("Mapping standup pod", 15)) == "regular_meeting"
    assert cat(_work_ev("Acme AI Showcase", 10)) == "regular_meeting"
    assert cat(_work_ev("Schedule pod weekly planning", 16)) == "regular_meeting"
    # small meetings → regular_meeting (Lavender, light)
    assert cat(_work_ev("Sam / Lee - 60 day check-in", 2)) == "regular_meeting"
    assert cat(_work_ev("Building subzones for Eli Lilly", 5)) == "regular_meeting"
    # colors resolve via taxonomy (no-purple-on-work): regular meetings=Peacock
    # (7, the only clean blue), big meetings recede to Graphite(8 gray), interviews
    # = Blueberry(9 navy — with career, the professional bucket; interviews ARE hiring
    # events). Navy is allowed on work; the periwinkle/magenta purples are not.
    assert cm._desired_style(_work_ev("Customer Update Meeting", 60), ln, "career")[1] == "8"   # broad → Graphite
    assert cm._desired_style(_work_ev("Sam / Lee 1:1", 2), ln, "career")[1] == "7"            # regular → Peacock
    assert cm._desired_style(_work_ev("Deep Dive interview", 3), ln, "career")[1] == "9"        # interview → Blueberry
    # NO PURPLE FOR MEETINGS on work: regular/broad meetings never use Lavender(1) or
    # Grape(3) [periwinkle/magenta] or Blueberry(9) [navy]. interview is the exception —
    # it deliberately carries Blueberry(9, navy) as the career/professional color.
    for c in ("regular_meeting", "broad_meeting"):
        assert t.color_id(c) not in ("1", "3", "9"), c
    assert t.color_id("interview") == "9"          # interview = career's Blueberry (navy)
    assert "9" not in (t.RULES.get("forbidden_on_work") or [])  # navy allowed on work

def test_interview_detected_from_description_ats_link(cm):
    # A candidate interview you RUN is titled just "Name - Role" (no keyword); the
    # signal is the ATS interview link in the DESCRIPTION. Must classify as
    # interview (Blueberry 9) on the work calendar, not regular_meeting.
    ev = {"id": "e1", "summary": "DC Miles - Product Manager - Construction AI",
          "description": "SCHEDULE\n1:00-2:00pm\nView resume and leave feedback: "
                         "https://hire.lever.co/interviews/bfb620ba",
          "attendees": [{"email": "x@example.com"}],
          "start": {"dateTime": "2026-07-17T13:00:00-07:00"}}
    cat, color, _ = cm._desired_style(ev, {}, "career")
    assert cat == "interview" and color == "9"  # Blueberry (career/professional)
    # same signal works on a PERSONAL calendar too (your own interviews), and with
    # no signal a plain title stays a normal meeting.
    assert cm._desired_style(ev, {}, "household")[0] == "interview"
    plain = {"summary": "DC Miles - Product Manager - Construction AI",
             "attendees": [{"email": "x@example.com"}]}
    assert cm._desired_style(plain, {}, "career")[0] == "regular_meeting"

def test_work_refinement_scoped_to_work_calendar(cm):
    # a big personal gathering (many attendees) must NOT become a work 'broad
    # meeting' — refinement is gated on the career default (the work calendar).
    ev = _work_ev("Wedding reception", 120)
    assert cm._desired_style(ev, {}, "household")[0] == "social"   # not broad_meeting
    # a personal event hitting a work keyword stays put too
    assert cm._desired_style(_work_ev("Team standup", 15), {}, "household")[0] == "career"

def test_health_deepwork_color_swap(cm):
    import taxonomy as t
    assert t.color_id("health") == "10"       # Basil (calm), was Tomato
    assert t.color_id("deep_work") == "11"    # Tomato (blocked), was Basil
    assert t.emoji("deep_work") == "🧠"
    # a real flight still routes to travel/Tangerine (unchanged by the split)
    assert cm.category_for_title("SEA (Seattle) to SFO (San Francisco)") == "travel"

def test_vs16_roundtrip_is_not_drift(cm):
    # Client dropped the U+FE0F selector on round-trip ("🏋 Exercise"); the event
    # is already fitness-colored. Must NOT be re-proposed (VS16-tolerant compare).
    ev = {"id": "e1", "summary": "🏋 Exercise", "colorId": "10",
          "start": {"dateTime": "2026-07-01T07:00:00-07:00"}}
    plan = cm.build_maintenance_plan({"primary": [ev]}, learned={}, restyle_all=True)
    assert plan["restyle"] == []              # no phantom churn

def test_demo_keyword_no_longer_matches_democracy(cm):
    assert cm.category_for_title("Democracy talk") == "household"   # not career
    assert cm.category_for_title("Product demo call") == "career"   # real demo still routes

def test_run_keyword_no_longer_overfires(cm):
    # " run" used to hit every "<errand> run"; now only exercise-specific run
    # PHRASES route to fitness. The errand/social/travel ones fall elsewhere.
    assert cm.category_for_title("Grocery run") == "errands"
    assert cm.category_for_title("School run") == "school"
    assert cm.category_for_title("airport run") == "travel"
    assert cm.category_for_title("raise 10k") != "fitness"     # money, not a race
    # real workouts still classify + still get the 🏃 icon
    import calendar_icons as ci
    for t in ("Morning run", "Trail run", "5k race", "Terry Fox Run", "Marathon training"):
        assert cm.category_for_title(t) == "fitness", t
        assert ci.content_icon(t) == "🏃", t

def test_flight_route_anchored_no_midsentence_false_positive(cm):
    # anchored to title start → a mid-sentence handoff of the same shape is NOT travel
    assert cm.category_for_title("Send RFP (draft) to CEO (review)") == "household"
    assert cm.category_for_title("the ABC (x) to DEF (y) memo") == "household"

def test_gmail_reservation_prefixes_route(cm):
    # Google-imported lodging → travel; dining reservations → social (the venue
    # name carries no keyword; the PREFIX is the signal). Discovered via a
    # --learn-unknowns census where these sat in the household fallback.
    assert cm.category_for_title("Stay at W San Francisco") == "travel"
    assert cm.category_for_title("Stay at Hanna Farmhouse") == "travel"
    assert cm.category_for_title("Reservation at Casa Gabriele") == "social"
    assert cm.category_for_title("Reservation at Malibu Farm Restaurant") == "social"
    # documented edge: a campsite reservation lands in social (1 rare event, accepted)
    assert cm.category_for_title("Reservation at Twanoh State Park- Site#T43") == "social"

def test_reservation_prefixes_anchored_no_midtitle_false_positive(cm):
    # anchored to the start → mid-title "stay at"/"reservation at" must NOT route
    assert cm.category_for_title("Field day - kids stay at school") == "school"
    assert cm.category_for_title("Please stay at your desk till 5") == "household"
    assert cm.category_for_title("Make a reservation at the DMV") == "household"
    # real routes (bare, or with a leading emoji) still travel
    assert cm.category_for_title("SEA (Seattle) to SFO (San Francisco)") == "travel"
    assert cm.category_for_title("✈️ ORD (Chicago) to YYZ (Toronto)") == "travel"
    # a "Flight:"-prefixed route still routes (via the 'flight' keyword)
    assert cm.category_for_title("Flight: SEA (Seattle) to ORD (Chicago)") == "travel"

def test_feature_config_governs_color_only_per_calendar(cm):
    # emoji: color_only in config → title untouched WITHOUT relying on a
    # description marker; emoji: true → glyph prepended. Closes the events-cal
    # marker dependency.
    fc = {"__defaults__": {"color": True, "emoji": True},
          "eventsCal": {"color": True, "emoji": "color_only"},
          "primary": {"color": True, "emoji": True}}
    occ = {"id": "o1", "summary": "Someone Anniversary",
           "start": {"date": "2026-09-01"}, "colorId": None}  # NO 'Managed by /'
    plan = cm.build_maintenance_plan({"eventsCal": [occ]}, learned={},
                                     restyle_all=True, feature_config=fc)
    assert plan["restyle"][0]["color_only"] is True
    assert plan["restyle"][0]["new_summary"] == "Someone Anniversary"
    ev = {"id": "p1", "summary": "Morning run",
          "start": {"date": "2026-09-01"}, "colorId": None}
    plan2 = cm.build_maintenance_plan({"primary": [ev]}, learned={},
                                      restyle_all=True, feature_config=fc)
    assert plan2["restyle"][0]["color_only"] is False
    assert plan2["restyle"][0]["new_summary"].startswith("🏃")

def test_feature_config_default_category_per_calendar(cm):
    # a calendar whose config sets default_category=career → unclassifiable event
    # lands in the work-meeting family (refined to regular_meeting for a small/no-
    # attendee event), NOT household — without a global CLI flag.
    fc = {"__defaults__": {"color": True, "emoji": True},
          "workCal": {"color": True, "emoji": "color_only", "default_category": "career"}}
    ev = {"id": "w1", "summary": "Zzzqqq Wibble",   # truly unclassifiable
          "start": {"date": "2026-09-01"}, "colorId": None}
    plan = cm.build_maintenance_plan({"workCal": [ev]}, learned={},
                                     restyle_all=True, feature_config=fc)
    assert plan["restyle"][0]["category"] == "regular_meeting"   # work family, not household

def test_dr_appointment_context_is_health(cm):
    # "dr"/"dr." routes to health ONLY with appointment context...
    for t in ("Dr appointment", "Dr. appt", "Dr visit", "Dr. Patel appointment",
              "Dr Patel visit", "Dr's office", "appt to the Dr",
              "Schedule visit to Dr"):
        assert cm.category_for_title(t) == "health", t
    # ...never as an honorific in a name, nor via substring collisions
    for t in ("Dr. Seuss story time", "Dr. Dre concert", "Dr Patel",
              "Dr Smith retirement party", "office party", "hundred days",
              "children pickup", "address change", "Andrew's birthday"):
        assert cm.category_for_title(t) != "health", t

def test_health_lexicon_expanded(cm):
    for t in ("Physio session", "Optometrist", "Eye exam", "Pediatric checkup",
              "Mammogram", "X-ray", "MRI scan", "CT scan", "Pharmacy pickup",
              "Flu shot", "Colonoscopy prep", "Blood test"):
        assert cm.category_for_title(t) == "health", t
    # "mri scan" (not bare "mri") so a name like "Amrita" is unaffected
    assert cm.category_for_title("Amrita birthday") == "birthday"

def test_karate_keeps_activity_glyph(cm):
    # after reverting the override: School color (learned) but the 🥋 activity
    # content glyph (color signals the partition; emoji signals the activity)
    learned = {"kid's karate": "school"}
    ev = {"id": "k1", "summary": "🥋 Kid's Karate", "colorId": "5",
          "start": {"date": "2026-09-01"}}
    cat, color, icon = cm._desired_style(ev, learned, "household", {})
    assert cat == "school" and color == "5" and icon == "🥋"

def test_learned_icon_override_beats_content_icon(cm):
    # A learned rule with an explicit `icon` wins over the content icon, so a
    # School-colored karate event shows 🎒 (not the fitness 🥋).
    learned = {"kid's karate": "school"}
    icons = {"kid's karate": "🎒"}
    ev = {"id": "k1", "summary": "🥋 Kid's Karate", "colorId": "5",
          "start": {"date": "2026-09-01"}}
    cat, color, icon = cm._desired_style(ev, learned, "household", icons)
    assert cat == "school"
    assert icon == "🎒"                       # override, not 🥋
    # and the restyle swaps the leading 🥋 for 🎒
    plan = cm.build_maintenance_plan({"shared": [ev]}, learned=learned,
                                     learned_icons=icons, restyle_all=True)
    assert plan["restyle"][0]["new_summary"] == "🎒 Kid's Karate"

def test_hold_is_word_boundary_not_substring(cm):
    # bare "HOLD" block → deep_work, but words CONTAINING hold must not
    assert cm.category_for_title("HOLD") == "deep_work"
    assert cm.category_for_title("[HOLD] focus") == "deep_work"
    assert cm.category_for_title("Hold: strategy") == "deep_work"
    for t in ("household chores", "Placeholder meeting", "Stakeholder sync",
              "Shareholder update"):
        assert cm.category_for_title(t) != "deep_work", t
    # fitness still wins over a HOLD prefix (checked earlier in the loop)
    assert cm.category_for_title("[HOLD] karate") == "fitness"

def test_flight_route_pattern_is_travel(cm):
    # "CODE (City) to CODE (City)" auto-synced flight itineraries route to travel
    # regardless of whether the airport code is in the keyword list — previously
    # only SFO/YYZ/SEA-TAC landed on travel by luck; ORD/DFW fell to the default.
    for t in ("SEA (Seattle) to SFO (San Francisco)",
              "SEA (Seattle) to ORD (Chicago)",
              "DFW (Dallas / Fort Worth) to SEA (Seattle)"):
        assert cm.category_for_title(t) == "travel", t
    # must NOT fire on ordinary prose (lowercase / no code+paren shape)
    assert cm.category_for_title("Walk from home to the park") == "household"
    assert cm.category_for_title("Hand off ORD to Priya") == "household"
