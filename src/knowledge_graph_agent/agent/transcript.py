"""Save each run so it can be read later with no API key.

outputs/runs/<run_id>/
    manifest.json          status, git commit, models, config, graph coverage
    <question_id>.json     the full transcript
    <question_id>.md       the same, readable

The run ID uses the real clock, only to label the folder. The agent itself
only ever sees the snapshot time.
"""

import json
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from knowledge_graph_agent.agent.answer import render
from knowledge_graph_agent.agent.loop import AgentRun, dump
from knowledge_graph_agent.agent.validate import ValidationResult, passed
from knowledge_graph_agent.config import Config

DEFAULT_RUNS_DIR = Path("outputs/runs")

Status = Literal["ok", "incomplete", "failed_validation", "failed"]


def status_of(run: AgentRun, validation: list[ValidationResult]) -> Status:
    if run.status == "failed":
        return "failed"
    if not passed(validation):
        return "failed_validation"
    return run.status


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def new_run_dir(config: Config, coverage: list[dict[str, Any]], runs_dir: Path = DEFAULT_RUNS_DIR) -> Path:
    """Create outputs/runs/<UTC time>-<short hash>/ with its manifest."""
    created = datetime.now(UTC)
    run_dir = runs_dir / f"{created:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    run_dir.mkdir(parents=True)
    write_json(run_dir / "manifest.json", {
        "run_id": run_dir.name,
        "created_utc": created.isoformat(),
        "status": "running",
        "git_commit": _git_commit(),
        "models": config.models.model_dump(),
        "config": config.model_dump(mode="json"),
        "graph_coverage": coverage,
        "questions": {},
    })
    return run_dir


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n")


def save_transcript(
    run_dir: Path,
    question_id: str,
    run: AgentRun,
    validation: list[ValidationResult],
    config: Config,
) -> Status:
    """Write <question_id>.json and .md, and record the status in the manifest."""
    status = status_of(run, validation)
    data = dump(run) | {
        "question_id": question_id,
        "status": status,
        "config": config.model_dump(mode="json"),
        "validation": [r.__dict__ for r in validation],
    }
    write_json(run_dir / f"{question_id}.json", data)
    (run_dir / f"{question_id}.md").write_text(markdown(data, run), encoding="utf-8", newline="\n")

    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["questions"][question_id] = status
    statuses = set(manifest["questions"].values())
    manifest["status"] = "failed" if "failed" in statuses else "complete"
    write_json(manifest_path, manifest)
    return status


def _fence(data: Any) -> str:
    return "```json\n" + json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n```"


def markdown(data: dict[str, Any], run: AgentRun) -> str:
    lines = [
        f"# {data['question_id']}: {data['question']}",
        "",
        f"**Status:** {data['status']}" + (f": {data['error']}" if data["error"] else ""),
        "",
    ]
    for step in data["steps"]:
        usage = step["usage"]
        lines += [
            f"## Step {step['index']} ({step['model']}, stop: {step['stop_reason']})",
            "",
            f"Tokens: {usage.get('input_tokens')} in, {usage.get('output_tokens')} out, "
            f"{usage.get('cache_read_input_tokens')} read from cache.",
            "",
        ]
        if step["thinking"]:
            lines += [f"> {line}" for summary in step["thinking"] for line in summary.splitlines()]
            lines.append("")
        for note in step["fallbacks"]:
            lines += [f"**Fallback:** {note}", ""]
        for call in step["tool_calls"]:
            flag = " (error)" if call["is_error"] else ""
            lines += [
                f"### Tool `{call['name']}`{flag}, {call['duration_ms']} ms",
                "",
                "Input:", "", _fence(call["input"]), "",
                "Output:", "", _fence(call["output"]), "",
            ]
        if step["notice"]:
            lines += [f"**System notice:** {step['notice']}", ""]

    lines += ["## Answer", ""]
    lines += ["```text", render(run.answer), "```", ""] if run.answer else ["(no answer)", ""]
    lines += ["## Validation", ""]
    lines += [f"- {r['status'].upper()} {r['check']}" + (f": {r['detail']}" if r["detail"] else "") for r in data["validation"]]
    return "\n".join(lines) + "\n"
