"""The committed extraction cache, run through resolution and rules, with no
database and no API key. The same spot checks as tests/graph/test_load_facts.py."""

from pathlib import Path

import pytest

from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.extract.llm import cached_extractions
from knowledge_graph_agent.extract.messages import load_messages
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.load_facts import build_fact_plan

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


@pytest.fixture(scope="module")
def plan():
    model = load_config(ROOT / "config.yaml").models.extraction
    ref = ReferenceData.from_data(DATA_DIR)
    messages = load_messages(ref, ("core",), DATA_DIR)
    extractions = cached_extractions(messages, model, ROOT / "cache" / "extractions")
    return build_fact_plan(messages, extractions, ref, model, ("core",), DATA_DIR)


def links(plan, link_type):
    return [link for link in plan.links if link.type == link_type]


def test_every_core_message_has_a_cached_extraction(plan):
    assert len(plan.messages) == 15
    assert plan.facts


def test_silver_tern_supersedes_inc094(plan):
    corrections = [l for l in links(plan, "CORRECTS") if l.end == "INC-094"]
    assert corrections and all(l.props["supersedes"] for l in corrections)
    assert all("correction_silver_tern" in l.start for l in corrections)


def test_weather_facts_conflict(plan):
    pairs = [(l.start, l.end) for l in links(plan, "CONFLICTS_WITH")]
    assert any("crew_2026-10-13" in a and "noon_2026-10-14" in b for a, b in pairs)


def test_kaimon_is_suspicious(plan):
    kaimon = next(m for m in plan.messages if m["evidence_id"] == "MSG:fwd_2026-10-25_status_request")
    assert kaimon["suspicious"] is True


def test_inc203_reported_by_customs_fact(plan):
    assert any(l.end == "INC-203" and "rtm_customs" in l.start for l in links(plan, "REPORTS"))


def test_vesta_dune_is_unmatched(plan):
    vesta = [f for f in plan.facts if "vesta_dune" in f["source_ref"]]
    assert vesta and all(f["voyage_match"] == "unmatched" for f in vesta)


def test_message_source_files(plan):
    files = {f["path"]: f for f in plan.source_files}
    assert files["messages/core"]["loaded"] is True and files["messages/core"]["row_or_file_count"] == 15
    assert files["messages/extra"]["loaded"] is False
