"""The `kg` command. Exit codes follow contracts/cli.md:

0  the steps ran
1  a verify failure, or a cache miss with no key
2  an LLM or database failure
3  kg ask only: the answer was printed but failed validation
"""

import anthropic
import typer
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from knowledge_graph_agent.agent.answer import render
from knowledge_graph_agent.agent.loop import run_agent
from knowledge_graph_agent.agent.prompts import system_prompt
from knowledge_graph_agent.agent.tools import ToolContext, source_coverage
from knowledge_graph_agent.agent.transcript import new_run_dir, save_transcript
from knowledge_graph_agent.agent.validate import Neo4jLookup, validate
from knowledge_graph_agent.agent.transcript import DEFAULT_RUNS_DIR
from knowledge_graph_agent.config import has_api_key, load_config, load_env, neo4j_settings
from knowledge_graph_agent.evaluate.run import run_evaluation, select
from knowledge_graph_agent.evaluate.score import load_questions, questions_for, score_run
from knowledge_graph_agent.extract.llm import ExtractionError, MissingExtractionsError, extract_messages
from knowledge_graph_agent.extract.messages import load_messages
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.db import connect
from knowledge_graph_agent.graph.load_facts import collections_for, load_facts
from knowledge_graph_agent.graph.load_tables import load_tables
from knowledge_graph_agent.graph.verify import all_passed, verify
from knowledge_graph_agent.pipeline import run_pipeline

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Ship delay operations assistant.")

EXIT_VERIFY_OR_CACHE = 1
EXIT_SERVICE = 2
EXIT_ANSWER_INVALID = 3


def _fail(message: str, code: int) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code)


def _driver():
    try:
        return connect(neo4j_settings())
    except ServiceUnavailable as e:
        raise _fail(f"Can't reach Neo4j: {e}. Is it running? Try `docker compose up -d neo4j`.", EXIT_SERVICE)


@app.callback()
def _setup() -> None:
    load_env()


@app.command()
def load() -> None:
    """Clear the graph, load the tables, then the cached message facts."""
    config = load_config()
    driver = _driver()
    try:
        tables = load_tables(driver)
        typer.echo("Tables:")
        for label, n in tables.node_counts.items():
            typer.echo(f"  {label:20} {n}")
        facts = load_facts(driver, config)
        typer.echo(f"Messages: {facts.messages}  facts: {facts.facts}  dropped as suspicious: {facts.dropped}")
        for link_type, n in facts.links.items():
            typer.echo(f"  {link_type:20} {n}")
    except MissingExtractionsError as e:
        raise _fail(f"Tables loaded, but facts were not: {e}", EXIT_VERIFY_OR_CACHE)
    except Neo4jError as e:
        raise _fail(f"Database error: {e}", EXIT_SERVICE)
    finally:
        driver.close()


@app.command()
def extract(
    refresh: bool = typer.Option(False, "--refresh", help="Redo every message, not only missing or stale ones."),
    include_extra: bool = typer.Option(False, "--include-extra", help="Also process messages/extra."),
) -> None:
    """Fill the extraction cache using Claude. Needs ANTHROPIC_API_KEY for any message not yet cached."""
    config = load_config()
    if config.messages.use_cache and refresh:
        raise _fail("--refresh would call Claude, but use_cache is true in config.yaml.", EXIT_VERIFY_OR_CACHE)
    collections = ("core", "extra") if include_extra else collections_for(config)
    ref = ReferenceData.from_data()
    messages = load_messages(ref, collections)
    # With use_cache, no client: any message not cached is reported, never sent.
    client = anthropic.Anthropic() if has_api_key() and not config.messages.use_cache else None
    try:
        extractions = extract_messages(messages, config.models.extraction, client, refresh=refresh)
    except MissingExtractionsError as e:
        hint = " (use_cache is true in config.yaml, so Claude was not called.)" if config.messages.use_cache else ""
        raise _fail(f"{e}{hint}", EXIT_VERIFY_OR_CACHE)
    except (ExtractionError, anthropic.APIError) as e:
        raise _fail(f"Extraction failed: {e}", EXIT_SERVICE)
    facts = sum(len(x.facts) for x in extractions.values())
    typer.echo(f"{len(extractions)} messages cached, {facts} facts.")


@app.command()
def ask(
    question: str = typer.Argument(..., help="The question, in quotes."),
    as_json: bool = typer.Option(False, "--json", help="Print the answer as JSON instead of text."),
) -> None:
    """Answer one question from the graph, citing evidence. Calls Claude, so needs ANTHROPIC_API_KEY.

    Saves a transcript under outputs/runs/. Exit code 3 means the answer was
    printed but failed validation.
    """
    if not has_api_key():
        raise _fail("kg ask needs ANTHROPIC_API_KEY (in .env or the environment).", EXIT_VERIFY_OR_CACHE)
    config = load_config()
    driver = _driver()
    try:
        ctx = ToolContext(driver, ReferenceData.from_data(), config)
        coverage = source_coverage(ctx).rows
        run_dir = new_run_dir(config, coverage)
        run = run_agent(question, anthropic.Anthropic(), ctx, config, system_prompt(config))
        validation = []
        if run.answer:
            vessel_names = [v.name for v in ctx.ref.vessels.values()]
            validation = validate(run.answer, run.retrieved_ids, Neo4jLookup(driver), vessel_names, run.computed)
        status = save_transcript(run_dir, "ask", run, validation, config)
    except Neo4jError as e:
        raise _fail(f"Database error: {e}", EXIT_SERVICE)
    finally:
        driver.close()

    if run.answer is None:
        raise _fail(f"No answer: {run.error}\nTranscript: {run_dir / 'ask.md'}", EXIT_SERVICE)
    if status == "failed_validation":
        typer.secho("WARNING: this answer FAILED validation; do not rely on it.\n", fg=typer.colors.RED, bold=True)
    typer.echo(run.raw_answer if as_json else render(run.answer))
    for r in validation:
        if r.status != "pass":
            typer.secho(f"{r.status.upper()} {r.check}: {r.detail}", fg=typer.colors.RED if r.status == "fail" else typer.colors.YELLOW, err=True)
    tokens = sum(s.usage.get("input_tokens", 0) + s.usage.get("output_tokens", 0) for s in run.steps)
    typer.echo(f"\n[{status}] {run.tool_steps} tool steps, {tokens} tokens. Transcript: {run_dir / 'ask.md'}", err=True)
    if status == "failed_validation":
        raise typer.Exit(EXIT_ANSWER_INVALID)


def _report_summary(run_dir) -> int:
    """Print the report's headline; return the exit code (2 if any question's run failed)."""
    report = score_run(run_dir, questions_for(run_dir))
    overall = report["overall"]
    typer.echo(f"Passed {overall['passed']} of {overall['total']} questions. Report: {run_dir / 'report.md'}")
    failed_runs = [q["id"] for q in report["questions"] if q["run_failed"]]
    if failed_runs:
        typer.secho(f"Runs that failed (recorded, not retried): {', '.join(failed_runs)}", fg=typer.colors.RED, err=True)
        return EXIT_SERVICE
    return 0


@app.command("run")
def run_command() -> None:
    """The whole pipeline: load, verify, then the evaluation if a key is set.

    With a key, calls Claude for every question. Without one, prints a notice
    naming the delivered run instead.
    """
    config = load_config()
    driver = _driver()
    try:
        result = run_pipeline(driver, config, echo=typer.echo)
    except MissingExtractionsError as e:
        raise _fail(f"Facts were not loaded: {e}", EXIT_VERIFY_OR_CACHE)
    except Neo4jError as e:
        raise _fail(f"Database error: {e}", EXIT_SERVICE)
    finally:
        driver.close()
    if not result.verified:
        raise typer.Exit(EXIT_VERIFY_OR_CACHE)
    if result.run_dir:
        raise typer.Exit(_report_summary(result.run_dir))


@app.command("eval")
def eval_command(
    ids: str = typer.Option("", "--ids", help="Only these questions, e.g. Q1,Q3."),
    rescore: str = typer.Option("", "--rescore", help="Recompute the report of a saved run. No key or Neo4j needed."),
) -> None:
    """Run the evaluation set as a new run, or rescore a saved one.

    A new run calls Claude for each question and judge item. --rescore uses only
    the saved answers and verdicts.
    """
    if rescore:
        run_dir = DEFAULT_RUNS_DIR / rescore
        if not (run_dir / "manifest.json").exists():
            raise _fail(f"No saved run at {run_dir}", EXIT_VERIFY_OR_CACHE)
        raise typer.Exit(_report_summary(run_dir))

    if not has_api_key():
        raise _fail("kg eval needs ANTHROPIC_API_KEY. To rescore a saved run, use --rescore <run_id>.", EXIT_VERIFY_OR_CACHE)
    try:
        questions = select(load_questions(), [i.strip() for i in ids.split(",") if i.strip()])
    except ValueError as e:
        raise _fail(str(e), EXIT_VERIFY_OR_CACHE)
    config = load_config()
    driver = _driver()
    try:
        ctx = ToolContext(driver, ReferenceData.from_data(), config)
        run_dir = run_evaluation(questions, ctx, anthropic.Anthropic(), config, echo=typer.echo)
    except Neo4jError as e:
        raise _fail(f"Database error: {e}", EXIT_SERVICE)
    finally:
        driver.close()
    raise typer.Exit(_report_summary(run_dir))


@app.command("verify")
def verify_command() -> None:
    """Run the load-time checks. Exits 1 if any check fails."""
    collections = collections_for(load_config())
    driver = _driver()
    try:
        results = verify(driver, facts=True, collections=collections)
    except Neo4jError as e:
        raise _fail(f"Database error: {e}", EXIT_SERVICE)
    finally:
        driver.close()
    colors = {"pass": typer.colors.GREEN, "fail": typer.colors.RED, "skip": typer.colors.YELLOW}
    for r in results:
        typer.secho(f"{r.status:5} {r.name:22} {r.detail}", fg=colors[r.status])
    if not all_passed(results):
        raise typer.Exit(EXIT_VERIFY_OR_CACHE)


if __name__ == "__main__":
    app()
