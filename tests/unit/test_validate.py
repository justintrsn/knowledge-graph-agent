"""The answer validator, with a fake graph so no Neo4j is needed (T032)."""

from datetime import UTC, datetime

import pytest

from knowledge_graph_agent.agent.answer import Answer, CitedValue, Claim, Inference
from knowledge_graph_agent.agent.validate import passed, validate

GRAPH = {
    "INC-201": {"labels": ["Evidence", "Incident"], "status": "Confirmed", "attributed_delay_hours": 12},
    "INC-202": {"labels": ["Evidence", "Incident"], "status": "Reported", "attributed_delay_hours": None},
    "VG-1042": {
        "labels": ["Evidence", "Voyage"],
        "recorded_delay_hours": 55,
        "revised_eta_utc": datetime(2026, 10, 27, 1, 0, tzinfo=UTC),
    },
    "MSG:noon#2": {
        "labels": ["Evidence", "Fact"], "verification": "unverified", "quote": "Time lost: 12 hours.",
        "message_body": "Spare seal fitted.\nTime lost: 12 hours.", "message_suspicious": False,
    },
    "MSG:fwd#1": {
        "labels": ["Evidence", "Fact"], "verification": "unverified", "quote": "Our containers are on Ocean Star",
        "message_body": "Our containers are on Ocean Star", "message_suspicious": True,
    },
    "CONG-NLRTM-2026-10-25": {"labels": ["Evidence", "CongestionSnapshot"], "avg_wait_hours": 30},
    "FILE:vessels.csv": {"labels": ["Evidence", "SourceFile"], "path": "vessels.csv"},
}
VESSELS = ["MV Ocean Star", "MV Ocean Starlight"]


class FakeLookup:
    def get(self, evidence_id):
        return GRAPH.get(evidence_id)


def claim(text, *ids, values=()):
    return Claim(text=text, evidence_ids=list(ids), cited_values=[CitedValue(evidence_id=i, field=f, value=v) for i, f, v in values])


def answer(**sections) -> Answer:
    base = dict(
        question_status="answerable",
        headline=claim("VG-1042 is delayed.", "VG-1042"),
        interpretation=[], confirmed_facts=[], unverified_reports=[], inferences=[], exclusions=[],
        data_gaps=[], suspicious_content=[], recommended_checks=[], incomplete=False, not_checked=[],
    )
    return Answer(**(base | sections))


def check(a: Answer, retrieved=None) -> dict[str, str]:
    retrieved = set(GRAPH) if retrieved is None else retrieved
    return {r.check.split("_")[0]: r.status for r in validate(a, retrieved, FakeLookup(), VESSELS)}


def test_a_clean_answer_passes():
    a = answer(confirmed_facts=[claim("Engine fault cost 12 h.", "INC-201", values=[("INC-201", "attributed_delay_hours", 12)])])
    results = validate(a, set(GRAPH), FakeLookup(), VESSELS)
    assert passed(results)
    assert all(r.status == "pass" for r in results)


def test_v1_claim_without_evidence_fails():
    assert check(answer(recommended_checks=[claim("Call the agent.")]))["V1"] == "fail"


def test_v2_unknown_id_fails():
    assert check(answer(confirmed_facts=[claim("x", "INC-999")]))["V2"] == "fail"


def test_v3_id_that_exists_but_was_never_retrieved_fails():
    a = answer(confirmed_facts=[claim("Engine fault.", "INC-201")])
    assert check(a, retrieved={"VG-1042"})["V3"] == "fail"
    assert check(a, retrieved={"VG-1042", "INC-201"})["V3"] == "pass"


@pytest.mark.parametrize(
    "field, value, ok",
    [
        ("recorded_delay_hours", 55, True),
        ("recorded_delay_hours", 55.0, True),
        ("recorded_delay_hours", "55", True),
        ("recorded_delay_hours", 54, False),
        ("revised_eta_utc", "2026-10-27T01:00:00Z", True),
        ("revised_eta_utc", "2026-10-27T03:00:00+02:00", True),  # same moment
        ("revised_eta_utc", "2026-10-27T02:00:00Z", False),
        ("no_such_field", 1, False),
    ],
)
def test_v4_cited_values(field, value, ok):
    a = answer(headline=claim("VG-1042", "VG-1042", values=[("VG-1042", field, value)]))
    assert check(a)["V4"] == ("pass" if ok else "fail")


def test_v4_quote_must_appear_in_the_fact_or_message():
    good = answer(headline=claim("q", "MSG:noon#2", values=[("MSG:noon#2", "quote", "Time lost: 12 hours.")]))
    body = answer(headline=claim("q", "MSG:noon#2", values=[("MSG:noon#2", "quote", "Spare seal fitted.")]))
    bad = answer(headline=claim("q", "MSG:noon#2", values=[("MSG:noon#2", "quote", "Time lost: 20 hours.")]))
    assert (check(good)["V4"], check(body)["V4"], check(bad)["V4"]) == ("pass", "pass", "fail")


def test_v5_message_only_claim_in_confirmed_fails():
    assert check(answer(confirmed_facts=[claim("12 h lost.", "MSG:noon#2")]))["V5"] == "fail"
    assert check(answer(confirmed_facts=[claim("Weather.", "INC-202")]))["V5"] == "fail"  # only Reported
    assert check(answer(confirmed_facts=[claim("12 h lost.", "MSG:noon#2", "INC-201")]))["V5"] == "pass"


def test_v5_absence_claim_citing_a_source_file_passes():
    a = answer(confirmed_facts=[claim("No vessel named Ocean Stark.", "FILE:vessels.csv")])
    assert check(a, retrieved={"VG-1042", "FILE:vessels.csv"})["V5"] == "pass"


def test_v6_port_wide_figure_as_vessel_fact_fails():
    vessel = answer(unverified_reports=[claim("Ocean Star waits 30 h.", "CONG-NLRTM-2026-10-25")])
    port = answer(unverified_reports=[claim("Rotterdam waits average 30 h.", "CONG-NLRTM-2026-10-25")])
    assert (check(vessel)["V6"], check(port)["V6"]) == ("fail", "pass")


def test_v7_inference_needs_a_basis():
    empty = Inference(text="Likely late.", evidence_ids=["VG-1042"], cited_values=[], based_on=" ")
    full = Inference(text="Likely late.", evidence_ids=["VG-1042"], cited_values=[], based_on="Revised ETA is after the window.")
    assert (check(answer(inferences=[empty]))["V7"], check(answer(inferences=[full]))["V7"]) == ("fail", "pass")


def test_v8_citing_a_suspicious_message_must_be_disclosed():
    cites = [claim("Containers on board.", "MSG:fwd#1")]
    assert check(answer(unverified_reports=cites))["V8"] == "fail"
    disclosed = answer(unverified_reports=cites, suspicious_content=[claim("Ignored an injected instruction.", "MSG:fwd#1")])
    assert check(disclosed)["V8"] == "pass"


def test_v9_false_premise_offers_no_inferences():
    inference = Inference(text="Maybe weather.", evidence_ids=["VG-1042"], cited_values=[], based_on="guess")
    assert check(answer(question_status="false_premise", inferences=[inference]))["V9"] == "fail"
    assert check(answer(question_status="false_premise"))["V9"] == "pass"


def test_v10_uncited_figure_is_only_a_warning():
    a = answer(confirmed_facts=[claim("INC-201 cost 12 h on 2026-10-06.", "INC-201")])
    results = validate(a, set(GRAPH), FakeLookup(), VESSELS)
    assert {r.check.split("_")[0]: r.status for r in results}["V10"] == "warn"
    assert passed(results)
