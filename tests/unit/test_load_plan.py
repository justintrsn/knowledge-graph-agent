"""Turning the CSVs into nodes and links, with no database involved."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_graph_agent.graph.load_tables import (
    build_plan,
    customer_id,
    incident_origin,
    normalize_booking_status,
    normalize_vessel_name,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module")
def plan():
    return build_plan(DATA_DIR)


def by_id(plan, label):
    return {n["evidence_id"]: n for n in plan.nodes[label]}


def test_row_counts(plan):
    counts = {label: len(nodes) for label, nodes in plan.nodes.items()}
    assert counts == {
        "Vessel": 40,
        "Port": 15,
        "Voyage": 121,
        "PortCall": 231,
        "Incident": 90,
        "CongestionSnapshot": 578,
        "Booking": 400,
        "Customer": counts["Customer"],
    }
    assert sum(n for label, n in counts.items() if label != "Customer") == 1475
    assert {s.path: s.row_or_file_count for s in plan.source_files}["bookings.csv"] == 400


def test_evidence_ids_are_unique(plan):
    ids = [n["evidence_id"] for nodes in plan.nodes.values() for n in nodes]
    assert len(ids) == len(set(ids))


def test_vg1042(plan):
    v = by_id(plan, "Voyage")["VG-1042"]
    assert v["departure_utc"] == datetime(2026, 9, 29, 22, 0, tzinfo=UTC)
    assert v["original_eta_utc"] == datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
    assert v["revised_eta_utc"] == datetime(2026, 10, 27, 1, 0, tzinfo=UTC)
    assert (v["revised_eta_utc"] - v["original_eta_utc"]).total_seconds() / 3600 == 55
    assert v["source_ref"] == "voyages.csv:3"


def test_every_recorded_delay_matches_utc_etas(plan):
    for v in plan.nodes["Voyage"]:
        elapsed = (v["revised_eta_utc"] - v["original_eta_utc"]).total_seconds() / 3600
        assert elapsed == v["recorded_delay_hours"], v["evidence_id"]


def test_blanks_stay_null(plan):
    assert by_id(plan, "Incident")["INC-202"]["attributed_delay_hours"] is None
    assert by_id(plan, "PortCall")["PC-7003"]["berthing_utc"] is None


def test_rolled_to_links(plan):
    rolled = [(r.start, r.end) for r in plan.relationships if r.type == "ROLLED_TO"]
    assert rolled == [("BK-50008", "VG-1107")]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Confirmed", ("confirmed", None)),
        ("Cancelled", ("cancelled", None)),
        ("Rolled to next sailing", ("rolled", None)),
        ("Rolled to VG-1107", ("rolled", "VG-1107")),
    ],
)
def test_normalize_booking_status(raw, expected):
    assert normalize_booking_status(raw) == expected


def test_unknown_booking_status_fails():
    with pytest.raises(ValueError):
        normalize_booking_status("Pending")


def test_names():
    assert customer_id("Halden Retail") == "CUST-halden-retail"
    assert normalize_vessel_name("MV Ocean Star") == "ocean star"
    assert normalize_vessel_name("MV Ocean Starlight") != normalize_vessel_name("MV Ocean Star")


def test_incident_origin():
    assert incident_origin("Master's report", "V001", "X") == "vessel:V001"
    assert incident_origin("Crew message", "V001", "X") == "vessel:V001"
    assert incident_origin("Technical manager", "V001", "Harbourline Ship Management") == (
        "org:Harbourline Ship Management"
    )
    assert incident_origin("Port agent", "V001", "X") == "unknown"
