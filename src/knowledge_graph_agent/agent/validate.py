"""Check an answer's citations against the graph: V1 to V10 in contracts/answer.md.

Runs after every answer. A failure is shown and recorded, never hidden by a
silent retry.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from neo4j import Driver

from knowledge_graph_agent.agent.answer import Answer, Claim
from knowledge_graph_agent.extract.resolve import contains_phrase, name_tokens
from knowledge_graph_agent.graph.db import read_session

Status = Literal["pass", "fail", "warn"]

NON_ANSWERS = {"false_premise", "entity_not_found", "cannot_answer"}
# Table rows with no status of their own: true as recorded.
UNSTATUSED_LABELS = {"Vessel", "Voyage", "PortCall", "Booking", "Port", "Customer", "CongestionSnapshot", "SourceFile"}


@dataclass(frozen=True)
class ValidationResult:
    check: str
    status: Status
    detail: str = ""


def passed(results: list[ValidationResult]) -> bool:
    """True when no check failed. Warnings don't fail an answer."""
    return not any(r.status == "fail" for r in results)


class GraphLookup(Protocol):
    def get(self, evidence_id: str) -> dict[str, Any] | None:
        """The node's properties plus `labels`, and for facts and messages,
        `message_body` and `message_suspicious`. None if it doesn't exist."""


class Neo4jLookup:
    def __init__(self, driver: Driver):
        self.driver = driver
        self._cache: dict[str, dict[str, Any] | None] = {}

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        if evidence_id not in self._cache:
            with read_session(self.driver) as session:
                record = session.run(
                    "MATCH (n:Evidence {evidence_id: $id}) "
                    "OPTIONAL MATCH (m:Message)-[:STATES]->(n) "
                    "WITH n, coalesce(m, CASE WHEN n:Message THEN n END) AS msg "
                    "RETURN properties(n) AS props, labels(n) AS labels, "
                    "msg.body AS body, msg.suspicious AS suspicious",
                    id=evidence_id,
                ).single()
            if record is None:
                self._cache[evidence_id] = None
            else:
                props = {k: v.to_native() if hasattr(v, "to_native") else v for k, v in record["props"].items()}
                self._cache[evidence_id] = props | {
                    "labels": record["labels"],
                    "message_body": record["body"],
                    "message_suspicious": record["suspicious"],
                }
        return self._cache[evidence_id]


def _result(check: str, problems: list[str], severity: Status = "fail") -> ValidationResult:
    if problems:
        return ValidationResult(check, severity, "; ".join(problems[:10]))
    return ValidationResult(check, "pass")


def _cited_ids(claim: Claim) -> list[str]:
    return list(dict.fromkeys(claim.evidence_ids + [v.evidence_id for v in claim.cited_values]))


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _value_matches(stored: Any, cited: Any) -> bool:
    """Numbers compare by value (55 equals 55.0), times after converting to
    UTC, and everything else exactly."""
    if isinstance(stored, bool) or isinstance(cited, bool):
        return stored == cited
    if isinstance(stored, int | float):
        try:
            return float(cited) == float(stored)
        except (TypeError, ValueError):
            return False
    if isinstance(stored, datetime):
        return _as_utc(cited) == _as_utc(stored)
    if hasattr(stored, "isoformat"):  # a date
        return str(cited) == stored.isoformat()
    return cited == stored


def _is_confirmed(node: dict[str, Any]) -> bool:
    labels = set(node["labels"])
    if "Incident" in labels:
        return node.get("status") == "Confirmed"
    if "Fact" in labels:
        return node.get("verification") == "confirmed"
    return bool(labels & UNSTATUSED_LABELS)


# Things in claim text that are not figures: IDs, dates and clock times.
_NOT_FIGURES = re.compile(
    r"\b[A-Z]{1,5}[-:][\w-]+(?:#\d+)?|\b\d{4}-\d{2}-\d{2}(?:T[\d:]+Z?)?|\b\d{1,2}:\d{2}\b"
)
# A number on its own, not digits inside an ID such as V001
_FIGURE = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?")


def validate(
    answer: Answer,
    retrieved_ids: set[str],
    lookup: GraphLookup,
    vessel_names: list[str],
    computed: dict[str, dict[str, Any]] | None = None,
) -> list[ValidationResult]:
    """Run V1 to V10. `retrieved_ids` are the IDs seen in this run's tool results,
    and `computed` the values tools worked out for each ID (AgentRun.computed)."""
    computed = computed or {}
    claims = answer.claims()
    cited = sorted({i for _, c in claims for i in _cited_ids(c)})
    nodes = {i: lookup.get(i) for i in cited}

    v1 = [f"{section}: {c.text[:60]!r}" for section, c in claims if not c.evidence_ids]
    v2 = [i for i in cited if nodes[i] is None]
    v3 = [i for i in cited if i not in retrieved_ids]

    v4 = []
    for _, claim in claims:
        for cv in claim.cited_values:
            node = nodes.get(cv.evidence_id)
            if node is None:
                continue  # already reported by V2
            if cv.field == "quote":
                sources = [node.get("quote"), node.get("message_body"), node.get("body")]
                if not any(isinstance(s, str) and str(cv.value) in s for s in sources):
                    v4.append(f"{cv.evidence_id}: quote not found")
            elif cv.field not in node:
                worked_out = computed.get(cv.evidence_id, {})
                if cv.value is None:
                    continue  # Neo4j doesn't store nulls: a blank field is absent
                if cv.field not in worked_out:
                    v4.append(f"{cv.evidence_id} has no field {cv.field!r}")
                elif not _value_matches(worked_out[cv.field], cv.value):
                    v4.append(f"{cv.evidence_id}.{cv.field} was computed as {worked_out[cv.field]!r}, cited as {cv.value!r}")
            elif not _value_matches(node[cv.field], cv.value):
                v4.append(f"{cv.evidence_id}.{cv.field} is {node[cv.field]!r}, cited as {cv.value!r}")

    v5 = [
        c.text[:60]
        for c in answer.confirmed_facts
        if not any(nodes.get(i) and _is_confirmed(nodes[i]) for i in _cited_ids(c))
    ]

    vessels = [name_tokens(n) for n in vessel_names]
    v6 = []
    for section in ("confirmed_facts", "unverified_reports"):
        for c in getattr(answer, section):
            found = [nodes.get(i) for i in _cited_ids(c)]
            only_congestion = found and all(n and "CongestionSnapshot" in n["labels"] for n in found)
            if only_congestion and any(contains_phrase(name_tokens(c.text), v) for v in vessels):
                v6.append(f"{section}: {c.text[:60]!r}")

    v7 = [c.text[:60] for c in answer.inferences if not c.based_on.strip()]

    suspicious = [i for i in cited if nodes.get(i) and nodes[i].get("message_suspicious")]
    v8 = [f"cites suspicious {', '.join(suspicious)} but suspicious_content is empty"] if (
        suspicious and not answer.suspicious_content
    ) else []

    v9 = [f"{answer.question_status} answer offers {len(answer.inferences)} inferences"] if (
        answer.question_status in NON_ANSWERS and answer.inferences
    ) else []

    v10 = []
    for c in answer.confirmed_facts:
        cited_text = " ".join(str(v.value) for v in c.cited_values)
        cited_numbers = {float(x) for x in _FIGURE.findall(cited_text)}
        for figure in _FIGURE.findall(_NOT_FIGURES.sub(" ", c.text)):
            if float(figure) not in cited_numbers:
                v10.append(f"{figure} in {c.text[:50]!r}")

    return [
        _result("V1_every_claim_has_evidence", v1),
        _result("V2_cited_ids_exist", v2),
        _result("V3_cited_ids_were_retrieved", v3),
        _result("V4_cited_values_match", v4),
        _result("V5_confirmed_claims_cite_confirmed_evidence", v5),
        _result("V6_port_wide_figures_not_vessel_facts", v6),
        _result("V7_inferences_have_a_basis", v7),
        _result("V8_suspicious_content_disclosed", v8),
        _result("V9_no_inferences_on_non_answers", v9),
        _result("V10_figures_are_cited", v10, severity="warn"),
    ]
