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
from .models import DEFAULT_COPILOT_URL, DEFAULT_PROFILE_DIR, ApprovalMode, CommandDecision, RunConfig, ShellKind
from .shell_runner import default_shell_kind
from .storage import create_run_paths
from .utils import now_iso


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


def _filesystem_roots() -> list[str]:
    if os.name == "nt":
        roots = [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]
        return roots or [str(Path.cwd().anchor or "C:\\")]
    return ["/"]


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
        self._approval_condition = threading.Condition(self.lock)
        self._approval_answers: dict[str, bool] = {}
        self.events: list[dict[str, Any]] = []

    def to_json(self) -> dict[str, Any]:
        with self.lock:
            return {
                "copilot_url": self.copilot_url,
                "profile_dir": self.profile_dir,
                "workspace_dir": self.workspace_dir,
                "approval_mode": self.approval_mode.value,
                "shell_kind": self.shell_kind.value,
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
                "events": self.events[-120:],
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
        event = {"ts": now_iso(), "type": event_type, "payload": payload}
        with self.lock:
            self._apply_event(event_type, payload)
            self.events.append(event)
            self.events = self.events[-500:]
            clients = list(self.clients)
        for client in clients:
            try:
                client.put_nowait(event)
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

        with self.lock:
            if self.running:
                raise ValueError("A ShellPilot run is already active.")
            self.workspace_dir = str(workspace)
            self.copilot_url = str(payload.get("url") or self.copilot_url).strip() or DEFAULT_COPILOT_URL
            self.profile_dir = str(payload.get("profile_dir") or self.profile_dir).strip() or DEFAULT_PROFILE_DIR
            self.approval_mode = _parse_approval_mode(payload.get("approval_mode") or self.approval_mode.value)
            self.shell_kind = _parse_shell_kind(payload.get("shell_kind") or self.shell_kind.value)
            self.stop_event = threading.Event()
            self.running = True
            self.current_turn = 0
            self.current_step = "Starting"
            self.latest_command = None
            self.latest_result = None
            self.pending_approval = None

        output_paths = create_run_paths(workspace)
        with self.lock:
            self.run_folder = str(output_paths.run_folder)
        self.emit(
            "run_started",
            {
                "task": task,
                "workspace_dir": str(workspace),
                "run_folder": str(output_paths.run_folder),
                "approval_mode": self.approval_mode.value,
                "shell_kind": self.shell_kind.value,
            },
        )

        run_config = RunConfig(
            copilot_url=self.copilot_url,
            user_data_dir=self.profile_dir,
            max_timeout_s=int(payload.get("copilot_timeout_s") or 180),
            capture_timeout_s=int(payload.get("capture_timeout_s") or 15),
            retry_once=True,
        )
        max_turns = int(payload.get("max_turns") or 12)
        command_timeout_s = int(payload.get("command_timeout_s") or 120)

        def target() -> None:
            try:
                loop = ShellPilotLoop(
                    copilot=self.copilot,
                    output_paths=output_paths,
                    event_callback=self.emit,
                    approval_callback=self.request_approval,
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
                self.emit("run_finished", {"run_folder": str(output_paths.run_folder)})

        self.run_thread = threading.Thread(target=target, name="shellpilot-run", daemon=True)
        self.run_thread.start()
        return {"ok": True, "run_folder": str(output_paths.run_folder)}

    def stop(self) -> dict[str, Any]:
        with self.lock:
            self.stop_event.set()
            pending = self.pending_approval
            if pending:
                self._approval_answers[str(pending["id"])] = False
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

        copilot_chat: dict[str, Any] | None = None
        if should_start_copilot_chat:
            try:
                copilot_chat = self.copilot.call("start_new_chat", copilot_url, profile_dir)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Could not start a new Copilot chat thread: {exc}") from exc

        with self.lock:
            self.stop_event = threading.Event()
            self.run_thread = None
            self.run_folder = ""
            self.current_turn = 0
            self.current_step = "Idle"
            self.latest_command = None
            self.latest_result = None
            self.pending_approval = None
            self.selector_report = None
            self._approval_answers.clear()
            self.events = []
            self._approval_condition.notify_all()
            if copilot_chat:
                self.session_status = "opened"
        self.emit(
            "new_session",
            {
                "workspace_dir": self.workspace_dir,
                "approval_mode": self.approval_mode.value,
                "shell_kind": self.shell_kind.value,
                "copilot_new_chat": bool(copilot_chat),
                "copilot_chat": copilot_chat or {},
            },
        )
        return {"ok": True, "copilot_new_chat": bool(copilot_chat), "copilot_chat": copilot_chat or {}}

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
            children = [path for path in current.iterdir() if path.is_dir()]
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

    def _apply_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "turn_started":
            self.current_turn = int(payload.get("turn") or self.current_turn)
        elif event_type == "step":
            self.current_step = str(payload.get("step") or "")
        elif event_type == "turn_result":
            self.latest_command = payload.get("decision")
            self.latest_result = payload.get("command_result")
        elif event_type == "run_error":
            self.current_step = "Error"
        elif event_type == "done":
            self.current_step = "Done"


class ShellPilotHandler(BaseHTTPRequestHandler):
    state: AppState

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._send_json(self.state.to_json())
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
            payload = self._read_json()
            if self.path == "/api/open_copilot":
                result = self.state.open_copilot(payload)
            elif self.path == "/api/check_session":
                result = self.state.check_session()
            elif self.path == "/api/run":
                result = self.state.start_run(payload)
            elif self.path == "/api/stop":
                result = self.state.stop()
            elif self.path == "/api/new_session":
                result = self.state.new_session()
            elif self.path == "/api/approval_mode":
                result = self.state.set_approval_mode(payload)
            elif self.path == "/api/approval":
                result = self.state.submit_approval(payload)
            elif self.path == "/api/browse_workspace":
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
