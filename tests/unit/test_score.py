"""Scoring from saved runs, with no API key and no Neo4j (T061)."""

import json
from pathlib import Path

import pytest
import yaml

from knowledge_graph_agent.agent.answer import Answer, Claim, Inference
from knowledge_graph_agent.agent.loop import AgentRun, Step
from knowledge_graph_agent.agent.transcript import new_run_dir, save_transcript, write_json
from knowledge_graph_agent.agent.validate import ValidationResult
from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.evaluate.score import (
    Check, Question, load_questions, questions_for, run_check, save_questions, score_run,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config(ROOT / "config.yaml")


def claim(text, *ids):
    return Claim(text=text, evidence_ids=list(ids), cited_values=[])


ANSWER = Answer(
    question_status="false_premise",
    headline=claim("VG-1042 is 55 h late.", "VG-1042"),
    interpretation=[claim("Last month is September 2026.", "VG-1042")],
    confirmed_facts=[
        claim("Engine fault cost 12 h.", "INC-201"),
        claim("Halden Retail has 80 containers on board.", "BK-1", "BK-2"),
        claim("Lumio Home has 30 containers.", "BK-3"),
    ],
    unverified_reports=[claim("The crew estimate about 20 h for weather.", "MSG:crew_2026-10-13_ocean_star#3")],
    inferences=[Inference(text="About 35 h at anchor.", evidence_ids=["PC-7004"], cited_values=[], based_on="port call times")],
    exclusions=[], data_gaps=[], suspicious_content=[], recommended_checks=[],
    incomplete=False, not_checked=[],
)


def check(answer=ANSWER, validator_ok=True, verdict=None, **spec):
    return run_check(Check(**spec), answer, validator_ok, verdict)[0]


# --- Each check kind ---------------------------------------------------------


def test_status_is():
    assert check(kind="status_is", value="false_premise")
    assert not check(kind="status_is", value="answerable")
    assert check(kind="status_is", value="answerable|false_premise")  # either is accepted


def test_section_matches_needs_every_pattern():
    assert check(kind="section_matches", section="headline", pattern="VG-1042")
    assert check(kind="section_matches", section="any", patterns=["55 h", "september 2026"])  # case-insensitive
    assert not check(kind="section_matches", section="any", patterns=["55 h", "Hamburg"])
    assert not check(kind="section_matches", section="confirmed_facts", pattern="35 h")  # only in inferences


def test_section_matches_sees_evidence_ids_and_the_basis_of_inferences():
    assert check(kind="section_matches", section="inferences", pattern="PC-7004")
    assert check(kind="section_matches", section="inferences", pattern="port call times")


def test_section_absent():
    assert check(kind="section_absent", section="confirmed_facts", pattern="weather")
    assert not check(kind="section_absent", section="unverified_reports", pattern="weather")
    assert check(kind="section_absent", section="exclusions", pattern=".")  # empty section
    assert not check(kind="section_absent", section="any", pattern="INC-201")  # IDs count


def test_cites_exact_and_prefix():
    assert check(kind="cites", section="confirmed_facts", evidence_id="INC-201")
    assert not check(kind="cites", section="unverified_reports", evidence_id="INC-201")
    assert check(kind="cites", section="unverified_reports", evidence_id="MSG:crew_2026-10-13_ocean_star*")
    assert not check(kind="cites", section="any", evidence_id="INC-20")  # no prefix without *


def test_totals():
    assert check(kind="totals", section="confirmed_facts", values={"Halden Retail": 80, "Lumio Home": 30})
    assert not check(kind="totals", section="confirmed_facts", values={"Halden Retail": 8})  # 80 isn't 8
    assert not check(kind="totals", section="confirmed_facts", values={"Verdant Foods": 22})  # not mentioned


def test_validator_passes():
    assert check(kind="validator_passes")
    assert not check(kind="validator_passes", validator_ok=False)


def test_judge_uses_only_the_saved_verdict():
    assert check(kind="judge", instruction="x", verdict={"verdict": "pass", "reason": "ok"})
    assert not check(kind="judge", instruction="x", verdict={"verdict": "fail", "reason": "no"})
    assert not check(kind="judge", instruction="x", verdict=None)


def test_bad_checks_are_rejected_when_the_file_loads():
    with pytest.raises(ValueError):
        Check(kind="cites", section="any")  # no evidence_id
    with pytest.raises(ValueError):
        Check(kind="section_matches", section="nowhere", pattern="x")
    with pytest.raises(ValueError):
        Question(id="Q1", question="?", story="s", expected_answer="e", rubric=[
            {"id": "Q1.a", "failure_type": "wrong_entity", "check": {"kind": "status_is", "value": "answerable"}},
        ])  # no validator_passes item


def test_the_real_question_file_is_valid():
    questions = load_questions(ROOT / "eval" / "questions.yaml")
    assert len(questions) == 12 and [q.id for q in questions] == [f"Q{n}" for n in range(1, 13)]


# --- Scoring a saved run -----------------------------------------------------


QUESTIONS_YAML = """
- id: QA
  question: "Answered well?"
  story: test
  expected_answer: "Yes."
  rubric:
    - { id: QA.status, failure_type: false_premise_accepted, check: { kind: status_is, value: false_premise } }
    - { id: QA.engine, failure_type: label_confusion, check: { kind: cites, section: confirmed_facts, evidence_id: INC-201 } }
    - { id: QA.judged, failure_type: unsupported_claim, check: { kind: judge, instruction: "Is fine." } }
    - { id: QA.valid, failure_type: citation_invalid, check: { kind: validator_passes } }
- id: QB
  question: "Wrong label?"
  story: test
  expected_answer: "No."
  rubric:
    - { id: QB.status, failure_type: false_premise_accepted, check: { kind: status_is, value: answerable } }
    - { id: QB.valid, failure_type: citation_invalid, check: { kind: validator_passes } }
- id: QC
  question: "The API failed?"
  story: test
  expected_answer: "Anything."
  rubric:
    - { id: QC.valid, failure_type: citation_invalid, check: { kind: validator_passes } }
"""


@pytest.fixture
def saved_run(tmp_path):
    """A run with one passing question, one failing, and one whose run failed."""
    questions = [Question.model_validate(q) for q in yaml.safe_load(QUESTIONS_YAML)]
    run_dir = new_run_dir(CONFIG, [{"path": "messages/extra", "loaded": False, "count": 45}], tmp_path)
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 4000}

    ok = AgentRun("Answered well?", steps=[Step(1, "claude-sonnet-5-5", "end_turn", usage=usage)], answer=ANSWER)
    save_transcript(run_dir, "QA", ok, [ValidationResult("V1_every_claim_has_evidence", "pass")], CONFIG)
    bad_validation = [ValidationResult("V4_cited_values_match", "fail", "wrong value")]
    save_transcript(run_dir, "QB", AgentRun("Wrong label?", answer=ANSWER), bad_validation, CONFIG)
    failed = AgentRun("The API failed?", status="failed", error="could not reach the API")
    save_transcript(run_dir, "QC", failed, [], CONFIG)
    write_json(run_dir / "judge.json", {"QA.judged": {"verdict": "pass", "reason": "fine", "model": "m"}})
    return run_dir, questions


def test_scores_and_failures(saved_run):
    run_dir, questions = saved_run
    report = score_run(run_dir, questions)
    by_id = {q["id"]: q for q in report["questions"]}
    assert by_id["QA"]["passed"] and by_id["QA"]["cache_read_tokens"] == 4000
    assert not by_id["QB"]["passed"]
    assert {i["id"] for i in by_id["QB"]["items"] if not i["passed"]} == {"QB.status", "QB.valid"}
    assert report["failure_types"]["citation_invalid"] == {"passed": 1, "total": 2, "rate": 0.5}


def test_a_failed_run_stays_in_the_denominator(saved_run):
    run_dir, questions = saved_run
    report = score_run(run_dir, questions)
    qc = {q["id"]: q for q in report["questions"]}["QC"]
    assert qc["run_failed"] and not qc["passed"] and qc["error"] == "could not reach the API"
    assert report["overall"] == {"passed": 1, "total": 3, "rate": 0.333}
    assert report["failure_types"]["run_failure"] == {"passed": 2, "total": 3, "rate": 0.667}
    assert "run_failure" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_rescoring_reproduces_the_report_with_no_key(saved_run, monkeypatch):
    run_dir, questions = saved_run
    first = score_run(run_dir, questions)
    first_md = (run_dir / "report.md").read_text(encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert score_run(run_dir, questions) == first
    assert (run_dir / "report.md").read_text(encoding="utf-8") == first_md
    assert json.loads((run_dir / "report.json").read_text(encoding="utf-8")) == first


def test_report_lists_each_failure_with_the_items_missed(saved_run):
    run_dir, questions = saved_run
    score_run(run_dir, questions)
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "### QB: Wrong label?" in md and "`QB.status`" in md and "`QB.valid`" in md
    assert "### QA" not in md
    assert "messages/extra | False | 45" in md


def test_a_run_is_rescored_with_the_rubric_it_was_run_with(saved_run):
    run_dir, questions = saved_run
    save_questions(run_dir, questions)
    first = score_run(run_dir, questions_for(run_dir))
    assert [q.id for q in questions_for(run_dir)] == ["QA", "QB", "QC"]  # its own copy, not eval/
    assert score_run(run_dir, questions_for(run_dir)) == first
