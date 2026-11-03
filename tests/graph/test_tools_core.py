"""The first agent tools and the validator's graph lookup, against the real graph."""

from pathlib import Path

import pytest

from knowledge_graph_agent.agent import tools
from knowledge_graph_agent.agent.validate import Neo4jLookup
from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.load_facts import load_facts
from knowledge_graph_agent.graph.load_tables import load_tables

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ctx(driver):
    config = load_config(ROOT / "config.yaml")
    load_tables(driver, ROOT / "data")
    load_facts(driver, config, ROOT / "data", ROOT / "cache" / "extractions")
    return tools.ToolContext(driver, ReferenceData.from_data(ROOT / "data"), config)


def call(ctx, name, **params):
    result, is_error = tools.execute(ctx, name, params)
    assert not is_error, result
    return result


def test_find_vessel_by_name_variant(ctx):
    result = call(ctx, "find_vessel", query="M/V OCEAN STAR")
    assert result["evidence_ids"] == ["V001"]
    assert result["match"]["method"] == "name"
    assert "match_method" not in result["rows"][0]  # rows hold graph fields only


def test_find_vessel_by_imo(ctx):
    assert call(ctx, "find_vessel", query="IMO 9654321")["evidence_ids"] == ["V003"]


def test_unknown_vessel_gives_suggestions_not_matches(ctx):
    result = call(ctx, "find_vessel", query="MV Ocean Stark")
    assert result["rows"] == [] and result["evidence_ids"] == []
    assert result["searched"] == ["FILE:vessels.csv"]
    assert "MV Ocean Star" in result["suggestions"]


def test_find_port(ctx):
    assert call(ctx, "find_port", query="Rotterdam")["evidence_ids"] == ["NLRTM"]
    missing = call(ctx, "find_port", query="Port of Gdansk")
    assert missing["rows"] == [] and missing["searched"] == ["FILE:ports.csv"]


def test_source_coverage_reports_what_was_not_loaded(ctx):
    result = call(ctx, "source_coverage")
    loaded = {r["path"]: r["loaded"] for r in result["rows"]}
    assert loaded["messages/core"] is True and loaded["messages/extra"] is False
    assert "messages/extra" in result["note"]
    assert set(result["searched"]) == set(result["evidence_ids"])


def test_every_row_has_evidence_id_and_source_ref(ctx):
    for name, params in [("find_vessel", {"query": "Ocean Star"}), ("find_port", {"query": "NLRTM"}), ("source_coverage", {})]:
        for row in call(ctx, name, **params)["rows"]:
            assert row["evidence_id"] and row["source_ref"]


def test_bad_parameters_are_an_error_result(ctx):
    result, is_error = tools.execute(ctx, "find_vessel", {"name": "x"})
    assert is_error and "unknown ['name']" in result["error"]


def test_lookup_returns_labels_and_message_details(ctx):
    lookup = Neo4jLookup(ctx.driver)
    assert lookup.get("INC-999") is None
    incident = lookup.get("INC-201")
    assert "Incident" in incident["labels"] and incident["attributed_delay_hours"] == 12
    fact = lookup.get("MSG:fwd_2026-10-25_status_request#1")
    assert fact["message_suspicious"] is True and fact["message_body"]
