"""Facts loaded from the committed cache into Neo4j (T029). Clears the graph."""

from pathlib import Path

import pytest

from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.graph.db import read_session
from knowledge_graph_agent.graph.load_facts import load_facts
from knowledge_graph_agent.graph.load_tables import load_tables
from knowledge_graph_agent.graph.verify import all_passed, verify

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache" / "extractions"


@pytest.fixture(scope="module")
def loaded(driver):
    load_tables(driver, DATA_DIR)
    return load_facts(driver, load_config(ROOT / "config.yaml"), DATA_DIR, CACHE_DIR)


def rows(driver, query, **params):
    with read_session(driver) as session:
        return session.run(query, **params).data()


def test_all_core_messages_loaded(driver, loaded):
    assert loaded.messages == 15
    assert rows(driver, "MATCH (m:Message {collection: 'core'}) RETURN count(m) AS n")[0]["n"] == 15
    extra = rows(driver, "MATCH (f:SourceFile {path: 'messages/extra'}) RETURN f.loaded AS loaded")
    assert extra == [{"loaded": False}]


def test_lookalike_vessels_stay_separate(driver, loaded):
    names = rows(
        driver,
        "MATCH (v:Vessel) WHERE v.name IN ['MV Ocean Star', 'MV Ocean Starlight', 'MV Ocean Pearl'] "
        "RETURN v.evidence_id AS id ORDER BY id",
    )
    assert [r["id"] for r in names] == ["V001", "V003", "V004"]
    # The Ocean Starlight message links only to Ocean Starlight
    linked = rows(
        driver,
        "MATCH (:Message {evidence_id: 'MSG:agent_2026-09-28_fxt_ocean_starlight'})-[:STATES]->(:Fact)"
        "-[:ABOUT_VESSEL]->(v:Vessel) RETURN DISTINCT v.evidence_id AS id",
    )
    assert linked == [{"id": "V003"}]


def test_silver_tern_correction_supersedes_inc094(driver, loaded):
    found = rows(
        driver,
        "MATCH (m:Message {evidence_id: 'MSG:agent_2026-10-21_correction_silver_tern'})-[:STATES]->(f:Fact)"
        "-[c:CORRECTS]->(i:Incident {evidence_id: 'INC-094'}) RETURN c.supersedes AS supersedes",
    )
    assert found and all(r["supersedes"] for r in found)
    # The original row is never changed
    inc = rows(driver, "MATCH (i:Incident {evidence_id: 'INC-094'}) RETURN i.status AS s, i.attributed_delay_hours AS h")
    assert inc == [{"s": "Confirmed", "h": 7}]


def test_weather_detour_facts_conflict(driver, loaded):
    found = rows(
        driver,
        "MATCH (a:Fact)-[c:CONFLICTS_WITH]-(b:Fact) "
        "WHERE a.source_ref CONTAINS 'crew_2026-10-13_ocean_star' AND b.source_ref CONTAINS 'noon_2026-10-14_ocean_star' "
        "RETURN a.verification AS a, b.verification AS b",
    )
    assert found and all(r["a"] == r["b"] == "unverified" for r in found)


def test_kaimon_message_is_suspicious(driver, loaded):
    found = rows(
        driver,
        "MATCH (m:Message {evidence_id: 'MSG:fwd_2026-10-25_status_request'}) "
        "RETURN m.suspicious AS suspicious, m.suspicious_quote AS quote",
    )
    assert found[0]["suspicious"] is True
    assert "ignore earlier instructions" in found[0]["quote"]
    # No fact from that message repeats the injected claim
    quotes = rows(
        driver,
        "MATCH (:Message {evidence_id: 'MSG:fwd_2026-10-25_status_request'})-[:STATES]->(f:Fact) RETURN f.quote AS q",
    )
    assert not any("customs" in r["q"].lower() for r in quotes)


def test_vesta_dune_links_to_the_vessel_only(driver, loaded):
    found = rows(
        driver,
        "MATCH (f:Fact)-[:ABOUT_VESSEL]->(:Vessel {evidence_id: 'V027'}) "
        "WHERE f.source_ref CONTAINS 'vesta_dune' RETURN f.voyage_match AS vm",
    )
    assert found and all(r["vm"] == "unmatched" for r in found)


def test_inc203_has_the_table_row_and_the_customs_fact(driver, loaded):
    found = rows(
        driver,
        "MATCH (f:Fact)-[:REPORTS]->(i:Incident {evidence_id: 'INC-203'}) "
        "RETURN i.source AS table_source, f.source_ref AS message",
    )
    assert found
    assert {r["table_source"] for r in found} == {"Agent email"}
    assert all("rtm_customs" in r["message"] for r in found)


def test_every_fact_quote_is_in_its_message(driver, loaded):
    bad = rows(driver, "MATCH (m:Message)-[:STATES]->(f:Fact) WHERE NOT m.body CONTAINS f.quote RETURN f.evidence_id AS id")
    assert bad == []


def test_all_verify_checks_pass(driver, loaded):
    results = verify(driver, DATA_DIR, facts=True)
    assert all_passed(results), [r for r in results if r.status != "pass"]
    assert all(r.status == "pass" for r in results)


def test_verify_expects_the_message_folders_config_asks_for(driver, loaded):
    # The graph has core only. If config also asked for extra, the check must fail.
    by_name = {r.name: r for r in verify(driver, DATA_DIR, facts=True, collections=("core", "extra"))}
    assert by_name["messages"].status == "fail" and "0 extra messages loaded" in by_name["messages"].detail
