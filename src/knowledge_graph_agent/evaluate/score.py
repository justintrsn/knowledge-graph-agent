"""Score an evaluation run from its saved files only (contracts/evaluation.md).

Reads <question_id>.json (the transcripts) and judge.json (saved verdicts) in a
run directory, so rescoring needs no API key and gives the same report.

A question passes only if every rubric item passes. A run that failed is
recorded as `run_failure` and stays in the denominator.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from knowledge_graph_agent.agent.answer import SECTIONS, Answer, Claim, Inference

QUESTIONS_PATH = Path("eval/questions.yaml")

FailureType = Literal[
    "wrong_entity", "false_premise_accepted", "unsupported_claim", "citation_invalid",
    "label_confusion", "temporal_error", "injection_followed", "missing_data_masked",
    "incomplete_coverage", "run_failure",
]
CheckKind = Literal["status_is", "section_matches", "section_absent", "cites", "totals", "validator_passes", "judge"]
SECTION_NAMES = ("headline", "any", *SECTIONS)

# What each check kind needs, besides `kind`
REQUIRED = {
    "status_is": ["value"],
    "section_matches": ["section"],  # plus pattern or patterns
    "section_absent": ["section", "pattern"],
    "cites": ["section", "evidence_id"],
    "totals": ["section", "values"],
    "validator_passes": [],
    "judge": ["instruction"],
}


# --- The question file -------------------------------------------------------


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CheckKind
    value: str | None = None
    section: str | None = None
    pattern: str | None = None
    patterns: list[str] | None = None
    evidence_id: str | None = None
    values: dict[str, float] | None = None
    instruction: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> "Check":
        missing = [f for f in REQUIRED[self.kind] if getattr(self, f) is None]
        if self.kind == "section_matches" and not (self.pattern or self.patterns):
            missing.append("pattern or patterns")
        if missing:
            raise ValueError(f"{self.kind} needs {', '.join(missing)}")
        if self.section is not None and self.section not in SECTION_NAMES:
            raise ValueError(f"unknown section {self.section!r}")
        for p in self.regexes():
            re.compile(p)
        return self

    def regexes(self) -> list[str]:
        return self.patterns or ([self.pattern] if self.pattern else [])


class RubricItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    failure_type: FailureType
    check: Check


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    question: str
    story: str
    expected_answer: str
    rubric: list[RubricItem]

    @model_validator(mode="after")
    def _consistent(self) -> "Question":
        wrong = [i.id for i in self.rubric if not i.id.startswith(f"{self.id}.")]
        if wrong:
            raise ValueError(f"{self.id}: rubric IDs must start with '{self.id}.': {wrong}")
        if not any(i.check.kind == "validator_passes" for i in self.rubric):
            raise ValueError(f"{self.id}: every question needs a validator_passes item")
        return self

    def judge_items(self) -> list[RubricItem]:
        return [i for i in self.rubric if i.check.kind == "judge"]


RUN_QUESTIONS = "questions.yaml"  # the copy of the question file saved in each run


def save_questions(run_dir: Path, questions: list[Question]) -> None:
    """Keep the rubric a run is scored with, so a later edit can't change its rescore."""
    data = [q.model_dump(exclude_none=True) for q in questions]
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    (run_dir / RUN_QUESTIONS).write_text(text, encoding="utf-8", newline="\n")


def questions_for(run_dir: Path) -> list[Question]:
    """The questions a run was scored with: its own copy, or (for older runs) the current file."""
    saved = run_dir / RUN_QUESTIONS
    return load_questions(saved if saved.exists() else QUESTIONS_PATH)


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    questions = [Question.model_validate(q) for q in yaml.safe_load(path.read_text(encoding="utf-8"))]
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate question IDs in {path}")
    items = [i.id for q in questions for i in q.rubric]
    if len(items) != len(set(items)):
        raise ValueError(f"duplicate rubric item IDs in {path}")
    return questions


# --- Checks ------------------------------------------------------------------


def section_claims(answer: Answer, section: str) -> list[Claim]:
    if section == "headline":
        return [answer.headline]
    if section == "any":
        return [c for _, c in answer.claims()]
    return list(getattr(answer, section))


def claim_ids(claim: Claim) -> list[str]:
    return claim.evidence_ids + [v.evidence_id for v in claim.cited_values]


def claim_text(claim: Claim) -> str:
    """What a pattern is matched against: the text, its basis, and its evidence IDs."""
    based_on = claim.based_on if isinstance(claim, Inference) else ""
    return " ".join([claim.text, based_on, *claim_ids(claim)])


def _id_matches(wanted: str, cited: str) -> bool:
    return cited.startswith(wanted[:-1]) if wanted.endswith("*") else cited == wanted


def _states_number(text: str, number: float) -> bool:
    written = str(int(number)) if number == int(number) else str(number)
    return re.search(rf"(?<![\d.]){re.escape(written)}(?![\d]|\.\d)", text) is not None


def run_check(check: Check, answer: Answer, validator_ok: bool, verdict: dict | None) -> tuple[bool, str]:
    """(passed, detail) for one rubric item."""
    claims = section_claims(answer, check.section) if check.section else []
    texts = [claim_text(c) for c in claims]

    if check.kind == "status_is":
        return answer.question_status in check.value.split("|"), f"status is {answer.question_status}"  # "a|b" accepts either
    if check.kind == "section_matches":
        missing = [p for p in check.regexes() if not any(re.search(p, t, re.IGNORECASE) for t in texts)]
        return not missing, f"no match in {check.section} for {missing}" if missing else ""
    if check.kind == "section_absent":
        found = [t[:80] for t in texts if re.search(check.pattern, t, re.IGNORECASE)]
        return not found, f"{check.pattern!r} found in {check.section}: {found[:2]}" if found else ""
    if check.kind == "cites":
        ok = any(_id_matches(check.evidence_id, i) for c in claims for i in claim_ids(c))
        return ok, "" if ok else f"{check.evidence_id} not cited in {check.section}"
    if check.kind == "totals":
        wrong = [
            f"{label} {number:g}" for label, number in check.values.items()
            if not any(label.casefold() in c.text.casefold() and _states_number(c.text, number) for c in claims)
        ]
        return not wrong, f"not stated in {check.section}: {wrong}" if wrong else ""
    if check.kind == "validator_passes":
        return validator_ok, "" if validator_ok else "the answer failed validation"
    # judge
    if verdict is None:
        return False, "no saved judge verdict"
    return verdict["verdict"] == "pass", verdict.get("reason", "")


# --- Scoring a run -------------------------------------------------------------


@dataclass
class ItemResult:
    id: str
    failure_type: str
    passed: bool
    detail: str = ""


@dataclass
class QuestionResult:
    id: str
    question: str
    status: str  # the transcript's status, or "missing"
    passed: bool
    run_failed: bool
    error: str | None
    items: list[ItemResult] = field(default_factory=list)
    cache_read_tokens: int = 0
    transcript: str = ""

    def missed(self) -> list[ItemResult]:
        return [i for i in self.items if not i.passed]


def score_question(question: Question, run_dir: Path, verdicts: dict[str, dict]) -> QuestionResult:
    path = run_dir / f"{question.id}.json"
    if not path.exists():
        return QuestionResult(question.id, question.question, "missing", False, True, "no transcript saved")
    data = json.loads(path.read_text(encoding="utf-8"))
    cache = sum(s["usage"].get("cache_read_input_tokens") or 0 for s in data["steps"])
    result = QuestionResult(
        question.id, question.question, data["status"], False, False, data.get("error"),
        cache_read_tokens=cache, transcript=f"{question.id}.md",
    )
    if data["answer"] is None:
        result.run_failed = True
        return result

    answer = Answer.model_validate(data["answer"])
    validator_ok = not any(v["status"] == "fail" for v in data["validation"])
    for item in question.rubric:
        passed, detail = run_check(item.check, answer, validator_ok, verdicts.get(item.id))
        result.items.append(ItemResult(item.id, item.failure_type, passed, detail))
    result.passed = all(i.passed for i in result.items)
    return result


def _rate(passed: int, total: int) -> dict[str, Any]:
    return {"passed": passed, "total": total, "rate": round(passed / total, 3) if total else None}


def score_run(run_dir: Path, questions: list[Question]) -> dict[str, Any]:
    """The report for a run, from saved files only. Writes report.json and report.md."""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    judge_path = run_dir / "judge.json"
    verdicts = json.loads(judge_path.read_text(encoding="utf-8")) if judge_path.exists() else {}
    asked = set(manifest["questions"])
    results = [score_question(q, run_dir, verdicts) for q in questions if q.id in asked]

    by_type: dict[str, list[bool]] = {t: [] for t in get_args(FailureType)}
    for r in results:
        by_type["run_failure"].append(not r.run_failed)
        for item in r.items:
            by_type[item.failure_type].append(item.passed)

    report = {
        "run_id": manifest["run_id"],
        "created_utc": manifest["created_utc"],
        "git_commit": manifest["git_commit"],
        "models": manifest["models"],
        "graph_coverage": manifest["graph_coverage"],
        "overall": _rate(sum(r.passed for r in results), len(results)),
        "failure_types": {t: _rate(sum(v), len(v)) for t, v in by_type.items() if v},
        "questions": [asdict(r) for r in results],
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    (run_dir / "report.md").write_text(report_markdown(report), encoding="utf-8", newline="\n")
    return report


def _pct(rate: dict[str, Any]) -> str:
    if rate["rate"] is None:
        return "n/a"
    return f"{rate['passed']}/{rate['total']} ({rate['rate']:.0%})"


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Evaluation report: run {report['run_id']}",
        "",
        f"Run started {report['created_utc']}, git commit `{report['git_commit']}`.",
        f"Models: {', '.join(f'{k} {v}' for k, v in report['models'].items())}.",
        "",
        f"**Overall: {_pct(report['overall'])} questions passed.** A question passes only if every rubric item passes.",
        "",
        "## Questions",
        "",
        "| Question | Result | Items passed | Status | Cache-read tokens | Transcript |",
        "|---|---|---|---|---|---|",
    ]
    for q in report["questions"]:
        items = f"{sum(i['passed'] for i in q['items'])}/{len(q['items'])}" if q["items"] else "-"
        result = "pass" if q["passed"] else ("**run failed**" if q["run_failed"] else "**fail**")
        link = f"[{q['transcript']}]({q['transcript']})" if q["transcript"] else "-"
        lines.append(f"| {q['id']} | {result} | {items} | {q['status']} | {q['cache_read_tokens']} | {link} |")

    lines += ["", "## By failure type", "", "| Failure type | Items passed |", "|---|---|"]
    lines += [f"| {t} | {_pct(r)} |" for t, r in report["failure_types"].items()]
    lines += ["", "`run_failure` counts questions whose run produced an answer.", "", "## Failures", ""]

    failures = [q for q in report["questions"] if not q["passed"]]
    if not failures:
        lines.append("None.")
    for q in failures:
        lines += [f"### {q['id']}: {q['question']}", ""]
        if q["run_failed"]:
            lines.append(f"- **run_failure**: {q['error']}")
        for item in q["items"]:
            if not item["passed"]:
                lines.append(f"- **{item['failure_type']}** `{item['id']}`: {item['detail']}")
        if q["transcript"]:
            lines.append(f"- Transcript: [{q['transcript']}]({q['transcript']})")
        lines.append("")

    lines += ["## Graph coverage", "", "| Source | Loaded | Rows or files |", "|---|---|---|"]
    lines += [f"| {c['path']} | {c['loaded']} | {c['count']} |" for c in report["graph_coverage"]]
    return "\n".join(lines) + "\n"
