"""Read message files: four headers, a blank line, then the body.

Also decides who sent each message (`sender_role`) and which source it counts
as (`origin`), per research.md:

- master and crew of one vessel share the origin `vessel:<id>`
- a named agent, manager or customer uses `org:<organization>`
- a bare `Port agent`, or a sender that can't be identified, is `unknown`
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from knowledge_graph_agent.extract.resolve import ReferenceData, resolve_vessel
from knowledge_graph_agent.graph.load_tables import DEFAULT_DATA_DIR, UNKNOWN_ORIGIN

COLLECTIONS = ("core", "extra")
HEADERS = ("From", "To", "Date", "Subject")
DATE_FORMAT = "%Y-%m-%d %H:%M UTC"

_CREW_TITLES = re.compile(r"\b(officer|engineer|bosun|cadet|rating|crew|mate|cook)\b", re.IGNORECASE)
_AGENT_WORDS = re.compile(r"ag[eê]nc|agent|port services|shipping|maritime", re.IGNORECASE)


@dataclass(frozen=True)
class Message:
    stem: str
    collection: str
    source_ref: str  # e.g. messages/core/noon_2026-10-06_ocean_star.txt
    from_raw: str
    to_raw: str
    subject: str
    sent_utc: datetime
    body: str
    sender_role: str
    origin: str
    sender_vessel_id: str | None  # set for master and crew messages

    @property
    def message_id(self) -> str:
        return f"MSG:{self.stem}"

    @property
    def header_text(self) -> str:
        return f"From: {self.from_raw}\nTo: {self.to_raw}\nSubject: {self.subject}"


def normalize_text(text: str) -> str:
    """LF line endings everywhere, so quotes compare the same on any OS."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def directory_sha256(directory: Path) -> str:
    """One fingerprint for a folder of messages: each file's name and content."""
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.txt")):
        digest.update(path.name.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def sender_identity(from_raw: str, ref: ReferenceData) -> tuple[str, str, str | None]:
    """Return (sender_role, origin, vessel_id) for a From line."""
    sender = re.sub(r"<[^>]*>", "", from_raw).strip()  # drop an email address
    head, _, rest = sender.partition(",")
    head, rest = head.strip(), rest.strip()

    is_master = head.casefold() == "master"
    if is_master or _CREW_TITLES.search(head):
        vessel = resolve_vessel(re.sub(r"\(.*?\)", "", rest), ref)
        origin = f"vessel:{vessel.entity_id}" if vessel.resolved else UNKNOWN_ORIGIN
        return ("master" if is_master else "crew"), origin, vessel.entity_id

    for manager in ref.technical_managers:
        if manager.casefold() in sender.casefold():
            return "technical_manager", f"org:{manager}", None
    for customer in ref.customers:
        if sender.casefold().startswith(customer.casefold()):
            return "customer", f"org:{customer}", None
    if sender.casefold() == "port agent":
        return "agent", UNKNOWN_ORIGIN, None  # unnamed, so it can't corroborate
    if _AGENT_WORDS.search(head):
        return "agent", f"org:{head}", None
    return "unknown", UNKNOWN_ORIGIN, None


def parse_message(path: Path, collection: str, ref: ReferenceData) -> Message:
    text = normalize_text(path.read_text(encoding="utf-8"))
    header_block, separator, body = text.partition("\n\n")
    if not separator:
        raise ValueError(f"{path.name}: no blank line after the headers")

    headers: dict[str, str] = {}
    for line in header_block.split("\n"):
        name, _, value = line.partition(":")
        headers[name.strip()] = value.strip()
    missing = [h for h in HEADERS if h not in headers]
    if missing:
        raise ValueError(f"{path.name}: missing headers {missing}")

    role, origin, vessel_id = sender_identity(headers["From"], ref)
    return Message(
        stem=path.stem,
        collection=collection,
        source_ref=f"messages/{collection}/{path.name}",
        from_raw=headers["From"],
        to_raw=headers["To"],
        subject=headers["Subject"],
        sent_utc=datetime.strptime(headers["Date"], DATE_FORMAT).replace(tzinfo=UTC),
        body=body.rstrip("\n"),
        sender_role=role,
        origin=origin,
        sender_vessel_id=vessel_id,
    )


def load_messages(
    ref: ReferenceData, collections: tuple[str, ...] = ("core",), data_dir: Path = DEFAULT_DATA_DIR
) -> list[Message]:
    messages = []
    for collection in collections:
        for path in sorted((data_dir / "messages" / collection).glob("*.txt")):
            messages.append(parse_message(path, collection, ref))
    return messages
