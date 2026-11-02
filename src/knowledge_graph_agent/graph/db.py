"""Neo4j connection and sessions.

Agent tools must use read_session(). The server then rejects any write in that
session, which is the real safety boundary for agent queries.
"""

import time

from neo4j import READ_ACCESS, WRITE_ACCESS, Driver, GraphDatabase, Session
from neo4j.exceptions import ServiceUnavailable

from knowledge_graph_agent.config import Neo4jSettings


def connect(settings: Neo4jSettings, attempts: int = 15, delay_seconds: float = 2.0) -> Driver:
    """Open a driver, retrying while Neo4j is still starting up.

    Only an unreachable server is retried. Wrong credentials fail at once.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    driver = GraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))
    try:
        for attempt in range(1, attempts + 1):
            try:
                driver.verify_connectivity()
                return driver
            except ServiceUnavailable:
                if attempt == attempts:
                    raise
                time.sleep(delay_seconds)
    except Exception:
        driver.close()
        raise


def read_session(driver: Driver) -> Session:
    return driver.session(default_access_mode=READ_ACCESS)


def write_session(driver: Driver) -> Session:
    return driver.session(default_access_mode=WRITE_ACCESS)
