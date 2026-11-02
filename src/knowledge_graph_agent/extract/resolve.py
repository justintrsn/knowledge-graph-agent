"""Match names written in messages to vessels, voyages, ports and classes.

Works from the CSV tables in memory, so it needs neither Neo4j nor an API key.
The order of methods and their confidence come from research.md. Anything
that can't be matched with confidence stays unresolved; nothing is guessed.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from knowledge_graph_agent.graph.load_tables import DEFAULT_DATA_DIR, build_plan

# A message sent shortly after a voyage's revised ETA can still be about it.
VOYAGE_GRACE = timedelta(days=1)


@dataclass(frozen=True)
class Vessel:
    vessel_id: str
    name: str
    imo: str
    vessel_class: str
    technical_manager: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class Voyage:
    voyage_id: str
    vessel_id: str
    departure_utc: datetime | None
    revised_eta_utc: datetime | None


@dataclass(frozen=True)
class Port:
    port_code: str
    name: str
    keys: tuple[tuple[str, ...], ...]  # token phrases that name this port


@dataclass(frozen=True)
class Incident:
    incident_id: str
    voyage_id: str
    type: str
    status: str
    origin: str
    attributed_delay_hours: int | None


@dataclass
class ReferenceData:
    vessels: dict[str, Vessel] = field(default_factory=dict)
    voyages: dict[str, Voyage] = field(default_factory=dict)
    ports: dict[str, Port] = field(default_factory=dict)
    incidents: dict[str, Incident] = field(default_factory=dict)
    vessel_classes: set[str] = field(default_factory=set)
    technical_managers: set[str] = field(default_factory=set)
    customers: set[str] = field(default_factory=set)

    @classmethod
    def from_data(cls, data_dir: Path = DEFAULT_DATA_DIR) -> "ReferenceData":
        plan = build_plan(data_dir)
        ref = cls()
        for v in plan.nodes["Vessel"]:
            ref.vessels[v["vessel_id"]] = Vessel(
                v["vessel_id"], v["name"], v["imo"], v["vessel_class"], v["technical_manager"],
                name_tokens(v["name"]),
            )
            ref.vessel_classes.add(v["vessel_class"])
            ref.technical_managers.add(v["technical_manager"])
        for v in plan.nodes["Voyage"]:
            ref.voyages[v["voyage_id"]] = Voyage(
                v["voyage_id"], v["vessel_id"], v["departure_utc"], v["revised_eta_utc"]
            )
        for p in plan.nodes["Port"]:
            ref.ports[p["port_code"]] = Port(p["port_code"], p["name"], _port_keys(p["name"]))
        for i in plan.nodes["Incident"]:
            ref.incidents[i["incident_id"]] = Incident(
                i["incident_id"], i["voyage_id"], i["type"], i["status"], i["origin"],
                i.get("attributed_delay_hours"),
            )
        ref.customers = {c["name"] for c in plan.nodes["Customer"]}
        return ref

    def voyages_of(self, vessel_id: str) -> list[Voyage]:
        return [v for v in self.voyages.values() if v.vessel_id == vessel_id]

    def incidents_on(self, voyage_id: str) -> list[Incident]:
        return [i for i in self.incidents.values() if i.voyage_id == voyage_id]


@dataclass(frozen=True)
class Resolution:
    """The outcome of matching one reference. entity_id is None when unresolved."""

    surface: str  # the text as written
    entity_id: str | None
    method: str
    confidence: float
    note: str = ""

    @property
    def resolved(self) -> bool:
        return self.entity_id is not None


def _unresolved(surface: str, note: str) -> Resolution:
    return Resolution(surface, None, "none", 0.0, note)


# --- Text helpers -----------------------------------------------------------

_VESSEL_PREFIX = re.compile(r"\b(?:m/v|mv)\b\.?", re.IGNORECASE)


def name_tokens(text: str) -> tuple[str, ...]:
    """'M/V OCEAN STAR' and 'MV. Ocean Star' both give ('ocean', 'star')."""
    text = _VESSEL_PREFIX.sub(" ", text)
    return tuple(re.findall(r"[a-z0-9]+", text.casefold()))


def contains_phrase(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """Whole-token match, so ('ocean', 'starlight') does not contain ('ocean', 'star')."""
    n = len(phrase)
    return n > 0 and any(tokens[i : i + n] == phrase for i in range(len(tokens) - n + 1))


def _port_keys(name: str) -> tuple[tuple[str, ...], ...]:
    """'Port of Antwerp-Bruges' can be written 'Antwerp-Bruges' or 'Antwerp'."""
    stripped = re.sub(r"^port of\s+", "", name, flags=re.IGNORECASE)
    keys = {name_tokens(stripped)}
    for separator in ("-", " and "):
        if separator in stripped:
            keys.add(name_tokens(stripped.split(separator)[0]))
    return tuple(keys)


# --- Single references ------------------------------------------------------

_IMO = re.compile(r"\b(\d{7})\b")
_VOYAGE_NUMBER = re.compile(r"\b(?:VG|VOY(?:AGE)?)[\s.:#-]*(\d{3,4})\b", re.IGNORECASE)
_ABBREVIATION = re.compile(r"\b[A-Z]{2,4}\b")


def resolve_voyage(surface: str, ref: ReferenceData) -> Resolution:
    """'VOY 1042', 'voyage 0981', 'VG-1042' and a bare '0981' all name a voyage ID.

    A bare number counts only here, where the text is already known to be a
    voyage reference; inside a vessel reference it could be anything.
    """
    match = _VOYAGE_NUMBER.search(surface) or re.fullmatch(r"\s*(\d{3,4})\s*", surface)
    if not match:
        return _unresolved(surface, "no voyage number")
    voyage_id = f"VG-{int(match.group(1)):04d}"
    if voyage_id not in ref.voyages:
        return _unresolved(surface, f"{voyage_id} is not in voyages.csv")
    return Resolution(surface, voyage_id, "voyage_number", 1.0)


def _vessels_named_in(tokens: tuple[str, ...], ref: ReferenceData) -> list[Vessel]:
    return [v for v in ref.vessels.values() if contains_phrase(tokens, v.tokens)]


def resolve_vessel(surface: str, ref: ReferenceData, context: str = "") -> Resolution:
    """Match one vessel reference, most certain method first.

    `context` is the message's header and body, used only to support an
    abbreviation such as 'OS'.
    """
    imo = _IMO.search(surface)
    if imo:
        matches = [v for v in ref.vessels.values() if v.imo == imo.group(1)]
        if not matches:
            return _unresolved(surface, f"IMO {imo.group(1)} is not in vessels.csv")
        return Resolution(surface, matches[0].vessel_id, "imo", 1.0)

    named = _vessels_named_in(name_tokens(surface), ref)
    if _VOYAGE_NUMBER.search(surface):
        voyage = resolve_voyage(surface, ref)
        if not voyage.resolved:
            return _unresolved(surface, voyage.note)
        vessel_id = ref.voyages[voyage.entity_id].vessel_id
        if named and {v.vessel_id for v in named} != {vessel_id}:
            return _unresolved(surface, f"voyage {voyage.entity_id} and vessel name disagree")
        return Resolution(surface, vessel_id, "voyage_number", 1.0)

    if len(named) == 1:
        return Resolution(surface, named[0].vessel_id, "name", 0.95)
    if len(named) > 1:
        return _unresolved(surface, "names more than one vessel")

    context_tokens = name_tokens(context)
    for abbreviation in _ABBREVIATION.findall(surface):
        candidates = [
            v
            for v in ref.vessels.values()
            if "".join(t[0] for t in v.tokens) == abbreviation.casefold()
            and contains_phrase(context_tokens, v.tokens)
        ]
        if len(candidates) == 1:
            return Resolution(
                surface, candidates[0].vessel_id, "abbreviation", 0.7,
                f"{abbreviation} expands to {candidates[0].name}, named in the same message",
            )
    return _unresolved(surface, "no vessel matches")


def resolve_port(surface: str, ref: ReferenceData) -> Resolution:
    code = surface.strip().upper()
    if code in ref.ports:
        return Resolution(surface, code, "port_code", 1.0)
    tokens = name_tokens(surface)
    matches = [p for p in ref.ports.values() if any(contains_phrase(tokens, k) for k in p.keys)]
    if len(matches) == 1:
        return Resolution(surface, matches[0].port_code, "port_name", 0.95)
    if matches:
        return _unresolved(surface, "names more than one port")
    return _unresolved(surface, "no port matches")


def resolve_class(surface: str | None, ref: ReferenceData) -> str | None:
    """A vessel class counts only when it matches vessels.vessel_class exactly."""
    if not surface:
        return None
    written = re.sub(r"\s+class$", "", surface.strip(), flags=re.IGNORECASE).casefold()
    for vessel_class in ref.vessel_classes:
        if vessel_class.casefold() == written:
            return vessel_class
    return None


def active_voyage(vessel_id: str, when: datetime, ref: ReferenceData) -> Voyage | None:
    """The voyage a vessel was on at a moment, if any.

    A voyage counts from departure until a day after its revised ETA. When
    several qualify, the latest departure wins.
    """
    candidates = [
        v
        for v in ref.voyages_of(vessel_id)
        if v.departure_utc
        and v.revised_eta_utc
        and v.departure_utc <= when <= v.revised_eta_utc + VOYAGE_GRACE
    ]
    return max(candidates, key=lambda v: v.departure_utc, default=None)
