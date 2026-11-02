"""The agent loop: ask Claude, run the tools it calls, repeat, until it answers.

Written by hand rather than with the SDK's tool runner, so every step (thinking
summary, tool input and output, model, token use) lands in the transcript.

Step budget: a step is one assistant turn that calls tools; parallel calls in
one turn count once. After the soft limit, a system message asks the model to
finish. After the hard limit, one last call with tool_choice none forces an
answer, marked incomplete.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
from pydantic import ValidationError

from knowledge_graph_agent.agent import tools
from knowledge_graph_agent.agent.answer import Answer, output_format
from knowledge_graph_agent.agent.prompts import HARD_LIMIT_NOTICE, SOFT_LIMIT_NOTICE
from knowledge_graph_agent.config import Config

MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

RunStatus = Literal["ok", "incomplete", "failed"]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    is_error: bool
    duration_ms: int


@dataclass
class Step:
    index: int
    model: str
    stop_reason: str | None
    thinking: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)
    notice: str | None = None  # a system message sent after this step


@dataclass
class AgentRun:
    question: str
    steps: list[Step] = field(default_factory=list)
    answer: Answer | None = None
    raw_answer: str | None = None
    status: RunStatus = "ok"
    error: str | None = None
    retrieved_ids: set[str] = field(default_factory=set)

    @property
    def tool_steps(self) -> int:
        return sum(1 for s in self.steps if s.tool_calls)


def _request(config: Config, system: str) -> dict[str, Any]:
    """Everything sent on every call. Fixed for the whole run, so the cache holds."""
    params: dict[str, Any] = {
        "model": config.models.agent,
        "max_tokens": MAX_TOKENS,
        # The breakpoint on the system block caches tools + system prompt together.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "tools": tools.definitions(),
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": config.agent.effort, "format": output_format()},
    }
    if config.agent.fallbacks:
        # If a safety classifier declines, the API retries on a fallback model in the same call.
        params |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    return params


def _record(index: int, response: Any) -> Step:
    step = Step(index=index, model=response.model, stop_reason=response.stop_reason)
    for block in response.content:
        if block.type == "thinking" and block.thinking:
            step.thinking.append(block.thinking)
        elif block.type == "text":
            step.text.append(block.text)
        elif block.type == "fallback":
            step.fallbacks.append(f"{block.from_.model} declined; {block.to.model} continued")
    usage = response.usage
    step.usage = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }
    return step


def _collect_ids(result: dict[str, Any]) -> set[str]:
    return set(result.get("evidence_ids", [])) | set(result.get("searched", []))


def run_agent(
    question: str,
    client: anthropic.Anthropic,
    ctx: tools.ToolContext,
    config: Config,
    system: str,
) -> AgentRun:
    """Answer one question. Never raises for API problems and never invents an
    answer: a failure is returned as a failed run, with the reason."""
    run = AgentRun(question)
    params = _request(config, system)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    soft, hard = config.agent.step_budget.soft, config.agent.step_budget.hard
    final_call = False

    while True:
        extra = {"tool_choice": {"type": "none"}} if final_call else {}
        try:
            response = client.beta.messages.create(messages=messages, **params, **extra)
        except anthropic.RateLimitError as e:
            return _fail(run, f"rate limited: {e.message}")
        except anthropic.APIStatusError as e:
            return _fail(run, f"API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:
            return _fail(run, f"could not reach the API: {e}")

        step = _record(len(run.steps) + 1, response)
        run.steps.append(step)
        # Pass the content back unchanged: thinking blocks must not be edited.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            details = response.stop_details
            return _fail(run, f"the model declined (category: {details.category if details else None})")
        if response.stop_reason == "max_tokens":
            return _fail(run, f"the response was cut off at {MAX_TOKENS} tokens")

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "tool_use" and tool_uses:
            results = []
            for block in tool_uses:
                started = time.perf_counter()
                output, is_error = tools.execute(ctx, block.name, dict(block.input))
                step.tool_calls.append(ToolCall(
                    block.id, block.name, dict(block.input), output, is_error,
                    round((time.perf_counter() - started) * 1000),
                ))
                if not is_error:
                    run.retrieved_ids |= _collect_ids(output)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tools.result_json(output),
                    "is_error": is_error,
                })
            # All results of parallel calls go back in one message.
            messages.append({"role": "user", "content": results})

            if run.tool_steps >= hard:
                step.notice = HARD_LIMIT_NOTICE
                final_call = True
            elif run.tool_steps == soft:
                step.notice = SOFT_LIMIT_NOTICE.format(steps=soft)
            if step.notice:
                messages.append({"role": "system", "content": step.notice})
            continue

        return _finish(run, step, final_call)


def _fail(run: AgentRun, reason: str) -> AgentRun:
    run.status = "failed"
    run.error = reason
    return run


def _finish(run: AgentRun, step: Step, hit_budget: bool) -> AgentRun:
    run.raw_answer = "".join(step.text).strip()
    if not run.raw_answer:
        return _fail(run, f"no answer in the final response (stop reason {step.stop_reason})")
    try:
        run.answer = Answer.model_validate_json(run.raw_answer)
    except ValidationError as e:
        return _fail(run, f"the answer did not match the schema: {e.errors()[:3]}")
    if hit_budget:
        run.answer.incomplete = True
    run.status = "incomplete" if run.answer.incomplete else "ok"
    return run


def dump(run: AgentRun) -> dict[str, Any]:
    """The run as plain data, for the transcript."""
    return {
        "question": run.question,
        "status": run.status,
        "error": run.error,
        "steps": [
            {
                "index": s.index,
                "model": s.model,
                "stop_reason": s.stop_reason,
                "thinking": s.thinking,
                "text": s.text,
                "tool_calls": [c.__dict__ for c in s.tool_calls],
                "usage": s.usage,
                "fallbacks": s.fallbacks,
                "notice": s.notice,
            }
            for s in run.steps
        ],
        "answer": json.loads(run.answer.model_dump_json()) if run.answer else None,
        "raw_answer": run.raw_answer,
        "retrieved_ids": sorted(run.retrieved_ids),
    }
