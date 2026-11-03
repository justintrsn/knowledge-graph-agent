"""Checks on a Cypher query written by the model, before it runs (contracts/agent-tools.md).

These give the model a clear reason when a query is refused. They are not the
safety boundary: the query runs in a read-only session, so a write that got
past these checks would still fail in Neo4j.
"""

import re

DENYLIST = (
    "CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "FOREACH", "LOAD",
    "CALL", "USE", "GRANT", "DENY", "REVOKE", "ALTER", "START", "STOP",
)
_DENIED = re.compile(r"\b(" + "|".join(DENYLIST) + r")\b", re.IGNORECASE)

# String literals, backtick-quoted names, and comments, so their text is not checked as code
_NOT_CODE = re.compile(
    r"'(?:[^'\\]|\\.)*'"
    r'|"(?:[^"\\]|\\.)*"'
    r"|`[^`]*`"
    r"|//[^\n]*"
    r"|/\*.*?\*/",
    re.DOTALL,
)

# The length part of a variable-length relationship, e.g. the "*1..3" in -[:R*1..3]->
_VAR_LENGTH = re.compile(r"\[[^\]]*\*\s*(\d*)\s*(\.\.)?\s*(\d*)\s*\]")


def strip_literals(query: str) -> str:
    """The query with strings, quoted names and comments replaced by blanks."""
    return _NOT_CODE.sub(" '' ", query)


def check(query: str, max_path_length: int) -> str | None:
    """None if the query may run, or the reason it may not."""
    code = strip_literals(query)

    denied = sorted({m.upper() for m in _DENIED.findall(code)})
    if denied:
        return f"Read-only queries only: {', '.join(denied)} is not allowed."

    for low, dots, high in _VAR_LENGTH.findall(code):
        upper = high if dots else low  # "*3" means exactly 3
        if not upper:
            return (f"Every variable-length pattern needs an upper bound of at most "
                    f"{max_path_length}, e.g. *1..{max_path_length}.")
        if int(upper) > max_path_length:
            return f"Path length {upper} is over the limit of {max_path_length}."

    if ";" in code.strip().removesuffix(";"):
        return "Send one statement at a time."
    return None
