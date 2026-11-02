"""Connection retry logic, with a fake driver so no Neo4j is needed."""

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable

from knowledge_graph_agent.config import neo4j_settings
from knowledge_graph_agent.graph import db


class FakeDriver:
    def __init__(self, failures):
        self.failures = list(failures)
        self.attempts = 0
        self.closed = False

    def verify_connectivity(self):
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    def install(*failures):
        driver = FakeDriver(failures)
        monkeypatch.setattr(db.GraphDatabase, "driver", lambda uri, auth: driver)
        monkeypatch.setattr(db.time, "sleep", lambda seconds: None)
        return driver

    return install


def test_retries_while_neo4j_starts(fake):
    driver = fake(ServiceUnavailable("starting"), ServiceUnavailable("starting"))

    assert db.connect(neo4j_settings({}), attempts=5) is driver
    assert driver.attempts == 3
    assert not driver.closed


def test_gives_up_after_the_last_attempt(fake):
    driver = fake(*[ServiceUnavailable("down")] * 3)

    with pytest.raises(ServiceUnavailable):
        db.connect(neo4j_settings({}), attempts=3)
    assert driver.attempts == 3
    assert driver.closed


def test_wrong_password_is_not_retried(fake):
    driver = fake(AuthError("bad password"))

    with pytest.raises(AuthError):
        db.connect(neo4j_settings({}), attempts=5)
    assert driver.attempts == 1
    assert driver.closed


def test_attempts_must_be_positive():
    with pytest.raises(ValueError):
        db.connect(neo4j_settings({}), attempts=0)
