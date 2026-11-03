"""The tools for predicting berth-window risk (User Story 3), against the real graph (T051)."""

from pathlib import Path

import pytest

from knowledge_graph_agent.agent import tools
from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.load_facts import load_facts
from knowledge_graph_agent.graph.load_tables import load_tables

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ctx(driver):
    config = load_config(ROOT / "config.yaml")
    load_tables(driver, ROOT / "data")
    load_facts(driver, config, ROOT / "data", ROOT / "cache" / "extractions")
    return tools.ToolContext(driver, ReferenceData.from_data(ROOT / "data"), config)


def call(ctx, name, **params):
    result, is_error = tools.execute(ctx, name, params)
    assert not is_error, result
    return result


def next_week_at_rotterdam(ctx):
    return call(ctx, "arrivals_at_port", port="NLRTM", start_date="2026-11-02", end_date="2026-11-08")


def test_six_arrivals_next_week(ctx):
    rows = next_week_at_rotterdam(ctx)["rows"]
    names = {r["computed"]["vessel_name"] for r in rows}
    assert names == {"MV Northern Lark", "MV Arctic Fern", "MV Kestrel Bay",
                     "MV Amber Coast", "MV Celtic Dawn", "MV Rio Verde"}
    assert "VG-1042" not in {r["evidence_id"] for r in rows}  # due 27 Oct, before the window


def test_only_arctic_fern_is_already_past_its_window(ctx):
    late = [r for r in next_week_at_rotterdam(ctx)["rows"] if r["computed"]["revised_eta_after_window_end"]]
    assert [r["computed"]["vessel_name"] for r in late] == ["MV Arctic Fern"]
    fern = late[0]
    assert fern["revised_eta_local"] == "2026-11-04 15:00" and fern["berth_window_end_local"] == "2026-11-03 16:00"
    assert fern["computed"]["hours_past_window_end"] == 23


def test_dates_are_port_local_and_inclusive(ctx):
    # Northern Lark is due 2 Nov 07:00 local, the first day of the range
    one_day = call(ctx, "arrivals_at_port", port="Rotterdam", start_date="2026-11-02", end_date="2026-11-02")
    assert [r["evidence_id"] for r in one_day["rows"]] == ["VG-1097"]


def test_bad_ranges_are_errors(ctx):
    for start, end in [("2026-11-08", "2026-11-02"), ("2026-01-01", "2026-12-31"), ("next week", "2026-11-08")]:
        _, is_error = tools.execute(ctx, "arrivals_at_port", {"port": "NLRTM", "start_date": start, "end_date": end})
        assert is_error


def test_santos_feed_gap_is_reported_as_missing_dates(ctx):
    result = call(ctx, "congestion", port="BRSSZ", from_date="2026-10-01", to_date="2026-10-26")
    assert result["last_date_available"] == "2026-10-04"
    assert result["missing_dates"][0] == "2026-10-05" and result["missing_dates"][-1] == "2026-10-26"
    assert len(result["missing_dates"]) == 22
