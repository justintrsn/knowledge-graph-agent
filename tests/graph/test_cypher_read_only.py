"""The guarded Cypher tool against the real graph (T056). The read-only session,
not the guard, is what stops writes, so these tests go around the guard."""

from pathlib import Path

import pytest
from neo4j.exceptions import Neo4jError

from knowledge_graph_agent.agent import tools
from knowledge_graph_agent.config import CypherSettings, load_config
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.db import read_session
from knowledge_graph_agent.graph.load_facts import load_facts
from knowledge_graph_agent.graph.load_tables import load_tables

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ctx(driver):
    config = load_config(ROOT / "config.yaml")
    load_tables(driver, ROOT / "data")
    load_facts(driver, config, ROOT / "data", ROOT / "cache" / "extractions")
    return tools.ToolContext(driver, ReferenceData.from_data(ROOT / "data"), config)


def with_limits(ctx, **limits):
    cypher = CypherSettings(**ctx.config.cypher.model_dump() | limits)
    return tools.ToolContext(ctx.driver, ctx.ref, ctx.config.model_copy(update={"cypher": cypher}))


def cypher(ctx, query):
    return tools.execute(ctx, "run_read_cypher", {"query": query, "reason": "test"})


def vessel_count(ctx):
    with read_session(ctx.driver) as session:
        return session.run("MATCH (v:Vessel) RETURN count(v) AS n").single()["n"]


def test_a_read_returns_citable_rows(ctx):
    result, is_error = cypher(ctx, "MATCH (v:Vessel {evidence_id: 'V001'}) RETURN v.evidence_id AS evidence_id, v.name AS name")
    assert not is_error and result["rows"] == [{"evidence_id": "V001", "name": "MV Ocean Star"}]
    assert result["evidence_ids"] == ["V001"]


def test_returned_nodes_are_citable_too(ctx):
    result, _ = cypher(ctx, "MATCH (v:Voyage {evidence_id: 'VG-1042'}) RETURN v")
    assert result["evidence_ids"] == ["VG-1042"] and "Voyage" in result["rows"][0]["v"]["labels"]


def test_the_guard_refuses_a_write_with_a_reason(ctx):
    result, is_error = cypher(ctx, "MATCH (v:Vessel) DETACH DELETE v")
    assert is_error and "DELETE" in result["error"]
    assert vessel_count(ctx) == 40


def test_a_write_fails_in_the_read_session_even_without_the_guard(ctx):
    before = vessel_count(ctx)
    with pytest.raises(Neo4jError), read_session(ctx.driver) as session:
        session.run("CREATE (:Vessel {evidence_id: 'HACK'})").consume()
    with pytest.raises(Neo4jError), read_session(ctx.driver) as session:
        session.run("MATCH (v:Vessel {evidence_id: 'V001'}) SET v.name = 'changed'").consume()
    assert vessel_count(ctx) == before


def test_a_write_past_the_guard_still_fails_and_is_returned_as_an_error(ctx, monkeypatch):
    monkeypatch.setattr(tools, "check_cypher", lambda query, max_path_length: None)  # guard off
    result, is_error = cypher(ctx, "CREATE (:Vessel {evidence_id: 'HACK'}) RETURN 1")
    assert is_error and "Database error" in result["error"]
    assert vessel_count(ctx) == 40


def test_a_slow_query_times_out(ctx):
    slow = "UNWIND range(1, 20000) AS a UNWIND range(1, 20000) AS b RETURN count(*) AS n"
    result, is_error = cypher(with_limits(ctx, timeout_seconds=1), slow)
    assert is_error and "Database error" in result["error"]


def test_a_large_result_is_truncated_and_flagged(ctx):
    result, is_error = cypher(with_limits(ctx, max_rows=5), "MATCH (v:Vessel) RETURN v.evidence_id AS evidence_id")
    assert not is_error and len(result["rows"]) == 5 and result["truncated"] is True
    result, _ = cypher(with_limits(ctx, max_rows=50), "MATCH (v:Vessel) RETURN v.evidence_id AS evidence_id")
    assert len(result["rows"]) == 40 and result["truncated"] is False
