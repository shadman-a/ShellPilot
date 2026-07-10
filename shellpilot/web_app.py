from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import string
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent_loop import ShellPilotLoop
from .copilot_connector import CopilotWorker
from .models import (
    DEFAULT_COPILOT_URL,
    DEFAULT_PROFILE_DIR,
    ApprovalMode,
    CommandDecision,
    PlanDecision,
    RunConfig,
    RunMode,
    ShellKind,
)
from .shell_runner import default_shell_kind
from .storage import (
    create_session,
    delete_project,
    delete_session,
    ensure_project,
    find_session,
    load_project,
    list_projects,
    list_sessions,
    load_session,
    OutputPaths,
    output_paths_for_session,
    update_session,
)
from .utils import now_iso, trim_text


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"


def _parse_approval_mode(value: Any) -> ApprovalMode:
    try:
        return ApprovalMode(str(value or "").strip())
    except ValueError as exc:
        valid = ", ".join(mode.value for mode in ApprovalMode)
        raise ValueError(f"Invalid approval mode. Use one of: {valid}") from exc


def _parse_shell_kind(value: Any) -> ShellKind:
    try:
        return ShellKind(str(value or "").strip())
    except ValueError as exc:
        valid = ", ".join(shell.value for shell in ShellKind)
        raise ValueError(f"Invalid command shell. Use one of: {valid}") from exc


def _parse_run_mode(value: Any) -> RunMode:
    try:
        return RunMode(str(value or RunMode.DIRECT.value).strip())
    except ValueError as exc:
        valid = ", ".join(mode.value for mode in RunMode)
        raise ValueError(f"Invalid run mode. Use one of: {valid}") from exc


def _filesystem_roots() -> list[str]:
    if os.name == "nt":
        roots = [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
        return roots or [str(Path.cwd().anchor or "C:\\")]
    return ["/"]


def _task_title(task: str) -> str:
    compact = " ".join(str(task or "").split())
    return compact[:80] or "New chat"


def _events_from_turns(turns: list[dict[str, Any]], run_mode: str = RunMode.DIRECT.value) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not turns:
        return events
    first = turns[0]
    events.append(
        {
            "ts": str(first.get("ts") or now_iso()),
            "type": "run_started",
            "payload": {
                "task": first.get("task") or "",
                "workspace_dir": (first.get("git_before") or {}).get("workspace") or "",
                "approval_mode": first.get("approval_mode") or "",
                "run_mode": run_mode,
            },
        }
    )
    for turn in turns:
        event_type = "turn_result"
        if turn.get("done"):
            event_type = "done"
        elif turn.get("error"):
            event_type = "turn_error"
        events.append(_compact_ui_event({"ts": str(turn.get("ts") or now_iso()), "type": event_type, "payload": turn}))
    return events


def _compact_ui_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    payload = dict(event.get("payload") or {})
    if event_type in {"turn_result", "turn_error", "done", "approval_required"}:
        payload.pop("copilot_result", None)
        for key in ("git_before", "git_after"):
            if isinstance(payload.get(key), dict):
                payload[key] = _compact_git_state(payload[key])
        command_result = payload.get("command_result")
        if isinstance(command_result, dict):
            compact_result = dict(command_result)
            for key in ("stdout", "stderr"):
                if key in compact_result:
                    compact_result[key] = trim_text(str(compact_result.get(key) or ""), 2400)
            payload["command_result"] = compact_result
    return {"id": event.get("id"), "ts": event.get("ts") or now_iso(), "type": event_type, "payload": payload}


def _compact_git_state(git_state: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "is_git_repo",
        "workspace",
        "git_root",
        "branch",
        "status_short",
        "diff_stat",
        "diff_name_status",
        "staged_name_status",
        "error",
        "dirty",
    ):
        if key not in git_state:
            continue
        value = git_state[key]
        compact[key] = trim_text(str(value), 1400) if isinstance(value, str) else value
    return compact


def _latest_turn_with_result(turns: list[dict[str, Any]]) -> dict[str, Any]:
    for turn in reversed(turns):
        if turn.get("command_result"):
            return turn
    return {}


class AppState:
    def __init__(self, *, default_workspace: Path) -> None:
        self.lock = threading.RLock()
        self.clients: list[queue.Queue[dict[str, Any]]] = []
        self.copilot = CopilotWorker(ui_log_callback=self.log)
        self.copilot_url = DEFAULT_COPILOT_URL
        self.profile_dir = DEFAULT_PROFILE_DIR
        self.workspace_dir = str(default_workspace.expanduser().resolve())
        self.approval_mode = ApprovalMode.ASK
        self.shell_kind = default_shell_kind()
        self.run_mode = RunMode.DIRECT
        project = ensure_project(self.workspace_dir)
        self.active_project_id = str(project["project_id"])
        self.active_session_id = ""
        self.session_status = "not_opened"
        self.selector_report: dict[str, Any] | None = None
        self.running = False
        self.stop_event = threading.Event()
        self.run_thread: threading.Thread | None = None
        self.run_folder = ""
        self.current_turn = 0
        self.current_step = "Idle"
        self.latest_command: dict[str, Any] | None = None
        self.latest_result: dict[str, Any] | None = None
        self.pending_approval: dict[str, Any] | None = None
        self.pending_plan: dict[str, Any] | None = None
        self.plan_state: dict[str, Any] | None = None
        self._approval_condition = threading.Condition(self.lock)
        self._approval_answers: dict[str, bool] = {}
        self._plan_answers: dict[str, bool] = {}
        self.events: list[dict[str, Any]] = []
        self.event_seq = 0
        if project.get("last_session_id"):
            self._load_session_locked(self.active_project_id, str(project["last_session_id"]))

    def to_json(self) -> dict[str, Any]:
        with self.lock:
            projects = list_projects()
            sessions = list_sessions(self.active_project_id) if self.active_project_id else []
            project_sessions = {
                str(project["project_id"]): list_sessions(str(project["project_id"]))[:8]
                for project in projects
                if project.get("project_id")
            }
            active_session = next(
                (session for session in sessions if session.get("session_id") == self.active_session_id),
                None,
            )
            return {
                "copilot_url": self.copilot_url,
                "profile_dir": self.profile_dir,
                "workspace_dir": self.workspace_dir,
                "active_project_id": self.active_project_id,
                "active_session_id": self.active_session_id,
                "active_session": active_session,
                "projects": projects,
                "sessions": sessions,
                "project_sessions": project_sessions,
                "approval_mode": self.approval_mode.value,
                "shell_kind": self.shell_kind.value,
                "run_mode": self.run_mode.value,
                "available_shells": [shell.value for shell in ShellKind],
                "session_status": self.session_status,
                "selector_report": self.selector_report,
                "running": self.running,
                "run_folder": self.run_folder,
                "current_turn": self.current_turn,
                "current_step": self.current_step,
                "latest_command": self.latest_command,
                "latest_result": self.latest_result,
                "pending_approval": self.pending_approval,
                "pending_plan": self.pending_plan,
                "plan_state": self.plan_state,
                "events": self.events[-120:],
                "event_seq": self.event_seq,
            }

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        client: queue.Queue[dict[str, Any]] = queue.Queue()
        with self.lock:
            self.clients.append(client)
        return client

    def unsubscribe(self, client: queue.Queue[dict[str, Any]]) -> None:
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.event_seq += 1
            event = {"id": self.event_seq, "ts": now_iso(), "type": event_type, "payload": payload}
            self._apply_event(event_type, payload)
            public_event = _compact_ui_event(event)
            self.events.append(public_event)
            self.events = self.events[-500:]
            clients = list(self.clients)
        for client in clients:
            try:
                client.put_nowait(public_event)
            except queue.Full:
                pass

    def log(self, line: str) -> None:
        self.emit("log", {"line": line})

    def open_copilot(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url") or self.copilot_url).strip() or DEFAULT_COPILOT_URL
        profile = str(payload.get("profile_dir") or self.profile_dir).strip() or DEFAULT_PROFILE_DIR
        with self.lock:
            self.copilot_url = url
            self.profile_dir = profile
            self.session_status = "opening"
        self.emit("session_status", {"status": "opening"})
        current_url = self.copilot.call("open_copilot", url, profile)
        with self.lock:
            self.session_status = "opened"
        self.emit("session_status", {"status": "opened", "url": current_url})
        return {"ok": True, "url": current_url}

    def check_session(self) -> dict[str, Any]:
        with self.lock:
            self.session_status = "checking"
        self.emit("session_status", {"status": "checking"})
        report = self.copilot.call("test_selectors")
        report_payload = report.to_json_record()
        with self.lock:
            self.selector_report = report_payload
            self.session_status = "ready" if report.passed else "needs_attention"
        self.emit("selector_report", report_payload)
        return {"ok": report.passed, "report": report_payload}

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = str(payload.get("task") or "").strip()
        if not task:
            raise ValueError("Task is required.")
        workspace = Path(str(payload.get("workspace_dir") or self.workspace_dir)).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {workspace}")
        project = ensure_project(workspace)

        with self.lock:
            if self.running:
                raise ValueError("A ShellPilot run is already active.")
            self.workspace_dir = str(workspace)
            self.copilot_url = str(payload.get("url") or self.copilot_url).strip() or DEFAULT_COPILOT_URL
            self.profile_dir = str(payload.get("profile_dir") or self.profile_dir).strip() or DEFAULT_PROFILE_DIR
            self.approval_mode = _parse_approval_mode(payload.get("approval_mode") or self.approval_mode.value)
            self.shell_kind = _parse_shell_kind(payload.get("shell_kind") or self.shell_kind.value)
            self.run_mode = _parse_run_mode(payload.get("run_mode") or RunMode.DIRECT.value)
            self.stop_event = threading.Event()
            self.running = True
            self.current_turn = 0
            self.current_step = "Starting"
            self.latest_command = None
            self.latest_result = None
            self.pending_approval = None
            self.pending_plan = None
            self.plan_state = None
            self._plan_answers.clear()
            self.active_project_id = str(project["project_id"])

        session, output_paths = self._prepare_session_for_run(project, workspace, task, self.run_mode)
        with self.lock:
            self.run_folder = str(output_paths.run_folder)
            self.active_session_id = str(session["session_id"])
            self.events = []
        self.emit(
            "run_started",
            {
                "task": task,
                "project_id": self.active_project_id,
                "session_id": self.active_session_id,
                "session_title": session.get("title") or task,
                "workspace_dir": str(workspace),
                "run_folder": str(output_paths.run_folder),
                "approval_mode": self.approval_mode.value,
                "shell_kind": self.shell_kind.value,
                "run_mode": self.run_mode.value,
            },
        )

        run_config = RunConfig(
            copilot_url=self.copilot_url,
            user_data_dir=self.profile_dir,
            max_timeout_s=int(payload.get("copilot_timeout_s") or 180),
            capture_timeout_s=int(payload.get("capture_timeout_s") or 15),
            chat_refresh_turns=max(0, int(payload.get("chat_refresh_turns", 10) or 0)),
            retry_once=True,
            run_mode=self.run_mode,
        )
        max_turns = int(payload.get("max_turns") or 100)
        command_timeout_s = int(payload.get("command_timeout_s") or 120)

        def target() -> None:
            try:
                loop = ShellPilotLoop(
                    copilot=self.copilot,
                    output_paths=output_paths,
                    event_callback=self.emit,
                    approval_callback=self.request_approval,
                    plan_approval_callback=self.request_plan_approval,
                    approval_mode=self.approval_mode,
                    shell_kind=self.shell_kind,
                    command_timeout_s=command_timeout_s,
                    max_turns=max_turns,
                )
                loop.run(task=task, workspace_dir=workspace, run_config=run_config, stop_event=self.stop_event)
            except Exception as exc:  # noqa: BLE001
                self.emit("run_error", {"error": str(exc)})
            finally:
                with self.lock:
                    self.running = False
                    self.current_step = "Idle"
                    self.pending_approval = None
                    self.pending_plan = None
                self._touch_active_session()
                self.emit("run_finished", {"run_folder": str(output_paths.run_folder)})

        self.run_thread = threading.Thread(target=target, name="shellpilot-run", daemon=True)
        self.run_thread.start()
        return {
            "ok": True,
            "project_id": self.active_project_id,
            "session_id": self.active_session_id,
            "run_folder": str(output_paths.run_folder),
        }

    def stop(self) -> dict[str, Any]:
        with self.lock:
            self.stop_event.set()
            pending = self.pending_approval
            pending_plan = self.pending_plan
            if pending:
                self._approval_answers[str(pending["id"])] = False
            if pending_plan:
                self._plan_answers[str(pending_plan["id"])] = False
            self._approval_condition.notify_all()
        self.emit("stop_requested", {})
        return {"ok": True}

    def new_session(self) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ValueError("Stop the active run before starting a new session.")
            should_start_copilot_chat = self.session_status in {"opened", "ready", "needs_attention"}
            copilot_url = self.copilot_url
            profile_dir = self.profile_dir
            workspace = Path(self.workspace_dir).expanduser().resolve()
            approval_mode = self.approval_mode.value
            shell_kind = self.shell_kind.value

        copilot_chat: dict[str, Any] | None = None
        if should_start_copilot_chat:
            try:
                copilot_chat = self.copilot.call("start_new_chat", copilot_url, profile_dir)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Could not start a new Copilot chat thread: {exc}") from exc
        project, session, output_paths = create_session(
            workspace,
            title="New chat",
            shell_kind=shell_kind,
            approval_mode=approval_mode,
        )

        with self.lock:
            self.active_project_id = str(project["project_id"])
            self.active_session_id = str(session["session_id"])
            self.stop_event = threading.Event()
            self.run_thread = None
            self.run_folder = str(output_paths.run_folder)
            self.current_turn = 0
            self.current_step = "Idle"
            self.latest_command = None
            self.latest_result = None
            self.pending_approval = None
            self.pending_plan = None
            self.plan_state = None
            self.run_mode = RunMode.DIRECT
            self.selector_report = None
            self._approval_answers.clear()
            self._plan_answers.clear()
            self.events = []
            self._approval_condition.notify_all()
            if copilot_chat:
                self.session_status = "opened"
        self.emit(
            "new_session",
            {
                "project_id": self.active_project_id,
                "session_id": self.active_session_id,
                "workspace_dir": self.workspace_dir,
                "run_folder": self.run_folder,
                "approval_mode": self.approval_mode.value,
                "shell_kind": self.shell_kind.value,
                "run_mode": self.run_mode.value,
                "copilot_new_chat": bool(copilot_chat),
                "copilot_chat": copilot_chat or {},
            },
        )
        return {
            "ok": True,
            "project_id": self.active_project_id,
            "session_id": self.active_session_id,
            "run_folder": self.run_folder,
            "copilot_new_chat": bool(copilot_chat),
            "copilot_chat": copilot_chat or {},
        }

    def select_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ValueError("Stop the active run before switching projects.")

        project_id = str(payload.get("project_id") or "").strip()
        workspace_value = str(payload.get("workspace_dir") or "").strip()
        if project_id:
            project = load_project(project_id)
        elif workspace_value:
            workspace = Path(workspace_value).expanduser().resolve()
            if not workspace.exists() or not workspace.is_dir():
                raise ValueError(f"Workspace does not exist: {workspace}")
            project = ensure_project(workspace)
        else:
            raise ValueError("Project id or workspace path is required.")

        loaded_session: dict[str, Any] | None = None
        last_session_id = str(project.get("last_session_id") or "")
        with self.lock:
            self.workspace_dir = str(project["workspace_path"])
            self.active_project_id = str(project["project_id"])
            self._clear_loaded_session_locked()
            if last_session_id:
                loaded_session = self._load_session_locked(self.active_project_id, last_session_id)
        self.emit(
            "project_selected",
            {
                "project_id": self.active_project_id,
                "workspace_dir": self.workspace_dir,
                "session_id": self.active_session_id,
            },
        )
        return {"ok": True, "project": project, "session": loaded_session or {}}

    def load_session_view(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ValueError("Stop the active run before loading another chat.")
            active_project_id = self.active_project_id
        project, session = find_session(session_id, active_project_id)
        with self.lock:
            self.workspace_dir = str(project["workspace_path"])
            self.active_project_id = str(project["project_id"])
            self._load_session_locked(self.active_project_id, session_id, session)
        self.emit(
            "session_loaded",
            {
                "project_id": self.active_project_id,
                "session_id": self.active_session_id,
                "workspace_dir": self.workspace_dir,
            },
        )
        return {"ok": True, "project": project, "session": session}

    def delete_session_view(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ValueError("Stop the active run before deleting a chat.")
            active_project_id = self.active_project_id
            active_session_id = self.active_session_id

        session_id = str(payload.get("session_id") or "").strip()
        project_id = str(payload.get("project_id") or active_project_id).strip()
        if not session_id:
            raise ValueError("Session id is required.")
        if not project_id:
            raise ValueError("Project id is required.")

        delete_session(project_id, session_id)
        deleted_active = project_id == active_project_id and session_id == active_session_id
        loaded_session: dict[str, Any] | None = None
        with self.lock:
            if deleted_active:
                self._clear_loaded_session_locked()
                remaining = list_sessions(project_id)
                if remaining:
                    loaded_session = self._load_session_locked(project_id, str(remaining[0]["session_id"]))
        self.emit("session_deleted", {"project_id": project_id, "session_id": session_id})
        return {"ok": True, "deleted_active": deleted_active, "session": loaded_session or {}}

    def delete_project_view(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ValueError("Stop the active run before deleting a project.")
            active_project_id = self.active_project_id

        project_id = str(payload.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("Project id is required.")

        delete_project(project_id)
        deleted_active = project_id == active_project_id
        loaded_project: dict[str, Any] | None = None
        loaded_session: dict[str, Any] | None = None
        with self.lock:
            if deleted_active:
                self._clear_loaded_session_locked()
                self.active_project_id = ""
                projects = list_projects()
                if projects:
                    loaded_project = projects[0]
                    self.workspace_dir = str(loaded_project["workspace_path"])
                    self.active_project_id = str(loaded_project["project_id"])
                    last_session_id = str(loaded_project.get("last_session_id") or "")
                    if last_session_id:
                        loaded_session = self._load_session_locked(self.active_project_id, last_session_id)
        self.emit("project_deleted", {"project_id": project_id})
        return {
            "ok": True,
            "deleted_active": deleted_active,
            "project": loaded_project or {},
            "session": loaded_session or {},
        }

    def browse_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(payload.get("path") or self.workspace_dir or Path.home()).strip()
        candidate = Path(raw_path).expanduser()
        if not candidate.exists():
            raise ValueError(f"Path does not exist: {candidate}")
        if not candidate.is_dir():
            candidate = candidate.parent
        current = candidate.resolve()

        entries: list[dict[str, Any]] = []
        try:
            children = [path for path in current.iterdir() if path.is_dir() and not path.name.startswith(".")]
        except PermissionError:
            children = []
        for child in sorted(children, key=lambda item: (item.name.startswith("."), item.name.lower()))[:500]:
            entries.append(
                {
                    "name": child.name,
                    "path": str(child.resolve()),
                    "hidden": child.name.startswith("."),
                }
            )

        parent = current.parent if current.parent != current else None
        return {
            "ok": True,
            "path": str(current),
            "parent": str(parent) if parent else "",
            "home": str(Path.home()),
            "roots": _filesystem_roots(),
            "entries": entries,
            "truncated": len(children) > len(entries),
        }

    def set_approval_mode(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = _parse_approval_mode(payload.get("approval_mode"))
        with self.lock:
            if self.running:
                raise ValueError("Approval mode cannot be changed during an active run.")
            self.approval_mode = mode
        self.emit("approval_mode_changed", {"approval_mode": mode.value})
        return {"ok": True, "approval_mode": mode.value}

    def submit_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(payload.get("id") or "").strip()
        if not approval_id:
            raise ValueError("Approval id is required.")
        approved = bool(payload.get("approved"))
        with self.lock:
            self._approval_answers[approval_id] = approved
            if self.pending_approval and self.pending_approval.get("id") == approval_id:
                self.pending_approval = None
            self._approval_condition.notify_all()
        self.emit("approval_answered", {"id": approval_id, "approved": approved})
        return {"ok": True}

    def request_approval(
        self,
        approval_id: str,
        decision: CommandDecision,
        assessment: dict[str, Any],
        git_before: dict[str, Any],
    ) -> bool:
        request = {
            "id": approval_id,
            "decision": decision.to_json_record(),
            "assessment": assessment,
            "git_before": git_before,
        }
        with self.lock:
            self.pending_approval = request
        self.emit("approval_required", request)
        with self._approval_condition:
            while approval_id not in self._approval_answers and not self.stop_event.is_set():
                self._approval_condition.wait(timeout=0.25)
            approved = bool(self._approval_answers.pop(approval_id, False))
            if self.pending_approval and self.pending_approval.get("id") == approval_id:
                self.pending_approval = None
            return approved

    def submit_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(payload.get("id") or "").strip()
        action = str(payload.get("action") or "").strip().lower()
        if not plan_id or action not in {"approve", "reject"}:
            raise ValueError("Plan approval requires an id and action approve or reject.")
        with self.lock:
            pending = self.pending_plan
            if not pending or pending.get("id") != plan_id:
                raise ValueError("That plan approval is no longer pending.")
            expected_revision = int(pending.get("revision") or 0)
            supplied_revision = int(payload.get("revision") or expected_revision)
            if supplied_revision != expected_revision:
                raise ValueError("That plan revision is stale.")
            self._plan_answers[plan_id] = action == "approve"
            self.pending_plan = None
            self._approval_condition.notify_all()
        self.emit("plan_answered", {"id": plan_id, "approved": action == "approve"})
        return {"ok": True, "approved": action == "approve"}

    def request_plan_approval(
        self,
        plan_id: str,
        decision: PlanDecision,
        plan_payload: dict[str, Any],
    ) -> bool:
        request = {
            "id": plan_id,
            "revision": int(plan_payload.get("revision") or 0),
            "plan": plan_payload,
            "decision": decision.to_json_record(),
        }
        with self.lock:
            self.pending_plan = request
        self.emit("plan_approval_required", request)
        with self._approval_condition:
            while plan_id not in self._plan_answers and not self.stop_event.is_set():
                self._approval_condition.wait(timeout=0.25)
            approved = bool(self._plan_answers.pop(plan_id, False))
            if self.pending_plan and self.pending_plan.get("id") == plan_id:
                self.pending_plan = None
            return approved

    def _prepare_session_for_run(
        self,
        project: dict[str, Any],
        workspace: Path,
        task: str,
        run_mode: RunMode,
    ) -> tuple[dict[str, Any], OutputPaths]:
        project_id = str(project["project_id"])
        with self.lock:
            active_session_id = self.active_session_id if self.active_project_id == project_id else ""
            shell_kind = self.shell_kind.value
            approval_mode = self.approval_mode.value

        if active_session_id:
            try:
                active_session = load_session(project_id, active_session_id)
                if not active_session.get("turns") and active_session.get("status") in {"new", "idle"}:
                    session = update_session(
                        project_id,
                        active_session_id,
                        title=_task_title(task),
                        workspace_path=str(workspace),
                        shell_kind=shell_kind,
                        approval_mode=approval_mode,
                        run_mode=run_mode.value,
                        status="running",
                        turn_count=0,
                    )
                    return session, output_paths_for_session(project_id, active_session_id)
            except ValueError:
                pass

        _, session, paths = create_session(
            workspace,
            title=task,
            shell_kind=shell_kind,
            approval_mode=approval_mode,
            run_mode=run_mode.value,
        )
        session = update_session(project_id, str(session["session_id"]), status="running", run_mode=run_mode.value)
        return session, paths

    def _load_session_locked(
        self,
        project_id: str,
        session_id: str,
        session: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            payload = session or load_session(project_id, session_id)
        except ValueError:
            self._clear_loaded_session_locked()
            return None

        self.active_project_id = project_id
        self.active_session_id = session_id
        self.run_mode = _parse_run_mode(payload.get("run_mode") or RunMode.DIRECT.value)
        self.plan_state = payload.get("plan_state") if isinstance(payload.get("plan_state"), dict) else None
        self.run_folder = str(payload.get("run_folder") or output_paths_for_session(project_id, session_id).run_folder)
        turns = list(payload.get("turns") or [])
        self.events = _events_from_turns(turns, self.run_mode.value)
        self.current_turn = int(turns[-1].get("turn") or 0) if turns else 0
        self.current_step = "Idle"
        self.pending_approval = None
        self.pending_plan = None
        self._approval_answers.clear()
        self._plan_answers.clear()
        latest = _latest_turn_with_result(turns)
        self.latest_command = latest.get("decision") if latest else None
        self.latest_result = latest.get("command_result") if latest else None
        return payload

    def _clear_loaded_session_locked(self) -> None:
        self.active_session_id = ""
        self.run_folder = ""
        self.current_turn = 0
        self.current_step = "Idle"
        self.latest_command = None
        self.latest_result = None
        self.pending_approval = None
        self.pending_plan = None
        self.plan_state = None
        self.run_mode = RunMode.DIRECT
        self._approval_answers.clear()
        self._plan_answers.clear()
        self.events = []

    def _touch_active_session(self, **fields: Any) -> None:
        with self.lock:
            project_id = self.active_project_id
            session_id = self.active_session_id
        if not project_id or not session_id:
            return
        try:
            update_session(project_id, session_id, **fields)
        except ValueError:
            return

    def _apply_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "run_started":
            self.run_mode = _parse_run_mode(payload.get("run_mode") or RunMode.DIRECT.value)
            self._touch_active_session(status="running", run_mode=self.run_mode.value)
        elif event_type == "turn_started":
            self.current_turn = int(payload.get("turn") or self.current_turn)
        elif event_type == "step":
            self.current_step = str(payload.get("step") or "")
        elif event_type == "turn_result":
            self.latest_command = payload.get("decision")
            self.latest_result = payload.get("command_result")
            self._touch_active_session(
                status="running",
                turn_count=self.current_turn,
                run_memory=str(payload.get("run_memory") or ""),
            )
        elif event_type in {"plan_proposed", "plan_approval_required"}:
            plan = payload.get("plan")
            if isinstance(plan, dict):
                self.plan_state = plan
                self._touch_active_session(run_mode=RunMode.PLAN.value, plan_state=plan, status="running")
        elif event_type == "plan_approved":
            self.pending_plan = None
            plan = payload.get("plan")
            if isinstance(plan, dict):
                self.plan_state = plan
                self._touch_active_session(run_mode=RunMode.PLAN.value, plan_state=plan, status="running")
        elif event_type == "plan_answered":
            self.pending_plan = None
        elif event_type == "plan_task_started" or event_type == "plan_task_updated":
            plan = payload.get("plan")
            if isinstance(plan, dict):
                self.plan_state = plan
                self._touch_active_session(run_mode=RunMode.PLAN.value, plan_state=plan, status="running")
        elif event_type == "plan_replan_required":
            self.current_step = "Replanning"
            self._touch_active_session(status="replanning", run_mode=RunMode.PLAN.value, plan_state=self.plan_state)
        elif event_type == "plan_rejected":
            plan = payload.get("plan")
            if isinstance(plan, dict):
                self.plan_state = {**plan, "status": "rejected", "reason": str(payload.get("reason") or "Plan rejected.")}
            self.current_step = "Plan rejected"
            self._touch_active_session(
                status="plan_rejected",
                run_mode=RunMode.PLAN.value,
                plan_state=self.plan_state,
                turn_count=self.current_turn,
            )
        elif event_type == "plan_error":
            self.current_step = "Error"
            self._touch_active_session(status="error", run_mode=RunMode.PLAN.value, turn_count=self.current_turn)
        elif event_type == "plan_completed":
            self.plan_state = payload.get("plan") if isinstance(payload.get("plan"), dict) else self.plan_state
            self.current_step = "Plan complete"
            self._touch_active_session(
                status="done",
                run_mode=RunMode.PLAN.value,
                plan_state=self.plan_state,
                turn_count=self.current_turn,
            )
        elif event_type == "run_error":
            self.current_step = "Error"
            self._touch_active_session(status="error", turn_count=self.current_turn)
        elif event_type == "done":
            self.current_step = "Done"
            self._touch_active_session(
                status="done",
                turn_count=self.current_turn,
                run_memory=str(payload.get("run_memory") or ""),
            )
        elif event_type == "turn_error":
            self._touch_active_session(
                status="error",
                turn_count=self.current_turn,
                run_memory=str(payload.get("run_memory") or ""),
            )
        elif event_type == "stopped":
            self.current_step = "Stopped"
            self._touch_active_session(status="stopped", turn_count=self.current_turn)
        elif event_type == "max_turns":
            self.current_step = "Max turns"
            self._touch_active_session(status="max_turns", turn_count=self.current_turn)


class ShellPilotHandler(BaseHTTPRequestHandler):
    state: AppState

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._send_json(self.state.to_json())
            return
        if parsed.path == "/api/projects":
            self._send_json({"projects": list_projects()})
            return
        if parsed.path.startswith("/api/projects/") and parsed.path.endswith("/sessions"):
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4:
                self._send_json({"sessions": list_sessions(parts[2])})
                return
        if parsed.path.startswith("/api/session/"):
            session_id = parsed.path.rsplit("/", 1)[-1]
            self._send_json(self.state.load_session_view(session_id))
            return
        if parsed.path == "/events":
            self._serve_events()
            return
        self._serve_static(parsed.path)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            body = json.dumps(self.state.to_json(), ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self._serve_static(parsed.path, head_only=True)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            payload = self._read_json()
            if path == "/api/open_copilot":
                result = self.state.open_copilot(payload)
            elif path == "/api/check_session":
                result = self.state.check_session()
            elif path == "/api/run":
                result = self.state.start_run(payload)
            elif path == "/api/stop":
                result = self.state.stop()
            elif path in {"/api/new_session", "/api/session/new"}:
                result = self.state.new_session()
            elif path == "/api/projects/select":
                result = self.state.select_project(payload)
            elif path == "/api/projects/delete":
                result = self.state.delete_project_view(payload)
            elif path == "/api/session/delete":
                result = self.state.delete_session_view(payload)
            elif path == "/api/approval_mode":
                result = self.state.set_approval_mode(payload)
            elif path == "/api/approval":
                result = self.state.submit_approval(payload)
            elif path == "/api/plan":
                result = self.state.submit_plan(payload)
            elif path == "/api/browse_workspace":
                result = self.state.browse_workspace(payload)
            else:
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(result)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, request_path: str, *, head_only: bool = False) -> None:
        path = request_path.lstrip("/") or "index.html"
        if path == "":
            path = "index.html"
        target = (WEB_ROOT / path).resolve()
        if WEB_ROOT not in target.parents and target != WEB_ROOT:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _serve_events(self) -> None:
        client = self.state.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = client.get(timeout=20)
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"event: message\ndata: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except Exception:
            return
        finally:
            self.state.unsubscribe(client)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ShellPilot local web GUI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", default=str(PROJECT_ROOT))
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser tab automatically.")
    return parser


def create_server(host: str, port: int, state: AppState) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for candidate in range(port, port + 50):
        try:
            handler = type("BoundShellPilotHandler", (ShellPilotHandler,), {"state": state})
            return ThreadingHTTPServer((host, candidate), handler), candidate
        except OSError as exc:
            last_error = exc
    raise RuntimeError(f"Could not bind local server: {last_error}")


def main() -> None:
    args = build_arg_parser().parse_args()
    state = AppState(default_workspace=Path(args.workspace))
    server, actual_port = create_server(args.host, int(args.port), state)
    url = f"http://{args.host}:{actual_port}/"
    print(f"ShellPilot running at {url}")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        state.copilot.close()
        server.server_close()


if __name__ == "__main__":
    main()
