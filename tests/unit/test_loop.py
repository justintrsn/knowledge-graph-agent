"""The agent loop, driven by a scripted fake client: no API key, no Neo4j (T037)."""

from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from knowledge_graph_agent.agent import tools
from knowledge_graph_agent.agent.answer import Answer, Claim
from knowledge_graph_agent.agent.loop import run_agent
from knowledge_graph_agent.config import StepBudget, load_config

ROOT = Path(__file__).resolve().parents[2]


# --- Fakes ------------------------------------------------------------------


def usage():
    return SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80, cache_creation_input_tokens=0)


def response(*blocks, stop_reason, model="claude-sonnet-5-5"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, model=model, usage=usage(), stop_details=None)


def tool_use(id_, name="echo", **params):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input={"value": params.get("value", "x")})


def text(t):
    return SimpleNamespace(type="text", text=t)


def thinking(t):
    return SimpleNamespace(type="thinking", thinking=t)


ANSWER = Answer(
    question_status="answerable",
    headline=Claim(text="Found it.", evidence_ids=["X-1"], cited_values=[]),
    interpretation=[], confirmed_facts=[], unverified_reports=[], inferences=[], exclusions=[],
    data_gaps=[], suspicious_content=[], recommended_checks=[], incomplete=False, not_checked=[],
).model_dump_json()


def calls_tool(id_):
    return response(thinking("Look it up."), tool_use(id_), stop_reason="tool_use")


def answers():
    return response(text(ANSWER), stop_reason="end_turn")


class FakeClient:
    """Returns scripted responses, and records what each call sent."""

    def __init__(self, *script):
        self.script = list(script)
        self.sent: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        # Copy the list: the loop keeps appending to the same one.
        self.sent.append(request | {"messages": list(request["messages"])})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def echo_tool(monkeypatch):
    def echo(ctx, value):
        return tools.ToolResult(rows=[{"evidence_id": "X-1", "source_ref": "test", "value": value}])

    registry = dict(tools.REGISTRY)
    registry["echo"] = tools.Tool("echo", "Echo.", {
        "type": "object", "properties": {"value": {"type": "string"}},
        "required": ["value"], "additionalProperties": False,
    }, echo)
    monkeypatch.setattr(tools, "REGISTRY", registry)


def config(soft=5, hard=10):
    base = load_config(ROOT / "config.yaml")
    budget = base.agent.model_copy(update={"step_budget": StepBudget(soft=soft, hard=hard)})
    return base.model_copy(update={"agent": budget})


def run(client, cfg=None):
    return run_agent("Why is it late?", client, ctx=None, config=cfg or config(), system="SYSTEM")


def system_messages(request):
    return [m for m in request["messages"] if m["role"] == "system"]


# --- Tests ------------------------------------------------------------------


def test_normal_two_step_run():
    client = FakeClient(calls_tool("t1"), answers())
    result = run(client)
    assert result.status == "ok"
    assert result.answer.headline.text == "Found it."
    assert [s.index for s in result.steps] == [1, 2]
    assert result.steps[0].thinking == ["Look it up."]
    assert result.steps[0].tool_calls[0].output["evidence_ids"] == ["X-1"]
    assert result.retrieved_ids == {"X-1"}
    assert result.steps[0].usage["cache_read_input_tokens"] == 80


def test_request_settings():
    client = FakeClient(answers())
    run(client)
    sent = client.sent[0]
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("strict" not in t for t in sent["tools"])  # the answer format is strict; tools are checked locally
    assert sent["fallbacks"] == "default"
    assert "tool_choice" not in sent


def test_parallel_tool_calls_return_in_one_message():
    client = FakeClient(
        response(tool_use("a", value="1"), tool_use("b", value="2"), stop_reason="tool_use"),
        answers(),
    )
    result = run(client)
    last = client.sent[1]["messages"][-1]
    assert last["role"] == "user"
    assert [r["tool_use_id"] for r in last["content"]] == ["a", "b"]
    assert result.tool_steps == 1  # parallel calls count as one step


def test_soft_notice_appears_once_after_step_5():
    client = FakeClient(*[calls_tool(f"t{i}") for i in range(7)], answers())
    result = run(client)
    assert result.status == "ok"
    final = client.sent[-1]
    assert len(system_messages(final)) == 1
    assert [s.index for s in result.steps if s.notice] == [5]
    # The notice comes straight after step 5's tool results
    assert len(system_messages(client.sent[4])) == 0
    assert len(system_messages(client.sent[5])) == 1


def test_hard_limit_forces_an_answer_marked_incomplete():
    client = FakeClient(*[calls_tool(f"t{i}") for i in range(3)], answers())
    result = run(client, config(soft=2, hard=3))
    assert client.sent[-1]["tool_choice"] == {"type": "none"}
    assert all("tool_choice" not in r for r in client.sent[:-1])
    assert result.answer.incomplete is True
    assert result.status == "incomplete"
    assert len(system_messages(client.sent[-1])) == 2  # soft, then hard


def test_refusal_is_a_failed_run_with_no_answer():
    refused = response(stop_reason="refusal")
    refused.stop_details = SimpleNamespace(category="cyber")
    result = run(FakeClient(refused))
    assert (result.status, result.answer) == ("failed", None)
    assert "declined" in result.error


def test_api_error_is_a_failed_run_with_no_answer():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    result = run(FakeClient(calls_tool("t1"), error))
    assert (result.status, result.answer) == ("failed", None)
    assert "could not reach" in result.error
    assert len(result.steps) == 1  # the step before the error is kept


def test_cut_off_response_is_a_failed_run():
    result = run(FakeClient(response(text("{"), stop_reason="max_tokens")))
    assert result.status == "failed" and result.answer is None


def test_answer_not_matching_the_schema_fails():
    result = run(FakeClient(response(text('{"headline": "no"}'), stop_reason="end_turn")))
    assert result.status == "failed" and "schema" in result.error


def test_unknown_tool_is_returned_as_an_error_not_raised():
    client = FakeClient(response(tool_use("t1", name="nope"), stop_reason="tool_use"), answers())
    result = run(client)
    call = result.steps[0].tool_calls[0]
    assert call.is_error and "Unknown tool" in call.output["error"]
    assert client.sent[1]["messages"][-1]["content"][0]["is_error"] is True


def test_history_is_append_only():
    client = FakeClient(calls_tool("t1"), calls_tool("t2"), answers())
    run(client)
    for earlier, later in zip(client.sent, client.sent[1:]):
        assert later["messages"][: len(earlier["messages"])] == earlier["messages"]
    # Assistant content is passed back exactly as received
    first_reply = client.sent[1]["messages"][1]
    assert first_reply["role"] == "assistant" and first_reply["content"][1].id == "t1"
