"""`kg run`: the whole pipeline in one command (contracts/cli.md).

1. Load the tables, then the message facts from the cache.
2. Verify. Any failure stops the run.
3. With a key (and KG_LIVE not off): run the evaluation as a new run.
4. Without: say live answering was skipped, and name the delivered run, which
   is not the output of this run (FR-022).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anthropic
from neo4j import Driver

from knowledge_graph_agent.agent.tools import ToolContext
from knowledge_graph_agent.agent.transcript import DEFAULT_RUNS_DIR
from knowledge_graph_agent.config import Config, live_enabled
from knowledge_graph_agent.evaluate.run import run_evaluation
from knowledge_graph_agent.evaluate.score import load_questions
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.load_facts import collections_for, load_facts
from knowledge_graph_agent.graph.load_tables import load_tables
from knowledge_graph_agent.graph.verify import all_passed, verify

DELIVERED = Path("outputs/DELIVERED")


@dataclass
class PipelineResult:
    verified: bool
    run_dir: Path | None = None  # the new evaluation run, if one ran


def delivered_notice(delivered: Path = DELIVERED, runs_dir: Path = DEFAULT_RUNS_DIR) -> str:
    """What to say when live answering is skipped."""
    skipped = "Live answering was skipped: no ANTHROPIC_API_KEY, or KG_LIVE=off. The graph was rebuilt and verified."
    run_id = delivered.read_text(encoding="utf-8").strip() if delivered.exists() else ""
    manifest = runs_dir / run_id / "manifest.json"
    if not run_id or not manifest.exists():
        return f"{skipped}\nNo delivered evaluation run is recorded in {delivered}."
    created = json.loads(manifest.read_text(encoding="utf-8"))["created_utc"]
    return (
        f"{skipped}\n"
        f"The delivered evaluation is run {run_id}, made on {created}: {runs_dir / run_id / 'report.md'}\n"
        "It is a saved earlier run, NOT the output of this run."
    )


def run_pipeline(driver: Driver, config: Config, echo: Callable[[str], None] = print) -> PipelineResult:
    tables = load_tables(driver)
    facts = load_facts(driver, config)
    echo("Tables:")
    for label, n in tables.node_counts.items():
        echo(f"  {label:20} {n}")
    echo(f"Messages: {facts.messages}  facts: {facts.facts}")

    results = verify(driver, facts=True, collections=collections_for(config))
    for r in results:
        echo(f"{r.status:5} {r.name:22} {r.detail}")
    if not all_passed(results):
        echo("Verify failed; stopping.")
        return PipelineResult(verified=False)

    if not live_enabled():
        echo(delivered_notice())
        return PipelineResult(verified=True)

    ctx = ToolContext(driver, ReferenceData.from_data(), config)
    run_dir = run_evaluation(load_questions(), ctx, anthropic.Anthropic(), config, echo=echo)
    echo(f"Report: {run_dir / 'report.md'}")
    return PipelineResult(verified=True, run_dir=run_dir)
