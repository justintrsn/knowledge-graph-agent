"""The tools for rejecting a false premise (User Story 2), against the real graph (T048)."""

from pathlib import Path

import pytest

from knowledge_graph_agent.agent import tools
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


def test_voyage_phases_are_relative_to_the_snapshot(ctx):
    rows = call(ctx, "vessel_voyages", vessel_id="V002")["rows"]
    phases = {r["evidence_id"]: r["computed"]["phase"] for r in rows}
    assert phases == {"VG-0904": "completed", "VG-0997": "completed", "VG-1063": "completed", "VG-1121": "future"}
    hamburg = [r for r in rows if r["destination"] == "DEHAM"]
    assert [r["evidence_id"] for r in hamburg] == ["VG-1121"]


def test_voyage_date_range_keeps_overlapping_voyages(ctx):
    rows = call(ctx, "vessel_voyages", vessel_id="V002", from_date="2026-09-01", to_date="2026-09-30")["rows"]
    # VG-0997 arrives at Antwerp 16 Sep; VG-1063 leaves Antwerp 21 Sep
    assert [r["evidence_id"] for r in rows] == ["VG-0997", "VG-1063"]


def test_in_progress_voyage(ctx):
    phases = {r["evidence_id"]: r["computed"]["phase"] for r in call(ctx, "vessel_voyages", vessel_id="V001")["rows"]}
    assert phases["VG-1042"] == "in_progress"


def test_no_hamburg_call_in_september_and_the_empty_result_says_what_was_searched(ctx):
    result = call(ctx, "port_calls", vessel_ids=["V002"], port="Hamburg", from_date="2026-09-01", to_date="2026-09-30")
    assert result["rows"] == [] and result["evidence_ids"] == []
    assert result["searched"] == ["FILE:port_calls.csv"]


def test_what_the_vessel_did_in_september(ctx):
    rows = call(ctx, "port_calls", vessel_ids=["V002"], from_date="2026-09-01", to_date="2026-09-30")["rows"]
    antwerp = {r["evidence_id"]: r for r in rows}["PC-7008"]
    assert antwerp["port"] == "BEANR" and antwerp["berthing_utc"] == "2026-09-16T08:00:00+00:00"
    assert antwerp["berthing_utc"] == antwerp["berth_scheduled_utc"]  # berthed on schedule


def test_port_alone_is_a_valid_filter(ctx):
    rows = call(ctx, "port_calls", port="Hamburg", from_date="2026-07-01", to_date="2026-07-31")["rows"]
    assert {r["evidence_id"] for r in rows} >= {"PC-7042", "PC-7099"}
    assert all(r["port"] == "DEHAM" for r in rows)


def test_unknown_vessel_has_no_match_only_suggestions(ctx):
    result = call(ctx, "find_vessel", query="MV Ocean Stark")
    assert result["rows"] == [] and result["evidence_ids"] == []
    assert result["suggestions"] and result["searched"] == ["FILE:vessels.csv"]


def test_unknown_port_has_no_match(ctx):
    result = call(ctx, "find_port", query="Port of Gdansk")
    assert result["rows"] == [] and result["evidence_ids"] == []
    assert result["searched"] == ["FILE:ports.csv"]
    assert "suggestions" in result
