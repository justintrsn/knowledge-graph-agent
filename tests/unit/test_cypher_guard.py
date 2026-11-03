"""The Cypher guard's checks, with no database (T055)."""

import pytest

from knowledge_graph_agent.agent.cypher_guard import DENYLIST, check

MAX = 6


@pytest.mark.parametrize("keyword", DENYLIST)
def test_every_denylisted_keyword_is_rejected(keyword):
    for written in (keyword, keyword.lower(), keyword.capitalize()):
        reason = check(f"MATCH (n) {written} n.x = 1 RETURN n", MAX)
        assert reason and keyword in reason


def test_reads_are_allowed():
    assert check("MATCH (v:Voyage {evidence_id: 'VG-1042'}) RETURN v.evidence_id, v.status", MAX) is None
    assert check("MATCH (v:Voyage) WITH v.status AS s, count(*) AS n RETURN s, n ORDER BY n DESC", MAX) is None


def test_keywords_inside_strings_are_allowed():
    assert check("MATCH (n) WHERE n.status = 'SET' RETURN n", MAX) is None
    assert check('MATCH (n) WHERE n.note CONTAINS "delete" RETURN n', MAX) is None
    assert check("MATCH (n) WHERE n.note = 'it\\'s CREATE' RETURN n", MAX) is None


def test_keywords_inside_names_are_allowed():
    # Whole words only: "settled", "created_at" and "offset" are fine
    assert check("MATCH (n) WHERE n.settled AND n.created_at IS NOT NULL RETURN n.offset", MAX) is None
    assert check("MATCH (n) RETURN n.`DELETE` AS x", MAX) is None


def test_comments_are_stripped():
    assert check("MATCH (n) // DELETE everything\nRETURN n", MAX) is None
    assert check("MATCH (n) /* CREATE (m) */ RETURN n", MAX) is None
    assert check("MATCH (n) /* fine */ DETACH DELETE n", MAX) is not None


@pytest.mark.parametrize("pattern", ["*", "*2..", "*..99", "*7", "*1..7"])
def test_unbounded_or_long_paths_are_rejected(pattern):
    assert check(f"MATCH (a)-[:ON_VOYAGE{pattern}]->(b) RETURN b", MAX) is not None


@pytest.mark.parametrize("pattern", ["*1..3", "*..6", "*3", "* 1 .. 2"])
def test_bounded_paths_are_allowed(pattern):
    assert check(f"MATCH (a)-[r{pattern}]->(b) RETURN b", MAX) is None


def test_one_statement_only():
    assert check("MATCH (n) RETURN n;", MAX) is None  # a trailing semicolon is fine
    assert check("MATCH (n) RETURN n; MATCH (m) RETURN m", MAX) is not None
    assert check("MATCH (n) WHERE n.x = 'a;b' RETURN n", MAX) is None
