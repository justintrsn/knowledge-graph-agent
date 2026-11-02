"""Tests in tests/graph and tests/live need a running Neo4j, for example `docker compose up -d neo4j`.

They are skipped, not failed, when Neo4j can't be reached.
"""

import pytest
from neo4j.exceptions import ServiceUnavailable

from knowledge_graph_agent.config import load_env, neo4j_settings
from knowledge_graph_agent.graph.db import connect


@pytest.fixture(scope="session")
def driver():
    load_env()
    try:
        driver = connect(neo4j_settings(), attempts=1)
    except ServiceUnavailable:
        pytest.skip("Neo4j is not running; start it with `docker compose up -d neo4j`")
    yield driver
    driver.close()
