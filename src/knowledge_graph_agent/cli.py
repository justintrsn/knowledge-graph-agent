"""The `kg` command. Exit codes follow contracts/cli.md:

0  the steps ran
1  a verify failure, or a cache miss with no key
2  an LLM or database failure
"""

import anthropic
import typer
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from knowledge_graph_agent.config import has_api_key, load_config, load_env, neo4j_settings
from knowledge_graph_agent.extract.llm import ExtractionError, MissingExtractionsError, extract_messages
from knowledge_graph_agent.extract.messages import load_messages
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.db import connect
from knowledge_graph_agent.graph.load_facts import collections_for, load_facts
from knowledge_graph_agent.graph.load_tables import load_tables
from knowledge_graph_agent.graph.verify import all_passed, verify

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Ship delay operations assistant.")

EXIT_VERIFY_OR_CACHE = 1
EXIT_SERVICE = 2


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


@app.command("verify")
def verify_command() -> None:
    """Run the load-time checks. Exits 1 if any check fails."""
    driver = _driver()
    try:
        results = verify(driver, facts=True)
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
