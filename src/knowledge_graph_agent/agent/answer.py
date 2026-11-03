"""The answer the agent returns, per contracts/answer.md.

The model fills this structure through structured output. Every claim carries
the evidence IDs behind it; validate.py checks them against the graph.
"""

from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field

QuestionStatus = Literal["answerable", "false_premise", "entity_not_found", "cannot_answer"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitedValue(_Strict):
    evidence_id: str
    field: str = Field(description="Property name on the cited node, e.g. 'attributed_delay_hours', or 'quote'.")
    value: str | float | bool | None = Field(description="The value exactly as the tool returned it.")


class Claim(_Strict):
    text: str
    evidence_ids: list[str] = Field(description="At least one evidence_id from a tool result in this run.")
    cited_values: list[CitedValue] = Field(description="Each number, time or quote in the text, with where it comes from.")


class Inference(Claim):
    based_on: str = Field(description="What the reasoning rests on.")


class Answer(_Strict):
    question_status: QuestionStatus
    headline: Claim
    interpretation: list[Claim] = Field(description="Time windows and readings of the question that were used.")
    confirmed_facts: list[Claim]
    unverified_reports: list[Claim]
    inferences: list[Inference]
    exclusions: list[Claim] = Field(description="What was left out, and why.")
    data_gaps: list[Claim]
    suspicious_content: list[Claim]
    recommended_checks: list[Claim]
    incomplete: bool = Field(
        description="True only if the answer is missing something it needed: you were told the tool "
        "budget is used up, or a lookup it depends on failed. Not for things beyond the question."
    )
    not_checked: list[str] = Field(description="Checks that would have helped but were not made.")

    def claims(self) -> list[tuple[str, Claim]]:
        """Every claim with the section it is in."""
        found: list[tuple[str, Claim]] = [("headline", self.headline)]
        for section in SECTIONS:
            found += [(section, claim) for claim in getattr(self, section)]
        return found


# Sections in display order. The first three are always shown, even when empty.
SECTIONS = (
    "confirmed_facts",
    "unverified_reports",
    "inferences",
    "interpretation",
    "exclusions",
    "data_gaps",
    "suspicious_content",
    "recommended_checks",
)
REQUIRED_SECTIONS = SECTIONS[:3]
TITLES = {
    "interpretation": "How the question was read",
    "confirmed_facts": "Confirmed",
    "unverified_reports": "Reported, not verified",
    "inferences": "Inferred",
    "exclusions": "Excluded",
    "data_gaps": "Data gaps",
    "suspicious_content": "Suspicious content (ignored)",
    "recommended_checks": "Recommended checks",
}


def output_format() -> dict[str, Any]:
    """The answer's JSON schema, for `output_config.format`."""
    return {"type": "json_schema", "schema": anthropic.transform_schema(Answer)}


def _line(claim: Claim) -> str:
    text = f"- {claim.text}  [{', '.join(claim.evidence_ids) or 'no evidence'}]"
    if isinstance(claim, Inference):
        text += f"\n    based on: {claim.based_on}"
    return text


def render(answer: Answer) -> str:
    """Plain text for the terminal, with evidence IDs beside each claim."""
    lines = [_line(answer.headline).removeprefix("- ")]
    if answer.question_status != "answerable":
        lines.append(f"(question status: {answer.question_status.replace('_', ' ')})")
    if answer.incomplete:
        lines.append("(INCOMPLETE: the answer is missing checks it needed; see Not checked)")
    for section in SECTIONS:
        claims = getattr(answer, section)
        if not claims and section not in REQUIRED_SECTIONS:
            continue
        lines.append(f"\n{TITLES[section]}:")
        lines += [_line(c) for c in claims] or ["- (none)"]
    if answer.not_checked:
        lines.append("\nNot checked:")
        lines += [f"- {item}" for item in answer.not_checked]
    return "\n".join(lines)
