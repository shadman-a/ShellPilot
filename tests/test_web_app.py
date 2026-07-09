from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from shellpilot import storage
from shellpilot.models import ApprovalMode, CommandDecision, DecisionAction, RiskLevel, ShellKind
from shellpilot.web_app import AppState


class FakeCopilot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def call(self, method: str, *args: object, **kwargs: object) -> object:
        self.calls.append((method, args))
        if method == "start_new_chat":
            return {"ok": True, "method": "fake", "url": "https://m365.cloud.microsoft/chat"}
        return None

    def close(self) -> None:
        return


class WebAppStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._app_data_temp = tempfile.TemporaryDirectory()
        self._app_data = Path(self._app_data_temp.name) / ".shellpilot"
        self._patches = [
            patch.object(storage, "APP_DATA_ROOT", self._app_data),
            patch.object(storage, "PROJECTS_ROOT", self._app_data / "projects"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self._app_data_temp.cleanup()

    def test_initial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            payload = state.to_json()
            state.copilot.close()
        self.assertEqual(payload["session_status"], "not_opened")
        self.assertFalse(payload["running"])
        self.assertEqual(payload["approval_mode"], ApprovalMode.ASK.value)
        self.assertIn(payload["shell_kind"], {shell.value for shell in ShellKind})

    def test_live_events_have_monotonic_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                state.emit("first", {})
                state.emit("second", {})
                payload = state.to_json()
            finally:
                state.copilot.close()

        self.assertEqual([event["id"] for event in payload["events"]], [1, 2])
        self.assertEqual(payload["event_seq"], 2)

    def test_live_turn_events_are_compact_but_saved_records_can_remain_full(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                state.emit(
                    "turn_result",
                    {
                        "turn": 1,
                        "copilot_result": {"response_text": "x" * 5000},
                        "command_result": {"stdout": "y" * 5000, "stderr": "z" * 5000},
                    },
                )
                payload = state.to_json()
            finally:
                state.copilot.close()

        event_payload = payload["events"][0]["payload"]
        self.assertNotIn("copilot_result", event_payload)
        self.assertLessEqual(len(event_payload["command_result"]["stdout"]), 2400)
        self.assertLessEqual(len(event_payload["command_result"]["stderr"]), 2400)

    def test_empty_task_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                with self.assertRaises(ValueError):
                    state.start_run({"task": "", "workspace_dir": temp_dir})
            finally:
                state.copilot.close()

    def test_approval_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            result: list[bool] = []

            def waiter() -> None:
                decision = CommandDecision(
                    action=DecisionAction.COMMAND,
                    command="touch file.txt",
                    risk=RiskLevel.WRITE_FILE,
                    reason="test",
                )
                approved = state.request_approval("a1", decision, {"risk": "write_file"}, {})
                result.append(approved)

            thread = threading.Thread(target=waiter)
            thread.start()
            try:
                while state.to_json()["pending_approval"] is None:
                    pass
                state.submit_approval({"id": "a1", "approved": True})
                thread.join(timeout=2)
            finally:
                state.copilot.close()
        self.assertEqual(result, [True])

    def test_set_approval_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                result = state.set_approval_mode({"approval_mode": "full_access"})
                payload = state.to_json()
            finally:
                state.copilot.close()
        self.assertTrue(result["ok"])
        self.assertEqual(payload["approval_mode"], ApprovalMode.FULL_ACCESS.value)

    def test_invalid_approval_mode_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                with self.assertRaises(ValueError):
                    state.set_approval_mode({"approval_mode": "reckless"})
            finally:
                state.copilot.close()

    def test_approval_mode_locked_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                with state.lock:
                    state.running = True
                with self.assertRaises(ValueError):
                    state.set_approval_mode({"approval_mode": "full_access"})
            finally:
                state.copilot.close()

    def test_new_session_clears_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            state.copilot.close()
            fake_copilot = FakeCopilot()
            state.copilot = fake_copilot
            try:
                with state.lock:
                    state.session_status = "ready"
                    state.selector_report = {"passed": True}
                    state.run_folder = str(Path(temp_dir) / ".shellpilot" / "runs" / "run_test")
                    state.current_turn = 4
                    state.current_step = "Copying"
                    state.latest_command = {"command": "git status"}
                    state.latest_result = {"command": "git status", "ok": True}
                    state.pending_approval = {"id": "a1"}
                    state._approval_answers["a1"] = True
                    state.events = [{"type": "old", "payload": {}}]

                result = state.new_session()
                payload = state.to_json()
            finally:
                state.copilot.close()

        self.assertTrue(result["ok"])
        self.assertEqual(payload["session_status"], "opened")
        self.assertIsNone(payload["selector_report"])
        self.assertFalse(payload["running"])
        self.assertIn("/.shellpilot/projects/", payload["run_folder"])
        self.assertTrue(payload["active_project_id"])
        self.assertTrue(payload["active_session_id"])
        self.assertEqual(payload["current_turn"], 0)
        self.assertEqual(payload["current_step"], "Idle")
        self.assertIsNone(payload["latest_command"])
        self.assertIsNone(payload["latest_result"])
        self.assertIsNone(payload["pending_approval"])
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["type"], "new_session")
        self.assertEqual(fake_copilot.calls[0][0], "start_new_chat")
        self.assertTrue(payload["events"][0]["payload"]["copilot_new_chat"])
        self.assertEqual(payload["events"][0]["payload"]["session_id"], payload["active_session_id"])

    def test_new_session_skips_copilot_when_not_opened(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            state.copilot.close()
            fake_copilot = FakeCopilot()
            state.copilot = fake_copilot
            try:
                result = state.new_session()
            finally:
                state.copilot.close()

        self.assertTrue(result["ok"])
        self.assertFalse(result["copilot_new_chat"])
        self.assertEqual(fake_copilot.calls, [])

    def test_new_session_rejected_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                with state.lock:
                    state.running = True
                with self.assertRaises(ValueError):
                    state.new_session()
            finally:
                state.copilot.close()

    def test_browse_workspace_lists_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            child = root / "child"
            child.mkdir()
            (root / "file.txt").write_text("x", encoding="utf-8")
            state = AppState(default_workspace=root)
            try:
                payload = state.browse_workspace({"path": str(root)})
            finally:
                state.copilot.close()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["path"], str(root.resolve()))
        self.assertIn(str(Path.home()), payload["home"])
        self.assertIn("roots", payload)
        self.assertEqual([entry["name"] for entry in payload["entries"]], ["child"])

    def test_invalid_shell_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                with self.assertRaises(ValueError):
                    state.start_run({"task": "test", "workspace_dir": temp_dir, "shell_kind": "fish"})
            finally:
                state.copilot.close()

    def test_select_project_by_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_a = Path(temp_dir) / "a"
            workspace_b = Path(temp_dir) / "b"
            workspace_a.mkdir()
            workspace_b.mkdir()
            state = AppState(default_workspace=workspace_a)
            try:
                result = state.select_project({"workspace_dir": str(workspace_b)})
                payload = state.to_json()
            finally:
                state.copilot.close()

        self.assertTrue(result["ok"])
        self.assertEqual(payload["workspace_dir"], str(workspace_b.resolve()))
        self.assertEqual(payload["active_project_id"], result["project"]["project_id"])
        self.assertIn(result["project"]["project_id"], {project["project_id"] for project in payload["projects"]})

    def test_delete_active_session_loads_remaining_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            try:
                first = state.new_session()
                second = state.new_session()
                project_id = str(second["project_id"])
                first_session_id = str(first["session_id"])
                second_session_id = str(second["session_id"])
                result = state.delete_session_view({"project_id": project_id, "session_id": second_session_id})
                payload = state.to_json()
            finally:
                state.copilot.close()

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted_active"])
        self.assertEqual(payload["active_session_id"], first_session_id)
        self.assertFalse((self._app_data / "projects" / project_id / "sessions" / second_session_id).exists())

    def test_delete_project_does_not_delete_workspace_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_a = Path(temp_dir) / "a"
            workspace_b = Path(temp_dir) / "b"
            workspace_a.mkdir()
            workspace_b.mkdir()
            state = AppState(default_workspace=workspace_a)
            try:
                first_project_id = state.to_json()["active_project_id"]
                selected = state.select_project({"workspace_dir": str(workspace_b)})
                deleted_project_id = str(selected["project"]["project_id"])
                result = state.delete_project_view({"project_id": deleted_project_id})
                payload = state.to_json()
                workspace_b_still_exists = workspace_b.exists()
                deleted_project_dir_exists = (self._app_data / "projects" / deleted_project_id).exists()
            finally:
                state.copilot.close()

        self.assertTrue(result["ok"])
        self.assertTrue(result["deleted_active"])
        self.assertTrue(workspace_b_still_exists)
        self.assertEqual(payload["active_project_id"], first_project_id)
        self.assertEqual(payload["workspace_dir"], str(workspace_a.resolve()))
        self.assertFalse(deleted_project_dir_exists)

    def test_load_session_view_restores_turn_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            state.copilot.close()
            fake_copilot = FakeCopilot()
            state.copilot = fake_copilot
            try:
                state.new_session()
                first = state.to_json()
                session_id = first["active_session_id"]
                project_id = first["active_project_id"]

                from shellpilot.models import TurnRecord
                from shellpilot.storage import append_turn, output_paths_for_session, update_session

                paths = output_paths_for_session(project_id, session_id)
                append_turn(
                    paths,
                    TurnRecord(
                        turn=1,
                        ts="2026-07-02T00:00:00",
                        task="check status",
                        decision={"action": "command", "command": "git status", "risk": "read_only"},
                        git_before={"is_git_repo": False, "workspace": temp_dir},
                        command_result={
                            "command": "git status",
                            "cwd": temp_dir,
                            "stdout": "",
                            "stderr": "not a git repo",
                            "exit_code": 128,
                            "duration_s": 0.01,
                            "timed_out": False,
                            "approved": True,
                            "declared_risk": "read_only",
                            "computed_risk": "read_only",
                            "risk_reason": "test",
                            "skipped": False,
                            "skip_reason": "",
                            "shell": "bash",
                            "ok": False,
                        },
                    ),
                )
                update_session(project_id, session_id, turn_count=1, status="error")
                loaded = state.load_session_view(session_id)
                payload = state.to_json()
            finally:
                state.copilot.close()

        self.assertTrue(loaded["ok"])
        self.assertEqual(payload["active_session_id"], session_id)
        self.assertEqual(payload["latest_command"]["command"], "git status")
        self.assertEqual(payload["latest_result"]["exit_code"], 128)
        self.assertTrue(any(event["type"] == "turn_result" for event in payload["events"]))


if __name__ == "__main__":
    unittest.main()
