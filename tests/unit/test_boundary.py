"""The agent never sees the expected answers (T062): nothing under agent/ imports
the evaluation code or names the question file."""

import ast
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "src" / "knowledge_graph_agent" / "agent"


def agent_files():
    files = sorted(AGENT_DIR.glob("*.py"))
    assert files, "no agent modules found"
    return files


def test_no_agent_module_imports_evaluate():
    for path in agent_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert "evaluate" not in name.split("."), f"{path.name} imports {name}"


def test_no_agent_module_references_the_question_file():
    for path in agent_files():
        text = path.read_text(encoding="utf-8")
        assert "questions.yaml" not in text and "eval/" not in text, path.name
