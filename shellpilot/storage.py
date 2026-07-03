from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import PromptResult, TurnRecord
from .utils import now_iso


APP_ROOT = Path(__file__).resolve().parent.parent
APP_DATA_ROOT = APP_ROOT / ".shellpilot"
PROJECTS_ROOT = APP_DATA_ROOT / "projects"


@dataclass(slots=True)
class OutputPaths:
    run_folder: Path
    copilot_responses_dir: Path
    screenshots_dir: Path
    logs_dir: Path
    turns_jsonl_path: Path
    events_log_path: Path


def app_data_root() -> Path:
    APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    return APP_DATA_ROOT


def normalized_workspace(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir).expanduser().resolve()


def project_id_for_workspace(workspace_dir: str | Path) -> str:
    workspace = normalized_workspace(workspace_dir)
    slug = _slugify(workspace.name or "workspace")
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def ensure_project(workspace_dir: str | Path) -> dict[str, Any]:
    workspace = normalized_workspace(workspace_dir)
    project_id = project_id_for_workspace(workspace)
    folder = _project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    metadata_path = folder / "project.json"
    existing = _read_json(metadata_path)
    now = now_iso()
    payload = {
        "project_id": project_id,
        "title": workspace.name or str(workspace),
        "workspace_path": str(workspace),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "last_session_id": existing.get("last_session_id") or "",
    }
    _write_json(metadata_path, payload)
    (folder / "sessions").mkdir(parents=True, exist_ok=True)
    return payload


def list_projects() -> list[dict[str, Any]]:
    app_data_root()
    projects: list[dict[str, Any]] = []
    for metadata_path in PROJECTS_ROOT.glob("*/project.json"):
        payload = _read_json(metadata_path)
        if payload.get("project_id") and payload.get("workspace_path"):
            projects.append(payload)
    return sorted(projects, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def list_sessions(project_id: str) -> list[dict[str, Any]]:
    sessions_dir = _sessions_dir(project_id)
    sessions: list[dict[str, Any]] = []
    if not sessions_dir.exists():
        return sessions
    for metadata_path in sessions_dir.glob("*/session.json"):
        payload = _read_json(metadata_path)
        if payload.get("session_id"):
            sessions.append(payload)
    return sorted(sessions, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def delete_project(project_id: str) -> None:
    folder = _project_dir(project_id)
    if not folder.exists():
        raise ValueError(f"Unknown project: {project_id}")
    shutil.rmtree(folder)


def delete_session(project_id: str, session_id: str) -> None:
    folder = _session_dir(project_id, session_id)
    if not folder.exists():
        raise ValueError(f"Unknown session: {session_id}")
    shutil.rmtree(folder)

    project_path = _project_dir(project_id) / "project.json"
    project = _read_json(project_path)
    if project and project.get("last_session_id") == session_id:
        sessions = list_sessions(project_id)
        project["last_session_id"] = str(sessions[0]["session_id"]) if sessions else ""
        project["updated_at"] = now_iso()
        _write_json(project_path, project)


def create_session(
    workspace_dir: str | Path,
    *,
    title: str = "",
    shell_kind: str = "bash",
    approval_mode: str = "ask",
) -> tuple[dict[str, Any], dict[str, Any], OutputPaths]:
    project = ensure_project(workspace_dir)
    session_id = _new_session_id(project["project_id"])
    session_dir = _session_dir(project["project_id"], session_id)
    paths = _paths_for_session_dir(session_dir)
    for path in (paths.run_folder, paths.copilot_responses_dir, paths.screenshots_dir, paths.logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    paths.turns_jsonl_path.touch(exist_ok=True)
    paths.events_log_path.touch(exist_ok=True)

    now = now_iso()
    session = {
        "project_id": project["project_id"],
        "session_id": session_id,
        "title": _session_title(title),
        "workspace_path": project["workspace_path"],
        "shell_kind": shell_kind,
        "approval_mode": approval_mode,
        "created_at": now,
        "updated_at": now,
        "status": "new",
        "run_folder": str(paths.run_folder),
        "turn_count": 0,
    }
    _write_session(project["project_id"], session_id, session)
    _touch_project(project["project_id"], last_session_id=session_id)
    return load_project(project["project_id"]), session, paths


def create_run_paths(workspace_dir: str | Path) -> OutputPaths:
    _, _, paths = create_session(workspace_dir)
    return paths


def output_paths_for_session(project_id: str, session_id: str) -> OutputPaths:
    return _paths_for_session_dir(_session_dir(project_id, session_id))


def load_project(project_id: str) -> dict[str, Any]:
    payload = _read_json(_project_dir(project_id) / "project.json")
    if not payload:
        raise ValueError(f"Unknown project: {project_id}")
    return payload


def load_session(project_id: str, session_id: str) -> dict[str, Any]:
    payload = _read_json(_session_dir(project_id, session_id) / "session.json")
    if not payload:
        raise ValueError(f"Unknown session: {session_id}")
    payload["turns"] = load_turns(project_id, session_id)
    payload["events_log"] = _read_event_log(project_id, session_id)
    return payload


def find_session(session_id: str, project_id: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    if project_id:
        project = load_project(project_id)
        return project, load_session(project_id, session_id)
    for project in list_projects():
        try:
            return project, load_session(str(project["project_id"]), session_id)
        except ValueError:
            continue
    raise ValueError(f"Unknown session: {session_id}")


def load_turns(project_id: str, session_id: str) -> list[dict[str, Any]]:
    path = output_paths_for_session(project_id, session_id).turns_jsonl_path
    return _read_jsonl(path)


def update_session(project_id: str, session_id: str, **fields: Any) -> dict[str, Any]:
    path = _session_dir(project_id, session_id) / "session.json"
    payload = _read_json(path)
    if not payload:
        raise ValueError(f"Unknown session: {session_id}")
    payload.update(fields)
    payload["updated_at"] = now_iso()
    _write_json(path, payload)
    _touch_project(project_id, last_session_id=session_id)
    return payload


def save_copilot_response(paths: OutputPaths, result: PromptResult) -> Path:
    output = paths.copilot_responses_dir / f"turn_{result.index:03d}.txt"
    output.write_text(result.response_text or result.error or "", encoding="utf-8")
    return output


def append_turn(paths: OutputPaths, turn: TurnRecord) -> None:
    append_jsonl(paths.turns_jsonl_path, turn.to_json_record())


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _project_dir(project_id: str) -> Path:
    app_data_root()
    return PROJECTS_ROOT / project_id


def _sessions_dir(project_id: str) -> Path:
    return _project_dir(project_id) / "sessions"


def _session_dir(project_id: str, session_id: str) -> Path:
    return _sessions_dir(project_id) / session_id


def _paths_for_session_dir(session_dir: Path) -> OutputPaths:
    return OutputPaths(
        run_folder=session_dir,
        copilot_responses_dir=session_dir / "copilot_responses",
        screenshots_dir=session_dir / "screenshots",
        logs_dir=session_dir / "logs",
        turns_jsonl_path=session_dir / "turns.jsonl",
        events_log_path=session_dir / "logs" / "events.log",
    )


def _new_session_id(project_id: str) -> str:
    base = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_id = base
    index = 2
    while _session_dir(project_id, session_id).exists():
        session_id = f"{base}_{index}"
        index += 1
    return session_id


def _write_session(project_id: str, session_id: str, payload: dict[str, Any]) -> None:
    session_dir = _session_dir(project_id, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(session_dir / "session.json", payload)


def _touch_project(project_id: str, *, last_session_id: str = "") -> None:
    path = _project_dir(project_id) / "project.json"
    payload = _read_json(path)
    if not payload:
        return
    payload["updated_at"] = now_iso()
    if last_session_id:
        payload["last_session_id"] = last_session_id
    _write_json(path, payload)


def _session_title(title: str) -> str:
    compact = " ".join(str(title or "").split())
    if not compact:
        return "New chat"
    return compact[:80]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return slug[:40] or "workspace"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _read_event_log(project_id: str, session_id: str, limit: int = 120) -> list[dict[str, Any]]:
    path = output_paths_for_session(project_id, session_id).events_log_path
    rows = _read_jsonl(path)
    return rows[-limit:]
