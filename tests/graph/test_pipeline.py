"""`kg run` with no key: rebuilds and verifies, then names the delivered run
without presenting it as this run's output (FR-022)."""

import json
from pathlib import Path

from knowledge_graph_agent.config import load_config
from knowledge_graph_agent.pipeline import delivered_notice, run_pipeline

ROOT = Path(__file__).resolve().parents[2]


def test_without_a_key_the_graph_is_rebuilt_and_no_run_is_made(driver, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("KG_LIVE", "off")
    said = []
    result = run_pipeline(driver, load_config(ROOT / "config.yaml"), echo=said.append)
    assert result.verified and result.run_dir is None
    assert any("Live answering was skipped" in line for line in said)


def test_notice_names_the_delivered_run_and_says_it_is_not_this_run(tmp_path):
    runs = tmp_path / "runs"
    (runs / "20261105T100000Z-abc123").mkdir(parents=True)
    (runs / "20261105T100000Z-abc123" / "manifest.json").write_text(
        json.dumps({"created_utc": "2026-11-05T10:00:00+00:00"}), encoding="utf-8"
    )
    delivered = tmp_path / "DELIVERED"
    delivered.write_text("20261105T100000Z-abc123\n", encoding="utf-8")
    notice = delivered_notice(delivered, runs)
    assert "20261105T100000Z-abc123" in notice and "2026-11-05T10:00:00" in notice
    assert "NOT the output of this run" in notice


def test_notice_when_nothing_was_delivered(tmp_path):
    notice = delivered_notice(tmp_path / "DELIVERED", tmp_path / "runs")
    assert "skipped" in notice and "No delivered evaluation run" in notice
