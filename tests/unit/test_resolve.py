"""Matching names as written in messages to table rows (T022)."""

from pathlib import Path

import pytest

from knowledge_graph_agent.extract.messages import sender_identity
from knowledge_graph_agent.extract.resolve import (
    ReferenceData,
    resolve_class,
    resolve_port,
    resolve_vessel,
    resolve_voyage,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module")
def ref():
    return ReferenceData.from_data(DATA_DIR)


@pytest.mark.parametrize(
    "surface, vessel_id, method",
    [
        ("IMO 9876543", "V001", "imo"),
        ("VOY 1042", "V001", "voyage_number"),
        ("voyage 0981", "V001", "voyage_number"),
        ("M/V OCEAN STAR", "V001", "name"),
        ("MV. Golden Wake", "V013", "name"),
        ("Ocean Starlight", "V003", "name"),
    ],
)
def test_vessel_references(ref, surface, vessel_id, method):
    r = resolve_vessel(surface, ref)
    assert (r.entity_id, r.method) == (vessel_id, method)


@pytest.mark.parametrize("surface", ["VOY 1042", "voyage 1042", "VG-1042", "1042"])
def test_voyage_references(ref, surface):
    assert resolve_voyage(surface, ref).entity_id == "VG-1042"


def test_unknown_voyage_stays_unresolved(ref):
    assert not resolve_voyage("VOY 9999", ref).resolved


def test_ocean_starlight_never_matches_ocean_star(ref):
    for surface in ["Ocean Starlight", "OCEAN STARLIGHT", "MV Ocean Starlight (IMO 9654321)"]:
        assert resolve_vessel(surface, ref).entity_id == "V003"


def test_abbreviation_needs_the_full_name_in_the_message(ref):
    with_name = resolve_vessel("OS bridge", ref, context="From: 2nd Officer, Ocean Star (via crew WhatsApp relay)")
    assert (with_name.entity_id, with_name.method) == ("V001", "abbreviation")
    assert with_name.confidence == 0.7

    without = resolve_vessel("OS bridge", ref, context="From: 2nd Officer (via crew WhatsApp relay)")
    assert not without.resolved


def test_voyage_and_vessel_that_disagree_stay_unresolved(ref):
    r = resolve_vessel("Ocean Pearl VOY 1042", ref)  # VG-1042 is Ocean Star's voyage
    assert not r.resolved
    assert "disagree" in r.note


def test_unknown_imo_stays_unresolved(ref):
    assert not resolve_vessel("IMO 1234567", ref).resolved


@pytest.mark.parametrize(
    "surface, code", [("NLRTM", "NLRTM"), ("Rotterdam", "NLRTM"), ("Antwerp", "BEANR"), ("Port Said", "EGPSD")]
)
def test_ports(ref, surface, code):
    assert resolve_port(surface, ref).entity_id == code


def test_port_area_not_in_the_table_stays_unresolved(ref):
    assert not resolve_port("Maas anchorage", ref).resolved


def test_classes_match_exactly(ref):
    assert resolve_class("Meridian 8500", ref) == "Meridian 8500"
    assert resolve_class("Meridian 8500 class", ref) == "Meridian 8500"
    assert resolve_class("Meridian", ref) is None


# Forms used in messages/extra (T075)


def test_imo_with_a_voyage_number_beside_it(ref):
    text = "IMO 9743143 (voyage 2033)"
    assert (resolve_vessel(text, ref).entity_id, resolve_vessel(text, ref).method) == ("V022", "imo")
    voyage = resolve_voyage("voyage 2033", ref)
    assert voyage.entity_id == "VG-2033"
    assert ref.voyages["VG-2033"].vessel_id == "V022"  # the two agree


def test_master_named_only_by_imo(ref):
    assert sender_identity("Master, IMO 9407175", ref) == ("master", "vessel:V024", "V024")
    assert resolve_vessel("Noon report IMO 9407175", ref).entity_id == "V024"
