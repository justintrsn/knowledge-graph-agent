import pytest
from neo4j.exceptions import ClientError

from knowledge_graph_agent.graph.db import read_session, write_session

PROBE = "KgTestProbe"


@pytest.fixture
def probe(driver):
    """One probe node with x = 0, removed after the test."""
    with write_session(driver) as session:
        session.run(f"MATCH (n:{PROBE}) DELETE n").consume()
        session.run(f"CREATE (:{PROBE} {{x: 0}})").consume()
    yield
    with write_session(driver) as session:
        session.run(f"MATCH (n:{PROBE}) DELETE n").consume()


def probe_values(driver) -> list:
    with read_session(driver) as session:
        return [r["x"] for r in session.run(f"MATCH (n:{PROBE}) RETURN n.x AS x")]


def test_read_session_can_read(driver, probe):
    assert probe_values(driver) == [0]


def test_write_session_can_write(driver, probe):
    with write_session(driver) as session:
        session.run(f"MATCH (n:{PROBE}) SET n.x = 1").consume()
    assert probe_values(driver) == [1]


@pytest.mark.parametrize(
    "query",
    [
        f"CREATE (:{PROBE} {{x: 1}})",
        f"MERGE (:{PROBE} {{x: 1}})",
        f"MATCH (n:{PROBE}) SET n.x = 1",
        f"MATCH (n:{PROBE}) DETACH DELETE n",
    ],
)
def test_read_session_rejects_writes(driver, probe, query):
    # Agent queries rely on this, so it is checked against the real server.
    with read_session(driver) as session:
        with pytest.raises(ClientError, match="read access mode"):
            session.run(query).consume()

    assert probe_values(driver) == [0]
