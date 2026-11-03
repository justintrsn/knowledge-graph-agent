"""Tools the agent can call. Each is one function plus one strict schema.

Every tool runs fixed, parameterized Cypher in a read-only session, and returns
the common result shape from contracts/agent-tools.md:

    rows          each with evidence_id and source_ref
    evidence_ids  everything in rows that can be cited
    searched      SourceFile IDs looked at; an empty result cites these
    truncated     whether rows were cut off
    note          anything the model should know about the result

To add a tool: write a function taking (ctx, **params) that returns a
ToolResult, and decorate it with @tool.
"""

import difflib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from neo4j import Driver
from neo4j.exceptions import Neo4jError
from neo4j.time import Date, DateTime, Duration, Time

from knowledge_graph_agent.config import Config
from knowledge_graph_agent.extract.resolve import ReferenceData, resolve_port, resolve_vessel
from knowledge_graph_agent.graph.db import read_session


@dataclass
class ToolContext:
    driver: Driver
    ref: ReferenceData
    config: Config


@dataclass
class ToolResult:
    rows: list[dict[str, Any]] = field(default_factory=list)  # graph properties only, so every field is citable
    searched: list[str] = field(default_factory=list)
    truncated: bool = False
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # tool-specific fields, e.g. suggestions

    def to_dict(self) -> dict[str, Any]:
        ids = [r["evidence_id"] for r in self.rows if r.get("evidence_id")]
        return {
            "rows": self.rows,
            "evidence_ids": list(dict.fromkeys(ids)),
            "searched": self.searched,
            "truncated": self.truncated,
            "note": self.note,
            **self.extra,
        }


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[..., ToolResult]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, **params: dict[str, Any]):
    """Register a tool. Each keyword is one parameter's JSON schema.

    Strict tool use needs every parameter listed as required, so an optional
    parameter is written with a null type, e.g. {"type": ["string", "null"]}.
    """
    schema = {
        "type": "object",
        "properties": params,
        "required": list(params),
        "additionalProperties": False,
    }

    def register(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        REGISTRY[name] = Tool(name, description, schema, fn)
        return fn

    return register


def definitions() -> list[dict[str, Any]]:
    """Tool definitions for the API, in a fixed order so the prompt cache holds."""
    return [REGISTRY[name].definition() for name in sorted(REGISTRY)]


def execute(ctx: ToolContext, name: str, params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Run a tool. Returns (result, is_error). Errors are returned, never raised,
    so the model can read the reason and correct itself."""
    tool_ = REGISTRY.get(name)
    if tool_ is None:
        return {"error": f"Unknown tool {name!r}. Available: {sorted(REGISTRY)}"}, True
    expected = set(tool_.input_schema["properties"])
    if set(params) != expected:
        return {"error": f"{name} takes exactly {sorted(expected)}; got {sorted(params)}"}, True
    try:
        return to_plain(tool_.run(ctx, **params).to_dict()), False
    except Neo4jError as e:
        return {"error": f"Database error in {name}: {e.message}"}, True


# --- Helpers ------------------------------------------------------------------


def to_plain(value: Any) -> Any:
    """Neo4j and Python values to JSON-friendly ones. Times become ISO 8601."""
    if isinstance(value, DateTime | Date | Time):
        return value.iso_format()
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Duration):
        return str(value)
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_plain(v) for v in value]
    return value


def query(ctx: ToolContext, cypher: str, **params: Any) -> list[dict[str, Any]]:
    with read_session(ctx.driver) as session:
        return [to_plain(r.data()) for r in session.run(cypher, **params)]


def node(ctx: ToolContext, label: str, evidence_id: str) -> dict[str, Any] | None:
    rows = query(ctx, f"MATCH (n:{label} {{evidence_id: $id}}) RETURN properties(n) AS n", id=evidence_id)
    return rows[0]["n"] if rows else None


def close_names(written: str, names: list[str], n: int = 3) -> list[str]:
    """Similar names, for a 'did you mean' hint. Never treated as matches."""
    lowered = {name.casefold(): name for name in names}
    return [lowered[m] for m in difflib.get_close_matches(written.casefold(), list(lowered), n=n, cutoff=0.6)]


# --- Tools --------------------------------------------------------------------


def _match_info(match) -> dict[str, Any]:
    """How a name was matched. Kept out of the rows: it is not a graph field."""
    return {"match": {"method": match.method, "confidence": match.confidence}}


@tool(
    "find_vessel",
    "Find a vessel by name, a variant of its name, an abbreviation, or its IMO number. "
    "Returns the vessel's row, and in `match` how it was matched and how confident that is (not citable). If nothing "
    "matches, rows are empty and `suggestions` lists similar names: these are NOT matches.",
    query={"type": "string", "description": "The vessel as the user wrote it, e.g. 'MV Ocean Star' or 'IMO 9876543'."},
)
def find_vessel(ctx: ToolContext, query: str) -> ToolResult:
    match = resolve_vessel(query, ctx.ref, context=query)
    if match.resolved:
        vessel = node(ctx, "Vessel", match.entity_id)
        return ToolResult(rows=[vessel], searched=["FILE:vessels.csv"], extra=_match_info(match))
    names = [v.name for v in ctx.ref.vessels.values()]
    return ToolResult(
        searched=["FILE:vessels.csv"],
        note=f"No vessel matches {query!r} ({match.note}).",
        extra={"suggestions": close_names(query, names)},
    )


@tool(
    "find_port",
    "Find a port by UN/LOCODE (e.g. NLRTM) or name. If nothing matches, rows are empty "
    "and `suggestions` lists similar names: these are NOT matches.",
    query={"type": "string", "description": "The port as the user wrote it."},
)
def find_port(ctx: ToolContext, query: str) -> ToolResult:
    match = resolve_port(query, ctx.ref)
    if match.resolved:
        port = node(ctx, "Port", match.entity_id)
        return ToolResult(rows=[port], searched=["FILE:ports.csv"], extra=_match_info(match))
    names = [p.name for p in ctx.ref.ports.values()]
    return ToolResult(
        searched=["FILE:ports.csv"],
        note=f"No port matches {query!r} ({match.note}).",
        extra={"suggestions": close_names(query, names)},
    )


@tool(
    "source_coverage",
    "List every data source: each table and message folder, whether it was loaded, and "
    "how many rows or files it has. Call this for any question about current or future "
    "events, and cite it when saying something is absent from the data.",
)
def source_coverage(ctx: ToolContext) -> ToolResult:
    rows = query(
        ctx,
        "MATCH (f:SourceFile) RETURN f.evidence_id AS evidence_id, f.source_ref AS source_ref, "
        "f.path AS path, f.loaded AS loaded, f.row_or_file_count AS count ORDER BY f.path",
    )
    not_loaded = [r["path"] for r in rows if not r["loaded"]]
    note = f"Not loaded: {', '.join(not_loaded)}." if not_loaded else None
    return ToolResult(rows=rows, searched=[r["evidence_id"] for r in rows], note=note)


def result_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)
