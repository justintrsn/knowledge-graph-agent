import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.timeutil import elapsed_hours, local_to_utc, parse_utc, snapshot_now

ROOT = Path(__file__).parents[2]
AMSTERDAM = "Europe/Amsterdam"


def read_table(name: str) -> list[dict[str, str]]:
    with open(ROOT / "data" / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_vg_1042_delay_is_elapsed_time_across_the_clock_change():
    # Original ETA is in summer time (UTC+2), revised ETA in winter time (UTC+1).
    original = local_to_utc("2026-10-24 20:00", AMSTERDAM)
    revised = local_to_utc("2026-10-27 02:00", AMSTERDAM)

    assert original == datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
    assert revised == datetime(2026, 10, 27, 1, 0, tzinfo=UTC)
    assert elapsed_hours(original, revised) == 55

    wall_clock = datetime(2026, 10, 27, 2, 0) - datetime(2026, 10, 24, 20, 0)
    assert wall_clock.total_seconds() / 3600 == 54


def test_berth_window_end_converts_to_utc():
    assert local_to_utc("2026-10-25 05:00", AMSTERDAM) == datetime(2026, 10, 25, 4, 0, tzinfo=UTC)


def test_zone_without_daylight_saving():
    assert local_to_utc("2026-09-30 06:00", "Asia/Singapore") == datetime(
        2026, 9, 29, 22, 0, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-25 02:30",  # repeated when clocks go back
        "2026-03-29 02:30",  # skipped when clocks go forward
    ],
)
def test_time_changed_by_daylight_saving_is_rejected(text):
    with pytest.raises(ValueError, match="daylight-saving"):
        local_to_utc(text, AMSTERDAM)


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_input_gives_none(blank):
    assert local_to_utc(blank, AMSTERDAM) is None
    assert parse_utc(blank) is None


def test_parse_utc():
    assert parse_utc("2026-10-06T06:00Z") == datetime(2026, 10, 6, 6, 0, tzinfo=UTC)


def test_unexpected_format_fails_loudly():
    with pytest.raises(ValueError):
        parse_utc("06/10/2026 06:00")


def test_unknown_time_gives_unknown_duration():
    known = parse_utc("2026-10-06T06:00Z")
    assert elapsed_hours(None, known) is None
    assert elapsed_hours(known, None) is None


def test_snapshot_comes_from_config_not_the_clock():
    config = load_config(ROOT / "config.yaml")
    assert snapshot_now(config) == datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def test_every_table_time_parses():
    # Unparseable values here would surface later as missing data.
    for row in read_table("port_calls"):
        for column in ("anchorage_arrival_utc", "berth_scheduled_utc", "berthing_utc", "departure_utc"):
            parse_utc(row[column])
    for row in read_table("incidents"):
        assert parse_utc(row["reported_utc"]) is not None


def test_recorded_delay_matches_elapsed_time_for_every_voyage():
    zones = {row["port_code"]: row["timezone"] for row in read_table("ports")}
    for voyage in read_table("voyages"):
        zone = zones[voyage["destination"]]
        delay = elapsed_hours(
            local_to_utc(voyage["original_eta_local"], zone),
            local_to_utc(voyage["revised_eta_local"], zone),
        )
        assert delay == float(voyage["recorded_delay_hours"]), voyage["voyage_id"]
