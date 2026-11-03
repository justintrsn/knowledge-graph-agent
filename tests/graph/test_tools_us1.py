"""The tools for explaining a delay (User Story 1), against the real graph (T045)."""

from pathlib import Path

import pytest

from knowledge_graph_agent.agent import tools
from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.extract.resolve import ReferenceData
from knowledge_graph_agent.graph.load_facts import load_facts
from knowledge_graph_agent.graph.load_tables import load_tables

ROOT = Path(__file__).resolve().parents[2]
NO_FILTERS = {"vessel_ids": None, "voyage_ids": None, "port": None, "kind": None,
              "incident_type": None, "from_date": None, "to_date": None}


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


def claims(ctx, **filters):
    return call(ctx, "message_claims", **(NO_FILTERS | filters))


def by_id(result):
    return {r["evidence_id"]: r for r in result["rows"]}


def test_current_voyage_is_the_one_in_progress(ctx):
    rows = call(ctx, "current_voyage", vessel_id="V001")["rows"]
    phases = {r["evidence_id"]: r["computed"]["phase"] for r in rows}
    assert phases == {"VG-1042": "current", "VG-1107": "next"}  # not the completed VG-0981


def test_delay_is_elapsed_hours_across_the_clock_change(ctx):
    voyage = by_id(call(ctx, "current_voyage", vessel_id="V001"))["VG-1042"]
    # Local times differ by 54 h, but the clocks went back on 25 Oct: 55 h elapsed
    assert voyage["computed"]["delay_hours"] == voyage["recorded_delay_hours"] == 55
    assert voyage["original_eta_local"] == "2026-10-24 20:00"  # local text kept beside UTC
    assert voyage["original_eta_utc"] == "2026-10-24T18:00:00+00:00"


def test_unknown_vessel_id_is_an_error_the_model_can_fix(ctx):
    result, is_error = tools.execute(ctx, "current_voyage", {"vessel_id": "Ocean Star"})
    assert is_error and "find_vessel" in result["error"]


def test_incidents_keep_unknown_hours_as_null(ctx):
    rows = by_id(call(ctx, "incidents", voyage_ids=["VG-1042"], vessel_ids=None))
    assert rows["INC-201"]["status"] == "Confirmed" and rows["INC-201"]["attributed_delay_hours"] == 12
    assert "attributed_delay_hours" in rows["INC-202"] and rows["INC-202"]["attributed_delay_hours"] is None


def test_incidents_link_reports_and_corrections(ctx):
    result = call(ctx, "incidents", voyage_ids=["VG-1042"], vessel_ids=None)
    reports = [r["evidence_id"] for r in by_id(result)["INC-202"]["computed"]["reports"]]
    assert "MSG:crew_2026-10-13_ocean_star#3" in reports and "MSG:noon_2026-10-14_ocean_star#3" in reports
    assert set(reports) <= set(result["evidence_ids"])  # linked facts can be cited

    corrected = by_id(call(ctx, "incidents", voyage_ids=None, vessel_ids=["V006"]))["INC-094"]
    assert [c["supersedes"] for c in corrected["computed"]["corrected_by"]] == [True, True]


def test_anchor_to_berth_uses_the_scheduled_berth_until_berthed(ctx):
    rows = by_id(call(ctx, "port_calls", voyage_ids=["VG-1042"], vessel_ids=None))
    assert rows["PC-7004"]["computed"] == {"anchor_to_berth_hours": 35, "berth_basis": "scheduled"}
    assert rows["PC-7004"]["berthing_utc"] is None
    assert rows["PC-7003"]["computed"]["anchor_to_berth_hours"] is None


def test_congestion_is_port_wide_and_reports_missing_dates(ctx):
    result = call(ctx, "congestion", port="Rotterdam", from_date="2026-10-25", to_date="2026-10-28")
    assert result["scope"] == "port_wide" and result["port"] == "NLRTM"
    assert result["last_date_available"] == "2026-10-26"
    assert result["missing_dates"] == ["2026-10-27", "2026-10-28"]
    assert [r["evidence_id"] for r in result["rows"]] == ["CONG-NLRTM-2026-10-25", "CONG-NLRTM-2026-10-26"]


def test_bad_dates_are_an_error(ctx):
    result, is_error = tools.execute(ctx, "congestion", {"port": "NLRTM", "from_date": "25 Oct", "to_date": None})
    assert is_error and "from_date" in result["error"]


def test_both_weather_estimates_come_back_marked_as_conflicting(ctx):
    rows = by_id(claims(ctx, voyage_ids=["VG-1042"], incident_type="Adverse weather / rerouting"))
    master, crew = rows["MSG:noon_2026-10-14_ocean_star#3"], rows["MSG:crew_2026-10-13_ocean_star#3"]
    assert (master["hours_low"], crew["hours_low"]) == (8, 20)
    assert [c["evidence_id"] for c in master["computed"]["conflicts_with"]] == [crew["evidence_id"]]
    assert [c["evidence_id"] for c in crew["computed"]["conflicts_with"]] == [master["evidence_id"]]


def test_message_claims_are_untrusted_facts_never_raw_bodies(ctx):
    result = claims(ctx, vessel_ids=["V001"])
    assert result["rows"] and result["searched"] == ["FILE:messages/core"]
    for row in result["rows"]:
        assert row["computed"]["untrusted"] is True
        assert "body" not in row


def test_injected_instruction_is_flagged_never_a_fact(ctx):
    result = claims(ctx, vessel_ids=["V001"])
    flagged = [r for r in result["rows"] if r["computed"]["message"].get("suspicious")]
    assert flagged, "the forwarded customer email should be flagged"
    for row in flagged:
        message = row["computed"]["message"]
        assert message["evidence_id"] in result["evidence_ids"]  # cite the message, not the fact
        injected = message["suspicious_quote"]
        assert injected not in row["quote"] and injected not in row["statement"]
    # And no fact anywhere repeats it
    everything = claims(ctx, from_date="2026-01-01")["rows"]
    assert not any(injected in r["quote"] or injected in r["statement"] for r in everything)


def test_every_row_has_evidence_id_and_source_ref(ctx):
    results = [
        call(ctx, "current_voyage", vessel_id="V001"),
        call(ctx, "incidents", voyage_ids=["VG-1042"], vessel_ids=None),
        call(ctx, "port_calls", voyage_ids=None, vessel_ids=["V001"]),
        call(ctx, "congestion", port="NLRTM", from_date=None, to_date=None),
        claims(ctx, voyage_ids=["VG-1042"]),
    ]
    for result in results:
        for row in result["rows"]:
            assert row["evidence_id"] and row["source_ref"]


def test_filters_are_required(ctx):
    for name, params in [("incidents", {"voyage_ids": None, "vessel_ids": None}),
                         ("port_calls", {"voyage_ids": [], "vessel_ids": None}),
                         ("message_claims", NO_FILTERS)]:
        assert tools.execute(ctx, name, params)[1] is True


def test_vessel_filter_also_finds_facts_about_its_class(ctx):
    rows = claims(ctx, vessel_ids=["V001"])["rows"]
    by_class = [r for r in rows if r["computed"]["matched_by"] == "vessel_class"]
    assert by_class and all(r["vessel_class_ref"] == "Meridian 8500" for r in by_class)
    assert all(r["computed"]["matched_by"] == "vessel" for r in rows if r not in by_class)
