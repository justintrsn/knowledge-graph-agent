"""Load-time checks from data-model.md ("Load-time checks").

Each check returns a named result. The caller decides what to do with
failures; the pipeline stops on any of them.

Message and fact checks run only with `facts=True`. Until facts are loaded
(T026) they are reported as skipped, never as passed.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from neo4j import Driver, Session

from knowledge_graph_agent.graph.db import read_session
from knowledge_graph_agent.graph.load_tables import DEFAULT_DATA_DIR, TABLE_FILES, sha256_of

Status = Literal["pass", "fail", "skip"]

CORE_MESSAGE_COUNT = 15
LOOKALIKE_VESSELS = ("MV Ocean Star", "MV Ocean Starlight", "MV Ocean Pearl")
# ABOUT_VESSEL.method for a link made from a name alone. The extraction rules
# (T021, T024) must write this same value.
NAME_MATCH_METHOD = "name"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str = ""


def all_passed(results: list[CheckResult]) -> bool:
    """True when nothing failed. Skipped checks are not failures."""
    return not any(r.status == "fail" for r in results)


def _result(name: str, problems: list[str], ok_detail: str = "") -> CheckResult:
    if problems:
        return CheckResult(name, "fail", "; ".join(problems))
    return CheckResult(name, "pass", ok_detail)


def _scalar(session: Session, query: str, **params) -> int:
    return session.run(query, **params).single(strict=True)[0]


def _csv_row_count(path: Path) -> int:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


# --- Table checks -----------------------------------------------------------


def check_row_counts(session: Session, data_dir: Path) -> CheckResult:
    """SC-001: one node per table row, counted from the files themselves."""
    problems = []
    total = 0
    for label, file_name in TABLE_FILES.items():
        expected = _csv_row_count(data_dir / file_name)
        actual = _scalar(session, f"MATCH (n:{label}) RETURN count(n)")
        total += actual
        if actual != expected:
            problems.append(f"{label}: {actual} nodes, {file_name} has {expected} rows")

    with open(data_dir / TABLE_FILES["Booking"], encoding="utf-8-sig", newline="") as f:
        expected_customers = len({row["customer"] for row in csv.DictReader(f)})
    customers = _scalar(session, "MATCH (c:Customer) RETURN count(c)")
    if customers != expected_customers:
        problems.append(f"Customer: {customers} nodes, bookings name {expected_customers}")
    return _result("row_counts", problems, f"{total} table rows loaded")


def check_customer_links(session: Session) -> CheckResult:
    """Every booking is held by exactly one customer with the booking's name."""
    problems = []
    bad = _scalar(
        session,
        "MATCH (b:Booking) "
        "OPTIONAL MATCH (c:Customer)-[:HOLDS]->(b) "
        "WITH b, collect(c) AS holders "
        "WHERE size(holders) <> 1 OR holders[0].name <> b.customer "
        "RETURN count(b)",
    )
    if bad:
        problems.append(f"{bad} bookings without exactly one matching customer")
    idle = _scalar(session, "MATCH (c:Customer) WHERE NOT (c)-[:HOLDS]->() RETURN count(c)")
    if idle:
        problems.append(f"{idle} customers hold no booking")
    return _result("customer_links", problems)


def check_evidence_and_source(session: Session) -> CheckResult:
    """SC-001: every node is citable and says where it came from."""
    missing = _scalar(
        session,
        "MATCH (n) WHERE n.evidence_id IS NULL OR n.source_ref IS NULL OR NOT n:Evidence "
        "RETURN count(n)",
    )
    problems = [f"{missing} nodes lack evidence_id, source_ref or the Evidence label"] if missing else []
    return _result("evidence_and_source", problems)


# (description, query counting violations)
_TABLE_LINK_RULES = [
    (
        "voyages without one vessel, one origin and one destination",
        "MATCH (v:Voyage) WHERE "
        "COUNT { (v)-[:OPERATED_BY]->(:Vessel) } <> 1 OR "
        "COUNT { (v)-[:FROM]->(:Port) } <> 1 OR "
        "COUNT { (v)-[:TO]->(:Port) } <> 1 "
        "RETURN count(v)",
    ),
    (
        "port calls without one voyage and one port",
        "MATCH (p:PortCall) WHERE "
        "COUNT { (p)-[:ON_VOYAGE]->(:Voyage) } <> 1 OR COUNT { (p)-[:AT_PORT]->(:Port) } <> 1 "
        "RETURN count(p)",
    ),
    (
        "incidents without one voyage",
        "MATCH (i:Incident) WHERE COUNT { (i)-[:ON_VOYAGE]->(:Voyage) } <> 1 RETURN count(i)",
    ),
    (
        "bookings without one voyage",
        "MATCH (b:Booking) WHERE COUNT { (b)-[:ON_VOYAGE]->(:Voyage) } <> 1 RETURN count(b)",
    ),
    (
        "congestion snapshots without one port",
        "MATCH (c:CongestionSnapshot) WHERE COUNT { (c)-[:AT_PORT]->(:Port) } <> 1 "
        "RETURN count(c)",
    ),
    (
        "congestion snapshots linked to a vessel or voyage (FR-014)",
        "MATCH (c:CongestionSnapshot)--(x) WHERE x:Vessel OR x:Voyage RETURN count(c)",
    ),
    (
        "rolled bookings whose named voyage has no ROLLED_TO link",
        "MATCH (b:Booking) WHERE b.status_raw CONTAINS 'VG-' "
        "AND NOT (b)-[:ROLLED_TO]->(:Voyage) RETURN count(b)",
    ),
    (
        "ROLLED_TO links from a status that names no voyage",
        "MATCH (b:Booking)-[:ROLLED_TO]->() WHERE NOT b.status_raw CONTAINS 'VG-' "
        "RETURN count(b)",
    ),
]

_FACT_LINK_RULE = (
    "facts without a message",
    "MATCH (f:Fact) WHERE NOT (:Message)-[:STATES]->(f) RETURN count(f)",
)


def check_relationships(session: Session, facts: bool) -> CheckResult:
    rules = _TABLE_LINK_RULES + ([_FACT_LINK_RULE] if facts else [])
    problems = []
    for description, query in rules:
        n = _scalar(session, query)
        if n:
            problems.append(f"{n} {description}")
    return _result("relationships", problems)


def check_table_source_files(session: Session, data_dir: Path) -> CheckResult:
    """Each table has a SourceFile whose hash and count match the file on disk."""
    problems = []
    for file_name in TABLE_FILES.values():
        record = session.run(
            "MATCH (f:SourceFile {path: $path}) "
            "RETURN f.sha256 AS sha256, f.row_or_file_count AS count, f.loaded AS loaded",
            path=file_name,
        ).single()
        if record is None:
            problems.append(f"no SourceFile for {file_name}")
            continue
        path = data_dir / file_name
        if record["sha256"] != sha256_of(path):
            problems.append(f"{file_name} changed since it was loaded")
        if record["count"] != _csv_row_count(path):
            problems.append(f"{file_name}: SourceFile count {record['count']} is wrong")
        if record["loaded"] is not True:
            problems.append(f"{file_name}: SourceFile not marked loaded")
    return _result("table_source_files", problems)


def check_voyage_delays(session: Session) -> CheckResult:
    """recorded_delay_hours equals real elapsed time between the UTC ETAs."""
    rows = session.run(
        "MATCH (v:Voyage) "
        "WHERE v.original_eta_utc IS NOT NULL AND v.revised_eta_utc IS NOT NULL "
        "WITH v, duration.inSeconds(v.original_eta_utc, v.revised_eta_utc).seconds / 3600.0 AS hours "
        "WHERE v.recorded_delay_hours IS NULL OR hours <> v.recorded_delay_hours "
        "RETURN v.evidence_id AS id, hours, v.recorded_delay_hours AS recorded "
        "ORDER BY id"
    ).data()
    problems = [f"{r['id']}: recorded {r['recorded']}h, elapsed {r['hours']}h" for r in rows]
    return _result("voyage_delays", problems)


def check_distinct_vessels(session: Session, facts: bool) -> CheckResult:
    """Look-alike names stay separate vessels, and a name alone links one vessel."""
    problems = []
    for name in LOOKALIKE_VESSELS:
        n = _scalar(session, "MATCH (v:Vessel {name: $name}) RETURN count(v)", name=name)
        if n != 1:
            problems.append(f"{n} vessels named {name}")
    if facts:
        n = _scalar(
            session,
            "MATCH (f:Fact)-[r:ABOUT_VESSEL {method: $method}]->(:Vessel) "
            "WITH f, count(r) AS links WHERE links > 1 RETURN count(f)",
            method=NAME_MATCH_METHOD,
        )
        if n:
            problems.append(f"{n} facts linked to more than one vessel by name alone")
    return _result("distinct_vessels", problems)


# --- Message and fact checks ------------------------------------------------


def check_fact_quotes(session: Session) -> CheckResult:
    """SC-007: every quote appears word for word in its message."""
    bad = session.run(
        "MATCH (m:Message)-[:STATES]->(f:Fact) "
        "WHERE f.quote IS NULL OR NOT m.body CONTAINS f.quote "
        "RETURN f.evidence_id AS id ORDER BY id LIMIT 20"
    ).data()
    return _result("fact_quotes", [f"{r['id']} quote not in its message" for r in bad])


def check_core_messages(session: Session, data_dir: Path) -> CheckResult:
    problems = []
    n = _scalar(session, "MATCH (m:Message {collection: 'core'}) RETURN count(m)")
    if n != CORE_MESSAGE_COUNT:
        problems.append(f"{n} core messages loaded, expected {CORE_MESSAGE_COUNT}")
    record = session.run(
        "MATCH (f:SourceFile {path: 'messages/core'}) "
        "RETURN f.row_or_file_count AS count, f.loaded AS loaded"
    ).single()
    if record is None:
        problems.append("no SourceFile for messages/core")
    elif record["loaded"] is not True or record["count"] != CORE_MESSAGE_COUNT:
        problems.append(f"messages/core SourceFile: count {record['count']}, loaded {record['loaded']}")
    on_disk = len(list((data_dir / "messages" / "core").glob("*.txt")))
    if on_disk != CORE_MESSAGE_COUNT:
        problems.append(f"{on_disk} core message files on disk")
    return _result("core_messages", problems)


def _skipped(name: str) -> CheckResult:
    return CheckResult(name, "skip", "facts not loaded")


# --- Entry point ------------------------------------------------------------


def verify(driver: Driver, data_dir: Path = DEFAULT_DATA_DIR, facts: bool = False) -> list[CheckResult]:
    """Run every load-time check. Read-only."""
    with read_session(driver) as session:
        results = [
            check_row_counts(session, data_dir),
            check_customer_links(session),
            check_evidence_and_source(session),
            check_relationships(session, facts),
            check_table_source_files(session, data_dir),
            check_voyage_delays(session),
            check_distinct_vessels(session, facts),
        ]
        if facts:
            results += [check_fact_quotes(session), check_core_messages(session, data_dir)]
        else:
            results += [_skipped("fact_quotes"), _skipped("core_messages")]
    return results
