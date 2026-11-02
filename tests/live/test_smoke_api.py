"""One real question through the agent loop (T038). Costs money, so it runs
only when asked for explicitly:

    KG_LIVE_TESTS=1 uv run pytest tests/live

It also needs ANTHROPIC_API_KEY and a running Neo4j.
"""

import os
from pathlib import Path

import anthropic
import pytest

from knowledge_graph_agent.agent.answer import Answer
from knowledge_graph_agent.agent.loop import run_agent
from knowledge_graph_agent.agent.prompts import system_prompt
from knowledge_graph_agent.agent.tools import ToolContext
from knowledge_graph_agent.config import has_api_key, load_config
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.load_facts import load_facts
from knowledge_graph_agent.graph.load_tables import load_tables

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    os.environ.get("KG_LIVE_TESTS") != "1", reason="live API test; set KG_LIVE_TESTS=1 to run (costs money)"
)


def test_question_calls_a_tool_then_returns_a_valid_answer(driver):
    if not has_api_key():
        pytest.skip("no ANTHROPIC_API_KEY")
    config = load_config(ROOT / "config.yaml")
    load_tables(driver, ROOT / "data")
    load_facts(driver, config, ROOT / "data", ROOT / "cache" / "extractions")
    ctx = ToolContext(driver, ReferenceData.from_data(ROOT / "data"), config)

    run = run_agent(
        "What is the IMO number and technical manager of MV Ocean Star?",
        anthropic.Anthropic(), ctx, config, system_prompt(config),
    )

    assert run.status == "ok", run.error
    assert run.tool_steps >= 1, "the answer must come from a tool"
    assert isinstance(run.answer, Answer)
    assert "V001" in run.retrieved_ids
    # The second call reuses the cached tools and system prompt
    assert run.steps[1].usage["cache_read_input_tokens"] > 0
