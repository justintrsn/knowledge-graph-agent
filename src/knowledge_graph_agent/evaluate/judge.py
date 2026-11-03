"""The LLM judge, for rubric items regex can't assess (contracts/evaluation.md).

One call per `judge` item. The judge sees the question, the expected answer,
one instruction and the rendered answer, and returns a strict JSON verdict.
Verdicts are saved to judge.json, so rescoring never calls the judge again.
"""

from typing import Literal

import anthropic
from pydantic import BaseModel

from knowledge_graph_agent.agent.answer import Answer, render
from knowledge_graph_agent.evaluate.score import Question, RubricItem

MAX_TOKENS = 2000

SYSTEM_PROMPT = """\
You grade one rubric item for an operations assistant's answer about ship delays.

You get the question, the expected answer written by the evaluator, one rubric instruction, and \
the assistant's answer. Each claim in the answer is filed under a section (Confirmed, Reported \
not verified, Inferred, and so on) and lists its evidence IDs in brackets.

Decide only whether the instruction holds for the answer. Judge the meaning, not the wording: a \
paraphrase counts. Use the expected answer to understand what the instruction refers to, but do \
not fail the item for things the instruction doesn't ask about. Give a one-sentence reason."""


class Verdict(BaseModel):
    verdict: Literal["pass", "fail"]
    reason: str


def judge_item(
    client: anthropic.Anthropic, model: str, question: Question, item: RubricItem, answer: Answer
) -> dict[str, str]:
    content = (
        f"<question>\n{question.question}\n</question>\n\n"
        f"<expected_answer>\n{question.expected_answer.strip()}\n</expected_answer>\n\n"
        f"<instruction>\n{item.check.instruction.strip()}\n</instruction>\n\n"
        f"<answer>\n{render(answer)}\n</answer>"
    )
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_format=Verdict,
        )
    except anthropic.APIError as e:
        return {"verdict": "fail", "reason": f"judge call failed: {e}", "model": model}
    if response.parsed_output is None:
        return {"verdict": "fail", "reason": f"no verdict (stop reason {response.stop_reason})", "model": model}
    return response.parsed_output.model_dump() | {"model": response.model}


def judge_question(
    client: anthropic.Anthropic, model: str, question: Question, answer: Answer
) -> dict[str, dict[str, str]]:
    """Verdicts for every judge item of one question, keyed by rubric item ID."""
    return {item.id: judge_item(client, model, question, item, answer) for item in question.judge_items()}
