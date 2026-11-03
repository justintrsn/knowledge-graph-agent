"""Loading the tables into a real Neo4j (T017). Note: this clears the graph."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from knowledge_graph_agent.graph.db import read_session
from knowledge_graph_agent.graph.load_tables import load_tables
from knowledge_graph_agent.graph.verify import all_passed, verify

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# SC-001
EXPECTED_COUNTS = {
    "Vessel": 40,
    "Port": 15,
    "Voyage": 121,
    "PortCall": 231,
    "Incident": 90,
    "CongestionSnapshot": 578,
    "Booking": 400,
}


@pytest.fixture(scope="module")
def loaded(driver):
    return load_tables(driver, DATA_DIR)


def one(driver, query, **params):
    with read_session(driver) as session:
        return session.run(query, **params).single(strict=True)


def native(value):
    return None if value is None else value.to_native()


def test_counts_per_label_match_sc001(driver, loaded):
    for label, expected in EXPECTED_COUNTS.items():
        assert one(driver, f"MATCH (n:{label}) RETURN count(n) AS n")["n"] == expected, label
    assert sum(EXPECTED_COUNTS.values()) == 1475
    assert one(driver, "MATCH (n:SourceFile) RETURN count(n) AS n")["n"] == 7
    assert loaded.node_counts["Customer"] == one(driver, "MATCH (c:Customer) RETURN count(c) AS n")["n"]


def test_vg1042_times_are_utc_and_delay_is_55h(driver, loaded):
    v = one(driver, "MATCH (v:Voyage {evidence_id: 'VG-1042'}) RETURN v")["v"]
    # Departure in Singapore (UTC+8); ETAs and berth window in Rotterdam,
    # which leaves summer time at 03:00 on 25 Oct.
    assert native(v["departure_utc"]) == datetime(2026, 9, 29, 22, 0, tzinfo=UTC)
    assert native(v["original_eta_utc"]) == datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
    assert native(v["revised_eta_utc"]) == datetime(2026, 10, 27, 1, 0, tzinfo=UTC)
    assert native(v["berth_window_start_utc"]) == datetime(2026, 10, 24, 16, 0, tzinfo=UTC)
    assert native(v["berth_window_end_utc"]) == datetime(2026, 10, 25, 4, 0, tzinfo=UTC)
    assert v["original_eta_local"] == "2026-10-24 20:00"
    assert v["revised_eta_local"] == "2026-10-27 02:00"
    assert v["recorded_delay_hours"] == 55

    hours = one(
        driver,
        "MATCH (v:Voyage {evidence_id: 'VG-1042'}) "
        "RETURN duration.inSeconds(v.original_eta_utc, v.revised_eta_utc).seconds / 3600.0 AS h",
    )["h"]
    assert hours == 55


def test_blank_attributed_delay_stays_null(driver, loaded):
    i = one(driver, "MATCH (i:Incident {evidence_id: 'INC-202'}) RETURN i")["i"]
    assert i["attributed_delay_hours"] is None
    assert one(driver, "MATCH (i:Incident {evidence_id: 'INC-201'}) RETURN i")["i"][
        "attributed_delay_hours"
    ] == 12


def test_non_ascii_names_are_intact(driver, loaded):
    v = one(driver, "MATCH (v:Vessel {evidence_id: 'V009'}) RETURN v")["v"]
    assert v["technical_manager"] == "Sulamar Gestão Naval"


def test_rolled_to_only_when_status_names_a_voyage(driver, loaded):
    vague = one(
        driver,
        "MATCH (b:Booking {status_raw: 'Rolled to next sailing'}) "
        "RETURN count(b) AS n, collect(DISTINCT b.status_normalized) AS statuses",
    )
    assert vague["n"] == 38
    assert vague["statuses"] == ["rolled"]

    linked = one(
        driver,
        "MATCH (b:Booking)-[:ROLLED_TO]->(v:Voyage) "
        "RETURN collect(b.evidence_id) AS bookings, collect(v.evidence_id) AS voyages",
    )
    assert linked["bookings"] == ["BK-50008"]
    assert linked["voyages"] == ["VG-1107"]


def test_rolled_to_next_sailing_has_no_link(driver, loaded):
    n = one(
        driver,
        "MATCH (b:Booking {status_raw: 'Rolled to next sailing'})-[:ROLLED_TO]->() "
        "RETURN count(b) AS n",
    )["n"]
    assert n == 0


def test_source_refs_point_at_csv_lines(driver, loaded):
    assert one(driver, "MATCH (i:Incident {evidence_id: 'INC-201'}) RETURN i.source_ref AS r")["r"] == (
        "incidents.csv:2"
    )
    assert one(driver, "MATCH (c:Customer {name: 'Halden Retail'}) RETURN c.evidence_id AS id")["id"] == (
        "CUST-halden-retail"
    )
    snapshot = one(driver, "MATCH (c:CongestionSnapshot {evidence_id: 'CONG-SGSIN-2026-09-17'}) RETURN c")["c"]
    assert native(snapshot["date"]) == date(2026, 9, 17)


def test_incident_origins(driver, loaded):
    with read_session(driver) as session:
        rows = {
            r["id"]: r["origin"]
            for r in session.run(
                "MATCH (i:Incident) WHERE i.evidence_id IN ['INC-201', 'INC-202', 'INC-203'] "
                "RETURN i.evidence_id AS id, i.origin AS origin"
            )
        }
    # Master and crew of V001 share an origin; the agent is unnamed.
    assert rows == {"INC-201": "vessel:V001", "INC-202": "vessel:V001", "INC-203": "unknown"}


def test_table_checks_pass(driver, loaded):
    results = verify(driver, DATA_DIR)
    assert all_passed(results), [r for r in results if r.status == "fail"]
    statuses = {r.name: r.status for r in results}
    assert statuses["fact_quotes"] == statuses["messages"] == "skip"


def test_reload_gives_the_same_graph(driver, loaded):
    again = load_tables(driver, DATA_DIR)
    assert again == loaded
    total = one(driver, "MATCH (n) RETURN count(n) AS n")["n"]
    assert total == sum(loaded.node_counts.values())
