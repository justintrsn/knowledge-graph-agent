import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from knowledge_graph_agent.config import (
    has_api_key,
    live_enabled,
    load_config,
    load_env,
    neo4j_settings,
)

ROOT = Path(__file__).parents[2]


def write_config(tmp_path: Path, **changes) -> Path:
    data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data.update(changes)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_committed_config_loads():
    config = load_config(ROOT / "config.yaml")

    assert config.snapshot_now.isoformat() == "2026-10-26T12:00:00+00:00"
    # "Next week" and "last month" are worked out from this weekday.
    assert config.snapshot_now.strftime("%A") == "Monday"
    assert config.models.agent == "claude-sonnet-5-5"
    assert config.agent.step_budget.soft == 5
    assert config.agent.step_budget.hard == 10
    assert config.messages.include_extra is False


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write_config(tmp_path, snapshot_nwo="2026-10-26T12:00:00Z"))


def test_snapshot_without_time_zone_is_rejected(tmp_path):
    with pytest.raises(ValidationError):
        load_config(write_config(tmp_path, snapshot_now="2026-10-26T12:00:00"))


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"ANTHROPIC_API_KEY": ""}, False),
        ({"ANTHROPIC_API_KEY": "   "}, False),
        ({"ANTHROPIC_API_KEY": "sk-test"}, True),
    ],
)
def test_blank_key_counts_as_no_key(env, expected):
    assert has_api_key(env) is expected


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"ANTHROPIC_API_KEY": "sk-test"}, True),
        ({"ANTHROPIC_API_KEY": "sk-test", "KG_LIVE": ""}, True),
        ({"ANTHROPIC_API_KEY": "sk-test", "KG_LIVE": "off"}, False),
        ({"ANTHROPIC_API_KEY": "sk-test", "KG_LIVE": " OFF "}, False),
        ({"KG_LIVE": "on"}, False),
    ],
)
def test_live_needs_a_key_and_no_off_switch(env, expected):
    assert live_enabled(env) is expected


def test_neo4j_defaults_match_compose():
    settings = neo4j_settings({})
    assert settings.uri == "bolt://localhost:7687"
    assert settings.user == "neo4j"
    assert settings.password == "localdevpassword"


def test_neo4j_settings_from_environment():
    settings = neo4j_settings(
        {"NEO4J_URI": "bolt://neo4j:7687", "NEO4J_USER": "u", "NEO4J_PASSWORD": "p"}
    )
    assert (settings.uri, settings.user, settings.password) == ("bolt://neo4j:7687", "u", "p")


def test_blank_neo4j_password_uses_default():
    # Compose passes NEO4J_PASSWORD even when .env leaves it empty.
    assert neo4j_settings({"NEO4J_PASSWORD": ""}).password == "localdevpassword"


def test_env_file_is_optional(tmp_path):
    load_env(tmp_path / "missing.env")


def test_env_file_does_not_override_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("KG_TEST_SHELL", "from shell")
    env_file = tmp_path / ".env"
    env_file.write_text("KG_TEST_SHELL=from file\nKG_TEST_FILE_ONLY=from file\n", encoding="utf-8")
    try:
        load_env(env_file)
        assert os.environ["KG_TEST_SHELL"] == "from shell"
        assert os.environ["KG_TEST_FILE_ONLY"] == "from file"
    finally:
        os.environ.pop("KG_TEST_FILE_ONLY", None)
