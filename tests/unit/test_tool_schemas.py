"""Tool schemas and input checks, offline, so a bad schema doesn't cost an
API call to find. Tools are not strict (strict tools plus the strict answer
format made a grammar too large for the API), so execute() checks inputs."""

from knowledge_graph_agent.agent import tools


def schemas(value):
    """Every nested schema object."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from schemas(v)
    elif isinstance(value, list):
        for v in value:
            yield from schemas(v)


def test_only_parameters_that_allow_null_are_optional():
    for d in tools.definitions():
        schema = d["input_schema"]
        assert schema["additionalProperties"] is False
        for name, prop in schema["properties"].items():
            assert (name in schema["required"]) != tools._allows_null(prop), (d["name"], name)


def test_enums_have_a_single_type():
    # The API rejects an enum on a type list such as ["string", "null"]; use anyOf instead
    for d in tools.definitions():
        for s in schemas(d["input_schema"]):
            if "enum" in s:
                assert isinstance(s.get("type"), str), f"{d['name']}: {s}"
                assert None not in s["enum"], f"{d['name']}: {s}"


def test_tool_names_are_unique_and_sorted():
    names = [d["name"] for d in tools.definitions()]
    assert names == sorted(set(names))


# The checks below fail before the tool runs, so no graph is needed (ctx=None).


def test_wrong_types_are_an_error_the_model_can_fix():
    for name, params in [
        ("find_vessel", {"query": 5}),
        ("incidents", {"voyage_ids": "VG-1042", "vessel_ids": None}),  # a string, not a list
        ("message_claims", {"kind": "rumour"}),  # not one of the kinds
    ]:
        result, is_error = tools.execute(None, name, params)
        assert is_error and "Bad input" in result["error"], (name, result)


def test_missing_required_and_unknown_parameters_are_errors():
    result, is_error = tools.execute(None, "find_vessel", {})
    assert is_error and "missing ['query']" in result["error"]
    result, is_error = tools.execute(None, "find_vessel", {"query": "x", "imo": "1"})
    assert is_error and "unknown ['imo']" in result["error"]


def test_left_out_optional_parameters_become_none():
    # All filters left out: the tool runs, sees None everywhere, and asks for a filter
    result, is_error = tools.execute(None, "message_claims", {})
    assert is_error and result["error"] == "Give at least one filter."
