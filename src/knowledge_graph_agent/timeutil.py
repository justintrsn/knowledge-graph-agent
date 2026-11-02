"""Time conversion. Every value becomes UTC, and nothing reads the system clock.

The data's "now" is the snapshot in config.yaml, never the real date.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from knowledge_graph_agent.config import Config

LOCAL_FORMAT = "%Y-%m-%d %H:%M"  # voyages.csv *_local columns
UTC_FORMAT = "%Y-%m-%dT%H:%MZ"  # *_utc columns, e.g. 2026-10-06T06:00Z


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


def local_to_utc(text: str | None, iana_zone: str) -> datetime | None:
    """Convert port-local text such as '2026-10-24 20:00' to UTC.

    Raises ValueError for a time that is skipped or repeated by a
    daylight-saving change, rather than guessing which one was meant.
    """
    if _blank(text):
        return None
    naive = datetime.strptime(text.strip(), LOCAL_FORMAT)
    zone = ZoneInfo(iana_zone)
    earlier = naive.replace(tzinfo=zone, fold=0)
    later = naive.replace(tzinfo=zone, fold=1)
    # Both readings agree except in the hour a clock change skips or repeats.
    if earlier.utcoffset() != later.utcoffset():
        raise ValueError(
            f"{text!r} is skipped or repeated by a daylight-saving change in {iana_zone}"
        )
    return earlier.astimezone(UTC)


def parse_utc(text: str | None) -> datetime | None:
    """Parse a UTC value such as '2026-10-06T06:00Z'."""
    if _blank(text):
        return None
    return datetime.strptime(text.strip(), UTC_FORMAT).replace(tzinfo=UTC)


def elapsed_hours(start: datetime | None, end: datetime | None) -> float | None:
    """Real elapsed hours, correct across clock changes. Unknown stays unknown."""
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600


def snapshot_now(config: Config) -> datetime:
    """The moment the data was taken, which is 'now' for every question."""
    return config.snapshot_now.astimezone(UTC)
