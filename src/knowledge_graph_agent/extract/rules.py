"""Rules applied to extracted facts. All of it is code; none of it is the LLM.

Part 1 checks each fact on its own: quotes, injected instructions, times, and
which vessel, voyage and port it is about.
Part 2 relates facts to incidents and to each other: reports, corrections,
conflicts and verification (data-model.md, "Rules").
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from knowledge_graph_agent.extract.messages import Message, normalize_text
from knowledge_graph_agent.extract.resolve import (
    ReferenceData,
    Resolution,
    active_voyage,
    resolve_class,
    resolve_port,
    resolve_vessel,
    resolve_voyage,
)
from knowledge_graph_agent.extract.schema import ExtractedFact
from knowledge_graph_agent.graph.load_tables import UNKNOWN_ORIGIN

# --- Part 1: quotes and injected instructions -------------------------------


def check_quote(quote: str, body: str) -> bool:
    """A quote must appear in the body exactly, after line-ending normalization."""
    quote = normalize_text(quote)
    return bool(quote.strip()) and quote in body


INJECTION_PATTERNS = [
    re.compile(r"\bignore\b[^\n]{0,60}?\binstructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard\b[^\n]{0,60}?\b(?:instructions|rules|prompt)\b", re.IGNORECASE),
    re.compile(r"\b(?:AI|artificial intelligence)\s+(?:assistant|model|agent)s?\b", re.IGNORECASE),
    re.compile(r"\bsystem prompt\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    source: str  # "regex" or "llm"

    def text(self, body: str) -> str:
        return body[self.start : self.end]


def _line_around(body: str, start: int, end: int) -> tuple[int, int]:
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    return line_start, len(body) if line_end == -1 else line_end


def scan_injection(body: str, llm_flags: list[str]) -> list[Span]:
    """Find text that tries to instruct an AI.

    The regex gives a guarantee that doesn't depend on a model reading hostile
    text correctly; the model's own flags catch phrasing the regex misses. A
    regex hit flags its whole line.
    """
    spans = []
    for pattern in INJECTION_PATTERNS:
        for match in pattern.finditer(body):
            spans.append(Span(*_line_around(body, match.start(), match.end()), "regex"))
    for flagged in llm_flags:
        flagged = normalize_text(flagged).strip()
        start = body.find(flagged) if flagged else -1
        if start >= 0:
            spans.append(Span(start, start + len(flagged), "llm"))
    return sorted(set(spans), key=lambda s: s.start)


def _quote_ranges(quote: str, body: str) -> list[tuple[int, int]]:
    quote = normalize_text(quote)
    return [(m.start(), m.start() + len(quote)) for m in re.finditer(re.escape(quote), body)]


def overlaps_flagged(quote: str, body: str, spans: list[Span]) -> bool:
    return any(
        start < span.end and span.start < end
        for start, end in _quote_ranges(quote, body)
        for span in spans
    )


def drop_overlapping(
    facts: list[ExtractedFact], body: str, spans: list[Span]
) -> tuple[list[int], list[int]]:
    """Split fact positions into (kept, dropped). A fact quoting flagged text is dropped."""
    kept, dropped = [], []
    for index, fact in enumerate(facts):
        (dropped if overlaps_flagged(fact.quote, body, spans) else kept).append(index)
    return kept, dropped


# --- Part 1: times ----------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+{_MONTH}(?=\W|$)", re.IGNORECASE)
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CLOCK = re.compile(r"\b([01]?\d|2[0-3]):?([0-5]\d)\b")
_UTC = re.compile(r"\bUTC\b|\bGMT\b|\d\s*Z\b")
_LOCAL = re.compile(r"\bLT\b|local time|ship'?s time", re.IGNORECASE)


@dataclass(frozen=True)
class TimeValue:
    raw: str | None
    utc: datetime | None
    precision: str | None  # exact, date, vague, no_zone; None when no time was given
    note: str = ""


def _find_date(text: str, year: int) -> date | None:
    if m := _ISO_DATE.search(text):
        return date(int(m[1]), int(m[2]), int(m[3]))
    if m := _DAY_MONTH.search(text):
        return date(year, _MONTHS[m[2][:3].casefold()], int(m[1]))
    if m := _MONTH_DAY.search(text):
        return date(year, _MONTHS[m[1][:3].casefold()], int(m[2]))
    return None


def normalize_time(raw: str | None, sent_utc: datetime) -> TimeValue:
    """Turn a time as written into UTC, only when the text says it is UTC.

    Ship's local time ('LT') has no known zone and stays raw. A date with no
    time stays a date. Anything else ('end of November') is vague.
    """
    if raw is None or not raw.strip():
        return TimeValue(None, None, None)
    text = raw.strip()
    if _LOCAL.search(text):
        return TimeValue(text, None, "no_zone", "ship's local time; zone unknown")

    day = _find_date(text, sent_utc.year)
    without_date = _ISO_DATE.sub(" ", text)
    clock = _CLOCK.search(without_date)

    if _UTC.search(text) and clock:
        note = ""
        if day is None:
            day = sent_utc.date()
            note = "date taken from the message header"
        when = datetime.combine(day, time(int(clock[1]), int(clock[2])), tzinfo=UTC)
        return TimeValue(text, when, "exact", note)
    if clock:
        return TimeValue(text, None, "no_zone", "time given without a zone")
    if day is not None:
        return TimeValue(text, None, "date")
    return TimeValue(text, None, "vague")


# --- Part 1: what a fact is about -------------------------------------------


@dataclass
class FactRecord:
    """One extracted fact after resolution, ready for the graph."""

    evidence_id: str
    message: Message
    fact: ExtractedFact
    time: TimeValue
    vessels: list[Resolution] = field(default_factory=list)
    voyages: list[Resolution] = field(default_factory=list)
    ports: list[Resolution] = field(default_factory=list)
    vessel_class: str | None = None
    unresolved: list[str] = field(default_factory=list)

    @property
    def origin(self) -> str:
        return self.message.origin


def _reference_time(record: FactRecord) -> datetime:
    """When the fact happened: its own exact time or date, else when it was sent."""
    if record.time.utc:
        return record.time.utc
    if record.time.precision == "date":
        day = _find_date(record.time.raw or "", record.message.sent_utc.year)
        if day:
            return datetime.combine(day, time(12, 0), tzinfo=UTC)
    return record.message.sent_utc


def _keep(found: list[Resolution], record: FactRecord, kind: str, r: Resolution) -> None:
    if r.resolved:
        found.append(r)
    else:
        record.unresolved.append(f"{kind} '{r.surface}': {r.note}")


def link_entities(record: FactRecord, ref: ReferenceData) -> None:
    """Resolve the fact's vessel, voyage, port and class references, in order:

    1. each reference as written
    2. a named voyage must belong to the named vessel; if not, keep neither
    3. no vessel named: use the named voyage's vessel, else the sender's own
       vessel (master and crew messages)
    4. one vessel and no voyage named: the voyage it was on at the time
    """
    message, fact = record.message, record.fact
    context = f"{message.header_text}\n{message.body}"

    for surface in fact.vessel_refs:
        _keep(record.vessels, record, "vessel", resolve_vessel(surface, ref, context))
    for surface in fact.voyage_refs:
        _keep(record.voyages, record, "voyage", resolve_voyage(surface, ref))
    for surface in fact.port_refs:
        _keep(record.ports, record, "port", resolve_port(surface, ref))
    record.vessel_class = resolve_class(fact.vessel_class_ref, ref)
    if fact.vessel_class_ref and not record.vessel_class:
        record.unresolved.append(f"class '{fact.vessel_class_ref}': not in vessels.csv")

    named = {r.entity_id for r in record.vessels}
    owners = {ref.voyages[v.entity_id].vessel_id for v in record.voyages}
    if named and owners and not owners <= named:
        record.unresolved.append(
            f"voyages {[v.entity_id for v in record.voyages]} belong to {sorted(owners)}, "
            f"but the fact names {sorted(named)}"
        )
        record.vessels, record.voyages = [], []
        return

    if not record.vessels:
        for voyage in record.voyages:
            owner = ref.voyages[voyage.entity_id].vessel_id
            if owner not in {v.entity_id for v in record.vessels}:
                record.vessels.append(Resolution(voyage.surface, owner, "voyage_number", 1.0))
    if not record.vessels and message.sender_vessel_id:
        record.vessels.append(Resolution(message.from_raw, message.sender_vessel_id, "sender_header", 0.9))

    vessel_ids = {r.entity_id for r in record.vessels}
    if not record.voyages and len(vessel_ids) == 1:
        (vessel_id,) = vessel_ids
        when = _reference_time(record)
        voyage = active_voyage(vessel_id, when, ref)
        if voyage:
            record.voyages.append(Resolution(
                f"{vessel_id} at {when:%Y-%m-%dT%H:%MZ}", voyage.voyage_id, "vessel_and_time", 0.8
            ))


# --- Part 2: relating facts to incidents and each other ---------------------


@dataclass(frozen=True)
class Report:
    fact_id: str
    incident_id: str
    method: str
    confidence: float


@dataclass(frozen=True)
class Correction:
    fact_id: str
    incident_id: str
    field: str
    supersedes: bool
    rule: str


@dataclass(frozen=True)
class Conflict:
    fact_a: str
    fact_b: str
    field: str
    values: tuple[str, str]


def link_reports(records: list[FactRecord], ref: ReferenceData) -> list[Report]:
    """A fact reports an incident on the same voyage with the same category.

    Times need not match: both the table row and the message stay visible.
    """
    reports = []
    for record in records:
        category = record.fact.incident_category
        if not category or record.fact.kind == "correction":
            continue
        for voyage in record.voyages:
            for incident in ref.incidents_on(voyage.entity_id):
                if incident.type == category:
                    reports.append(Report(record.evidence_id, incident.incident_id, "voyage_and_category", voyage.confidence))
    return reports


def link_corrections(records: list[FactRecord], ref: ReferenceData) -> list[Correction]:
    """A correction on the same voyage links to the incident it corrects.

    It supersedes the incident only when it comes from the incident's own
    source (same origin). The incident itself is never changed.
    """
    corrections = []
    for record in records:
        if record.fact.kind != "correction":
            continue
        for voyage in record.voyages:
            incidents = ref.incidents_on(voyage.entity_id)
            category = record.fact.incident_category
            if category:
                incidents = [i for i in incidents if i.type == category]
            elif len(incidents) != 1:
                continue  # no category, and no single incident to point at
            for incident in incidents:
                same = record.origin != UNKNOWN_ORIGIN and record.origin == incident.origin
                rule = (
                    f"same origin ({record.origin})"
                    if same
                    else f"different origin: correction from {record.origin}, incident from {incident.origin}"
                )
                corrections.append(Correction(
                    record.evidence_id, incident.incident_id,
                    record.fact.correction_field or "unspecified", same, rule,
                ))
    return corrections


def _hours(record: FactRecord) -> tuple[float, float] | None:
    low, high = record.fact.hours_low, record.fact.hours_high
    if low is None and high is None:
        return None
    return (low if low is not None else high, high if high is not None else low)


def link_conflicts(records: list[FactRecord], reports: list[Report]) -> list[Conflict]:
    """Two facts reporting the same incident with different hours conflict."""
    by_id = {r.evidence_id: r for r in records}
    by_incident: dict[str, list[str]] = {}
    for report in reports:
        by_incident.setdefault(report.incident_id, []).append(report.fact_id)

    conflicts = set()
    for fact_ids in by_incident.values():
        for i, a in enumerate(fact_ids):
            for b in fact_ids[i + 1 :]:
                ha, hb = _hours(by_id[a]), _hours(by_id[b])
                if ha and hb and ha != hb:
                    first, second = sorted((a, b))
                    conflicts.add(Conflict(
                        first, second, "hours",
                        (by_id[first].fact.hours_raw or "", by_id[second].fact.hours_raw or ""),
                    ))
    return sorted(conflicts, key=lambda c: (c.fact_a, c.fact_b))


def verification(
    record: FactRecord,
    reports: list[Report],
    conflicts: list[Conflict],
    records: list[FactRecord],
    ref: ReferenceData,
) -> tuple[str, str]:
    """FR-013: a message fact is unverified unless a source with a different,
    known origin reports the same incident and no figures conflict.

    Returns (status, reason).
    """
    fid = record.evidence_id
    in_conflict = [c for c in conflicts if fid in (c.fact_a, c.fact_b)]
    if in_conflict:
        others = sorted({c.fact_b if c.fact_a == fid else c.fact_a for c in in_conflict})
        return "unverified", f"hours conflict with {', '.join(others)}"

    incident_ids = sorted({r.incident_id for r in reports if r.fact_id == fid})
    if not incident_ids:
        return "unverified", "only this message reports it"

    by_id = {r.evidence_id: r for r in records}
    reasons = []
    for incident_id in incident_ids:
        incident = ref.incidents[incident_id]
        if incident.status == "Dismissed":
            return "unverified", f"{incident_id} is dismissed in the table"

        # Another message from a different, known origin
        for report in reports:
            other = by_id[report.fact_id]
            if (
                report.incident_id == incident_id
                and report.fact_id != fid
                and other.origin not in (UNKNOWN_ORIGIN, record.origin)
            ):
                return "confirmed", f"{report.fact_id} ({other.origin}) also reports {incident_id}"

        # The table row, when confirmed by a different, known origin
        if incident.origin == UNKNOWN_ORIGIN:
            reasons.append(f"{incident_id} has an unknown origin")
        elif incident.origin == record.origin:
            reasons.append(f"{incident_id} shares this fact's origin ({record.origin})")
        elif incident.status != "Confirmed":
            reasons.append(f"{incident_id} is only {incident.status.lower()}")
        else:
            return "confirmed", f"{incident_id} is confirmed by {incident.origin}"
    return "unverified", "no independent source: " + "; ".join(reasons)


def voyage_match(record: FactRecord) -> str:
    if record.voyages:
        return "matched"
    if record.vessels:
        return "unmatched"
    return "not_applicable"
