"""Load the seven CSV tables into Neo4j, per data-model.md.

Every run starts from an empty graph: the graph is fully derived from files,
so a reload gives the same result each time.

`build_plan()` turns the files into plain node and relationship records with no
database involved, and `load_tables()` writes that plan.
"""

import csv
import hashlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from neo4j import Driver, ManagedTransaction

from knowledge_graph_agent.graph.db import write_session
from knowledge_graph_agent.timeutil import local_to_utc, parse_utc

DEFAULT_DATA_DIR = Path("data")

# Label -> source file. verify.py uses this to compare counts with the files.
TABLE_FILES: dict[str, str] = {
    "Vessel": "vessels.csv",
    "Port": "ports.csv",
    "Voyage": "voyages.csv",
    "PortCall": "port_calls.csv",
    "Incident": "incidents.csv",
    "CongestionSnapshot": "congestion.csv",
    "Booking": "bookings.csv",
}

# Relationship types written here. Cypher can't take a type as a parameter, so
# only these fixed names are ever put into a query.
RELATIONSHIP_TYPES = ("OPERATED_BY", "FROM", "TO", "ON_VOYAGE", "AT_PORT", "HOLDS", "ROLLED_TO")

UNKNOWN_ORIGIN = "unknown"

Props = dict[str, Any]


@dataclass
class Relationship:
    start: str  # evidence_id
    type: str
    end: str  # evidence_id


@dataclass
class SourceFileRecord:
    path: str
    row_or_file_count: int
    sha256: str


@dataclass
class LoadPlan:
    nodes: dict[str, list[Props]] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    source_files: list[SourceFileRecord] = field(default_factory=list)


@dataclass(frozen=True)
class LoadSummary:
    node_counts: dict[str, int]
    relationship_counts: dict[str, int]


# --- Value conversion -------------------------------------------------------


def _text(value: str | None) -> str | None:
    """Blank stays null, never an empty string."""
    if value is None or not value.strip():
        return None
    return value.strip()


def _int(value: str | None) -> int | None:
    """Blank stays null, never 0."""
    text = _text(value)
    return None if text is None else int(text)


def _date(value: str | None) -> date | None:
    text = _text(value)
    return None if text is None else date.fromisoformat(text)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_vessel_name(name: str) -> str:
    """'MV Ocean Star' -> 'ocean star', for matching names written in messages."""
    text = " ".join(name.split()).casefold()
    return text.removeprefix("mv ")


def customer_id(name: str) -> str:
    """'Halden Retail' -> 'CUST-halden-retail'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        raise ValueError(f"customer name {name!r} gives an empty ID")
    return f"CUST-{slug}"


_VOYAGE_REF = re.compile(r"\bVG-\d+\b")


def normalize_booking_status(raw: str) -> tuple[str, str | None]:
    """Return (normalized status, voyage the booking was rolled to, if named).

    'Rolled to next sailing' is rolled with no target: the text names none.
    An unrecognized status is an error rather than a guess.
    """
    text = raw.strip()
    lowered = text.casefold()
    if lowered == "confirmed":
        return "confirmed", None
    if lowered == "cancelled":
        return "cancelled", None
    if lowered.startswith("rolled"):
        match = _VOYAGE_REF.search(text)
        return "rolled", match.group(0) if match else None
    raise ValueError(f"unknown booking status {raw!r}")


def incident_origin(source: str, vessel_id: str, technical_manager: str) -> str:
    """Who an incident row's information came from, per research.md.

    Master and crew of a vessel share one origin. A technical manager row comes
    from that vessel's manager. The table does not name the agent behind an
    agent email or port agent row, so those origins are unknown.
    """
    match source.strip().casefold():
        case "master's report" | "crew message":
            return f"vessel:{vessel_id}"
        case "technical manager":
            return f"org:{technical_manager}"
        case _:
            return UNKNOWN_ORIGIN


# --- Reading files ----------------------------------------------------------


def _rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (CSV line number, row). The header is line 1."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # line_num is the line a record ends on, so it stays right even if
            # a quoted value ever spans lines.
            yield reader.line_num, row


def _base(evidence_id: str, file_name: str, line: int) -> Props:
    return {"evidence_id": evidence_id, "source_ref": f"{file_name}:{line}"}


def build_plan(data_dir: Path = DEFAULT_DATA_DIR) -> LoadPlan:
    """Read every table and return the nodes and relationships to write."""
    plan = LoadPlan()
    rels = plan.relationships

    def table(label: str, convert: Callable[[int, dict[str, str]], Props]) -> list[Props]:
        file_name = TABLE_FILES[label]
        path = data_dir / file_name
        nodes = [convert(line, row) for line, row in _rows(path)]
        plan.nodes[label] = nodes
        plan.source_files.append(SourceFileRecord(file_name, len(nodes), sha256_of(path)))
        return nodes

    # Vessels
    vessels: dict[str, Props] = {}

    def vessel(line: int, r: dict[str, str]) -> Props:
        props = _base(r["vessel_id"], "vessels.csv", line) | {
            "vessel_id": r["vessel_id"],
            "name": r["name"],
            "name_normalized": normalize_vessel_name(r["name"]),
            "imo": _text(r["imo"]),  # an identifier, so kept as text
            "type": _text(r["type"]),
            "vessel_class": _text(r["vessel_class"]),
            "technical_manager": _text(r["technical_manager"]),
        }
        vessels[r["vessel_id"]] = props
        return props

    table("Vessel", vessel)

    # Ports
    zones: dict[str, str] = {}

    def port(line: int, r: dict[str, str]) -> Props:
        zones[r["port_code"]] = r["timezone"]
        return _base(r["port_code"], "ports.csv", line) | {
            "port_code": r["port_code"],
            "name": r["name"],
            "country": _text(r["country"]),
            "timezone": r["timezone"],
        }

    table("Port", port)

    # Voyages: departure is in the origin port's zone; ETAs and the berth
    # window are in the destination port's zone.
    voyage_vessel: dict[str, str] = {}

    def voyage(line: int, r: dict[str, str]) -> Props:
        vid = r["voyage_id"]
        origin_zone = zones[r["origin"]]
        destination_zone = zones[r["destination"]]
        voyage_vessel[vid] = r["vessel_id"]
        rels.append(Relationship(vid, "OPERATED_BY", r["vessel_id"]))
        rels.append(Relationship(vid, "FROM", r["origin"]))
        rels.append(Relationship(vid, "TO", r["destination"]))
        props = _base(vid, "voyages.csv", line) | {
            "voyage_id": vid,
            "vessel_id": r["vessel_id"],
            "origin": r["origin"],
            "destination": r["destination"],
            "status": _text(r["status"]),
            "recorded_delay_hours": _int(r["recorded_delay_hours"]),
        }
        for column, zone in (
            ("departure", origin_zone),
            ("original_eta", destination_zone),
            ("revised_eta", destination_zone),
            ("berth_window_start", destination_zone),
            ("berth_window_end", destination_zone),
        ):
            local = _text(r[f"{column}_local"])
            props[f"{column}_local"] = local
            props[f"{column}_utc"] = local_to_utc(local, zone)
        return props

    table("Voyage", voyage)

    # Port calls: times are already UTC.
    def port_call(line: int, r: dict[str, str]) -> Props:
        rels.append(Relationship(r["call_id"], "ON_VOYAGE", r["voyage_id"]))
        rels.append(Relationship(r["call_id"], "AT_PORT", r["port"]))
        props = _base(r["call_id"], "port_calls.csv", line) | {
            "call_id": r["call_id"],
            "voyage_id": r["voyage_id"],
            "vessel_id": r["vessel_id"],
            "port": r["port"],
            "call_type": _text(r["call_type"]),
        }
        for column in ("anchorage_arrival_utc", "berth_scheduled_utc", "berthing_utc", "departure_utc"):
            props[column] = parse_utc(r[column])
        return props

    table("PortCall", port_call)

    # Incidents
    def incident(line: int, r: dict[str, str]) -> Props:
        vessel_id = voyage_vessel[r["voyage_id"]]
        rels.append(Relationship(r["incident_id"], "ON_VOYAGE", r["voyage_id"]))
        return _base(r["incident_id"], "incidents.csv", line) | {
            "incident_id": r["incident_id"],
            "voyage_id": r["voyage_id"],
            "type": _text(r["type"]),
            "status": _text(r["status"]),
            "severity": _text(r["severity"]),
            "reported_utc": parse_utc(r["reported_utc"]),
            "source": _text(r["source"]),
            "origin": incident_origin(
                r["source"], vessel_id, vessels[vessel_id]["technical_manager"] or ""
            ),
            "attributed_delay_hours": _int(r["attributed_delay_hours"]),
        }

    table("Incident", incident)

    # Congestion: a port-wide figure, linked only to its port (FR-014).
    def congestion(line: int, r: dict[str, str]) -> Props:
        eid = f"CONG-{r['port']}-{r['date']}"
        rels.append(Relationship(eid, "AT_PORT", r["port"]))
        return _base(eid, "congestion.csv", line) | {
            "port": r["port"],
            "date": _date(r["date"]),
            "congestion_level": _text(r["congestion_level"]),
            "avg_wait_hours": _int(r["avg_wait_hours"]),
            "vessels_at_anchor": _int(r["vessels_at_anchor"]),
            "source": _text(r["source"]),
        }

    table("CongestionSnapshot", congestion)

    # Bookings, and the customers derived from them
    customers: dict[str, Props] = {}

    def booking(line: int, r: dict[str, str]) -> Props:
        bid = r["booking_id"]
        status, rolled_to = normalize_booking_status(r["status"])
        name = r["customer"]
        cid = customer_id(name)
        known = customers.setdefault(
            cid, {"evidence_id": cid, "source_ref": "bookings.csv", "name": name}
        )
        if known["name"] != name:
            raise ValueError(f"customers {known['name']!r} and {name!r} share the ID {cid}")
        rels.append(Relationship(cid, "HOLDS", bid))
        rels.append(Relationship(bid, "ON_VOYAGE", r["voyage_id"]))
        if rolled_to:
            rels.append(Relationship(bid, "ROLLED_TO", rolled_to))
        return _base(bid, "bookings.csv", line) | {
            "booking_id": bid,
            "customer": name,
            "voyage_id": r["voyage_id"],
            "containers": _int(r["containers"]),
            "status_raw": r["status"],
            "status_normalized": status,
            "promised_delivery_date": _date(r["promised_delivery_date"]),
        }

    table("Booking", booking)
    plan.nodes["Customer"] = list(customers.values())

    return plan


# --- Writing ----------------------------------------------------------------


def _clear(tx: ManagedTransaction) -> None:
    tx.run("MATCH (n) DETACH DELETE n").consume()


def _write_plan(tx: ManagedTransaction, plan: LoadPlan) -> dict[str, int]:
    for label, nodes in plan.nodes.items():
        # Labels come from TABLE_FILES or the fixed 'Customer', never from data.
        tx.run(
            f"UNWIND $rows AS row CREATE (n:Evidence:{label}) SET n = row",
            rows=nodes,
        ).consume()

    tx.run(
        "UNWIND $rows AS row "
        "CREATE (f:Evidence:SourceFile {evidence_id: 'FILE:' + row.path, source_ref: row.path, "
        "path: row.path, row_or_file_count: row.count, sha256: row.sha256, loaded: true})",
        rows=[
            {"path": s.path, "count": s.row_or_file_count, "sha256": s.sha256}
            for s in plan.source_files
        ],
    ).consume()

    created: dict[str, int] = {}
    for rel_type in RELATIONSHIP_TYPES:
        pairs = [{"start": r.start, "end": r.end} for r in plan.relationships if r.type == rel_type]
        record = tx.run(
            "UNWIND $pairs AS pair "
            "MATCH (a:Evidence {evidence_id: pair.start}) "
            "MATCH (b:Evidence {evidence_id: pair.end}) "
            f"CREATE (a)-[:{rel_type}]->(b) "
            "RETURN count(*) AS n",
            pairs=pairs,
        ).single()
        n = record["n"] if record else 0
        if n != len(pairs):
            # A missing endpoint would otherwise drop the link silently.
            raise ValueError(f"{rel_type}: created {n} of {len(pairs)} links; an endpoint is missing")
        created[rel_type] = n
    return created


def load_tables(driver: Driver, data_dir: Path = DEFAULT_DATA_DIR) -> LoadSummary:
    """Clear the graph and load every table. Returns what was written."""
    # Build first, so a bad file fails before the graph is touched.
    plan = build_plan(data_dir)

    with write_session(driver) as session:
        session.execute_write(_clear)
        # Schema changes can't share a transaction with data writes.
        session.run(
            "CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS "
            "FOR (n:Evidence) REQUIRE n.evidence_id IS UNIQUE"
        ).consume()
        relationship_counts = session.execute_write(_write_plan, plan)

    node_counts = {label: len(nodes) for label, nodes in plan.nodes.items()}
    node_counts["SourceFile"] = len(plan.source_files)
    return LoadSummary(node_counts, relationship_counts)
