from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import PromptResult, TurnRecord


@dataclass(slots=True)
class OutputPaths:
    run_folder: Path
    copilot_responses_dir: Path
    screenshots_dir: Path
    logs_dir: Path
    turns_jsonl_path: Path
    events_log_path: Path


def create_run_paths(workspace_dir: str | Path) -> OutputPaths:
    workspace = Path(workspace_dir).expanduser().resolve()
    root = workspace / ".shellpilot" / "runs"
    run_name = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_folder = root / run_name
    copilot_responses_dir = run_folder / "copilot_responses"
    screenshots_dir = run_folder / "screenshots"
    logs_dir = run_folder / "logs"
    for path in (run_folder, copilot_responses_dir, screenshots_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    turns_jsonl_path = run_folder / "turns.jsonl"
    events_log_path = logs_dir / "events.log"
    turns_jsonl_path.touch(exist_ok=True)
    events_log_path.touch(exist_ok=True)
    return OutputPaths(
        run_folder=run_folder,
        copilot_responses_dir=copilot_responses_dir,
        screenshots_dir=screenshots_dir,
        logs_dir=logs_dir,
        turns_jsonl_path=turns_jsonl_path,
        events_log_path=events_log_path,
    )


def save_copilot_response(paths: OutputPaths, result: PromptResult) -> Path:
    output = paths.copilot_responses_dir / f"turn_{result.index:03d}.txt"
    output.write_text(result.response_text or result.error or "", encoding="utf-8")
    return output


def append_turn(paths: OutputPaths, turn: TurnRecord) -> None:
    append_jsonl(paths.turns_jsonl_path, turn.to_json_record())


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

