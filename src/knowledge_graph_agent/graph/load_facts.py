"""Load message facts into the graph, from cached extractions.

Resolution and rules run here, at load time, so fixing a rule needs no new
extraction. Run after load_tables(): facts link to table nodes.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neo4j import Driver, ManagedTransaction

from knowledge_graph_agent.config import Config
from knowledge_graph_agent.extract.llm import DEFAULT_CACHE_DIR, cached_extractions
from knowledge_graph_agent.extract.messages import COLLECTIONS, Message, directory_sha256, load_messages
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.extract.rules import (
    FactRecord,
    check_quote,
    drop_overlapping,
    link_conflicts,
    link_corrections,
    link_entities,
    link_reports,
    normalize_time,
    scan_injection,
    verification,
    voyage_match,
)
from knowledge_graph_agent.extract.schema import Extraction
from knowledge_graph_agent.graph.db import write_session
from knowledge_graph_agent.graph.load_tables import DEFAULT_DATA_DIR

Props = dict[str, Any]


@dataclass(frozen=True)
class Link:
    start: str
    type: str
    end: str
    props: Props = field(default_factory=dict)


@dataclass
class FactPlan:
    messages: list[Props] = field(default_factory=list)
    facts: list[Props] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    source_files: list[Props] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)  # message ID -> facts dropped


def collections_for(config: Config) -> tuple[str, ...]:
    return ("core", "extra") if config.messages.include_extra else ("core",)


def _message_props(message: Message, extraction: Extraction, flagged: list[str]) -> Props:
    return {
        "evidence_id": message.message_id,
        "source_ref": message.source_ref,
        "from_raw": message.from_raw,
        "to_raw": message.to_raw,
        "subject": message.subject,
        "body": message.body,
        "sent_utc": message.sent_utc,
        "sender_role": message.sender_role,
        "origin": message.origin,
        "collection": message.collection,
        "suspicious": bool(flagged or extraction.suspicious_instructions),
        "suspicious_quote": " | ".join(flagged) or None,
    }


def _fact_props(record: FactRecord, model: str, status: str, basis: str) -> Props:
    fact, time = record.fact, record.time
    return {
        "evidence_id": record.evidence_id,
        "source_ref": record.message.source_ref,
        "kind": fact.kind,
        "statement": fact.statement,
        "quote": fact.quote,
        "sender_role": record.message.sender_role,
        "sent_utc": record.message.sent_utc,
        "origin": record.origin,
        "incident_category": fact.incident_category,
        "hours_raw": fact.hours_raw,
        "hours_low": fact.hours_low,
        "hours_high": fact.hours_high,
        "event_time_raw": time.raw,
        "event_time_utc": time.utc,
        "time_precision": time.precision,
        "time_note": time.note or None,
        "vessel_class_ref": record.vessel_class,
        "correction_field": fact.correction_field,
        "verification": status,
        "verification_basis": basis,
        "voyage_match": voyage_match(record),
        "unresolved_refs": record.unresolved,
        "extraction_model": model,
    }


def build_fact_plan(
    messages: list[Message],
    extractions: dict[str, Extraction],
    ref: ReferenceData,
    model: str,
    loaded_collections: tuple[str, ...],
    data_dir: Path = DEFAULT_DATA_DIR,
) -> FactPlan:
    plan = FactPlan()
    records: list[FactRecord] = []

    for message in messages:
        extraction = extractions[message.stem]
        spans = scan_injection(message.body, extraction.suspicious_instructions)
        flagged = list(dict.fromkeys(span.text(message.body) for span in spans))  # unique, in order
        plan.messages.append(_message_props(message, extraction, flagged))

        kept, dropped = drop_overlapping(extraction.facts, message.body, spans)
        if dropped:
            plan.dropped[message.message_id] = len(dropped)
        for index in kept:
            fact = extraction.facts[index]
            if not check_quote(fact.quote, message.body):
                # The cache check should make this impossible; never load a bad quote.
                raise ValueError(f"{message.stem}: quote not in body: {fact.quote!r}")
            # IDs follow the cached order, so they stay stable when rules change.
            record = FactRecord(
                f"{message.message_id}#{index + 1}", message, fact,
                normalize_time(fact.event_time_raw, message.sent_utc),
            )
            link_entities(record, ref)
            records.append(record)

    reports = link_reports(records, ref)
    corrections = link_corrections(records, ref)
    conflicts = link_conflicts(records, reports)

    for record in records:
        status, basis = verification(record, reports, conflicts, records, ref)
        plan.facts.append(_fact_props(record, model, status, basis))
        fid = record.evidence_id
        plan.links.append(Link(record.message.message_id, "STATES", fid))
        for rel_type, resolutions in (
            ("ABOUT_VESSEL", record.vessels),
            ("ABOUT_VOYAGE", record.voyages),
            ("ABOUT_PORT", record.ports),
        ):
            seen = set()
            for r in resolutions:
                if r.entity_id not in seen:
                    seen.add(r.entity_id)
                    plan.links.append(Link(fid, rel_type, r.entity_id, {
                        "surface": r.surface, "method": r.method, "confidence": r.confidence,
                    }))

    for report in reports:
        plan.links.append(Link(report.fact_id, "REPORTS", report.incident_id, {
            "method": report.method, "confidence": report.confidence,
        }))
    for c in corrections:
        plan.links.append(Link(c.fact_id, "CORRECTS", c.incident_id, {
            "field": c.field, "supersedes": c.supersedes, "rule": c.rule,
        }))
    for c in conflicts:
        plan.links.append(Link(c.fact_a, "CONFLICTS_WITH", c.fact_b, {
            "field": c.field, "values": list(c.values),
        }))

    for collection in COLLECTIONS:
        directory = data_dir / "messages" / collection
        path = f"messages/{collection}"
        plan.source_files.append({
            "evidence_id": f"FILE:{path}",
            "source_ref": path,
            "path": path,
            "row_or_file_count": len(list(directory.glob("*.txt"))),
            "sha256": directory_sha256(directory),
            "loaded": collection in loaded_collections,
        })
    return plan


LINK_TYPES = ("STATES", "ABOUT_VESSEL", "ABOUT_VOYAGE", "ABOUT_PORT", "REPORTS", "CORRECTS", "CONFLICTS_WITH")


def _write(tx: ManagedTransaction, plan: FactPlan) -> dict[str, int]:
    # Replace any earlier message load, so this can run more than once.
    tx.run("MATCH (n) WHERE n:Message OR n:Fact DETACH DELETE n").consume()
    tx.run("MATCH (f:SourceFile) WHERE f.path STARTS WITH 'messages/' DELETE f").consume()

    for label, rows in (("Message", plan.messages), ("Fact", plan.facts), ("SourceFile", plan.source_files)):
        tx.run(f"UNWIND $rows AS row CREATE (n:Evidence:{label}) SET n = row", rows=rows).consume()

    created = {}
    for link_type in LINK_TYPES:
        rows = [{"start": l.start, "end": l.end, "props": l.props} for l in plan.links if l.type == link_type]
        record = tx.run(
            "UNWIND $rows AS row "
            "MATCH (a:Evidence {evidence_id: row.start}) "
            "MATCH (b:Evidence {evidence_id: row.end}) "
            f"CREATE (a)-[r:{link_type}]->(b) SET r = row.props "
            "RETURN count(*) AS n",
            rows=rows,
        ).single()
        n = record["n"] if record else 0
        if n != len(rows):
            raise ValueError(f"{link_type}: created {n} of {len(rows)} links; an endpoint is missing")
        created[link_type] = n
    return created


@dataclass(frozen=True)
class FactSummary:
    messages: int
    facts: int
    dropped: int
    links: dict[str, int]


def load_facts(
    driver: Driver,
    config: Config,
    data_dir: Path = DEFAULT_DATA_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> FactSummary:
    """Load cached facts for the configured collections. Raises
    MissingExtractionsError if any message has no valid cached extraction."""
    collections = collections_for(config)
    ref = ReferenceData.from_data(data_dir)
    messages = load_messages(ref, collections, data_dir)
    extractions = cached_extractions(messages, config.models.extraction, cache_dir)
    plan = build_fact_plan(messages, extractions, ref, config.models.extraction, collections, data_dir)

    with write_session(driver) as session:
        links = session.execute_write(_write, plan)
    return FactSummary(len(plan.messages), len(plan.facts), sum(plan.dropped.values()), links)
