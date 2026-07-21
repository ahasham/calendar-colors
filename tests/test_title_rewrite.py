#!/usr/bin/env python3
"""Tests for gcal/title_rewrite.py — the no-info-loss + risk gates."""
import importlib.util, sys
from pathlib import Path
# Layout-agnostic: find the dir holding taxonomy.py (repo root in the public
# bundle, lib/ in the monorepo) so this file is byte-identical + syncable in both.
_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "taxonomy.py").exists())
sys.path.insert(0, str(_ROOT))
spec = importlib.util.spec_from_file_location("tr", str(_ROOT / "gcal" / "title_rewrite.py"))
tr = importlib.util.module_from_spec(spec); spec.loader.exec_module(tr)


def test_reject_dropped_parenthetical():
    d = tr.decide("🎂 Pops (52 G) Bday", "🎂 Pops — Birthday", 0.95)
    assert d["verdict"] == "reject" and any("52" in m for m in d["missing"])

def test_reject_dropped_flight_code():
    d = tr.decide("Flight to Seattle (AC 541)", "Flight to Seattle", 0.95)
    assert d["verdict"] == "reject"

def test_reject_dropped_acronyms():
    d = tr.decide("✈️ DS YYZ dates", "✈️ Toronto trip dates", 0.95)
    assert d["verdict"] == "reject"   # DS + YYZ vanished

def test_review_sensitive_even_if_valid():
    d = tr.decide("💜 Marriage: Sex", "💜 Marriage: Intimacy", 0.99)
    assert d["verdict"] == "review"   # sensitive → never auto

def test_review_command_ref():
    d = tr.decide("💜 Sunday Self-Score + /marriage (private)",
                  "💜 Sunday Self-Score + /marriage (private) review", 0.99)
    assert d["verdict"] == "review"   # has /command + (private)

def test_apply_safe_chore():
    d = tr.decide("🏠 Adam to replace furnace filter", "🏠 Replace furnace filter", 0.95)
    assert d["verdict"] == "apply"

def test_review_low_confidence():
    d = tr.decide("🍷 gautam in chicago", "🍷 Gautam visiting Chicago", 0.5)
    assert d["verdict"] == "review"

def test_noop_when_unchanged():
    assert tr.decide("🏋️ Exercise", "🏋️ Exercise", 0.99)["verdict"] == "noop"

def test_proper_noun_preserved_required():
    # dropping the person's name = loss
    assert tr.decide("🍷 Gautam in Chicago", "🍷 Someone visiting", 0.95)["verdict"] == "reject"


def test_self_name_kept_when_event_has_attendees():
    # Adam kept on invites (others need to know whose event) → dropping = reject
    d = tr.decide("💼 Adam / Shim catch-up", "💼 Shim Catch-up", 0.95, has_attendees=True)
    assert d["verdict"] == "reject" and "adam" in d["missing"]

def test_self_name_droppable_on_solo_event():
    d = tr.decide("🏠 Adam to replace furnace filter", "🏠 Replace furnace filter",
                  0.95, has_attendees=False)
    assert d["verdict"] == "apply"
