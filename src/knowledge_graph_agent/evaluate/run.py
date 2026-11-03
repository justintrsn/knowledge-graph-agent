"""Run the evaluation set as a new run (contracts/evaluation.md).

Each question is asked once. A failed run or a failed validation is recorded and
stays in the denominator: nothing is retried until it passes.
"""

from collections.abc import Callable
from pathlib import Path

import anthropic

from knowledge_graph_agent.agent.loop import run_agent
from knowledge_graph_agent.agent.prompts import system_prompt
from knowledge_graph_agent.agent.tools import ToolContext, source_coverage
from knowledge_graph_agent.agent.transcript import DEFAULT_RUNS_DIR, new_run_dir, save_transcript, write_json
from knowledge_graph_agent.agent.validate import Neo4jLookup, validate
from knowledge_graph_agent.config import Config
from knowledge_graph_agent.evaluate.judge import judge_question
from knowledge_graph_agent.evaluate.score import Question, save_questions, score_run


def select(questions: list[Question], ids: list[str] | None) -> list[Question]:
    if not ids:
        return questions
    unknown = set(ids) - {q.id for q in questions}
    if unknown:
        raise ValueError(f"Unknown question IDs: {sorted(unknown)}")
    return [q for q in questions if q.id in ids]


def run_evaluation(
    questions: list[Question],
    ctx: ToolContext,
    client: anthropic.Anthropic,
    config: Config,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    echo: Callable[[str], None] = print,
) -> Path:
    """Ask each question, validate, judge, then score. Returns the run directory."""
    run_dir = new_run_dir(config, source_coverage(ctx).rows, runs_dir)
    save_questions(run_dir, questions)
    system = system_prompt(config)
    lookup = Neo4jLookup(ctx.driver)
    vessel_names = [v.name for v in ctx.ref.vessels.values()]
    verdicts: dict[str, dict[str, str]] = {}

    for question in questions:
        run = run_agent(question.question, client, ctx, config, system)
        validation = validate(run.answer, run.retrieved_ids, lookup, vessel_names, run.computed) if run.answer else []
        status = save_transcript(run_dir, question.id, run, validation, config)
        if run.answer:
            verdicts |= judge_question(client, config.models.judge, question, run.answer)
        write_json(run_dir / "judge.json", verdicts)  # after every question, so a crash keeps what's done
        echo(f"{question.id}: {status}, {run.tool_steps} tool steps")

    score_run(run_dir, questions)
    return run_dir
