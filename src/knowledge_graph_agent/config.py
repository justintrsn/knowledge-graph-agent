"""Settings from config.yaml and the environment.

config.yaml holds run settings and is committed. Secrets and connection
details come from environment variables, optionally loaded from .env.
Everything in .env is optional: without an API key the graph still rebuilds.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import AwareDatetime, BaseModel, ConfigDict

DEFAULT_CONFIG_PATH = Path("config.yaml")


class _Settings(BaseModel):
    # A typo in config.yaml should fail loudly, not be silently ignored.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Models(_Settings):
    extraction: str
    agent: str
    judge: str


class StepBudget(_Settings):
    soft: int
    hard: int


class AgentSettings(_Settings):
    effort: str
    fallbacks: bool
    step_budget: StepBudget


class CypherSettings(_Settings):
    max_rows: int
    timeout_seconds: int
    max_path_length: int


class MessageSettings(_Settings):
    include_extra: bool
    # True: never call the API for extraction, only use cache/extractions/.
    use_cache: bool = True


class Config(_Settings):
    snapshot_now: AwareDatetime
    models: Models
    agent: AgentSettings
    cypher: CypherSettings
    messages: MessageSettings


class Neo4jSettings(_Settings):
    uri: str
    user: str
    password: str


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))


def load_env(path: Path = Path(".env")) -> None:
    """Load .env if it exists. Variables already set in the environment win."""
    load_dotenv(path, override=False)


def neo4j_settings(env: Mapping[str, str] = os.environ) -> Neo4jSettings:
    # Defaults match docker-compose.yml, for running outside the app container.
    return Neo4jSettings(
        uri=env.get("NEO4J_URI") or "bolt://localhost:7687",
        user=env.get("NEO4J_USER") or "neo4j",
        password=env.get("NEO4J_PASSWORD") or "localdevpassword",
    )


def has_api_key(env: Mapping[str, str] = os.environ) -> bool:
    """A blank key, as in an unedited copy of .env.example, counts as no key."""
    return bool(env.get("ANTHROPIC_API_KEY", "").strip())


def live_enabled(env: Mapping[str, str] = os.environ) -> bool:
    """Whether to answer questions live. KG_LIVE=off skips it even with a key."""
    return has_api_key(env) and env.get("KG_LIVE", "").strip().lower() != "off"
