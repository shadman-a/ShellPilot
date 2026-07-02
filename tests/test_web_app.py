from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from shellpilot.models import ApprovalMode, CommandDecision, DecisionAction, RiskLevel, ShellKind
from shellpilot.web_app import AppState


class WebAppStateTests(unittest.TestCase):
    def test_initial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = AppState(default_workspace=Path(temp_dir))
            payload = state.to_json()
            state.copilot.close()
        self.assertEqual(payload["session_status"], "not_opened")
        self.assertFalse(payload["running"])
        self.assertEqual(payload["approval_mode"], ApprovalMode.ASK.value)
        self.assertIn(payload["shell_kind"], {shell.value for shell in ShellKind})

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
        self.assertEqual(payload["session_status"], "ready")
        self.assertIsNone(payload["selector_report"])
        self.assertFalse(payload["running"])
        self.assertEqual(payload["run_folder"], "")
        self.assertEqual(payload["current_turn"], 0)
        self.assertEqual(payload["current_step"], "Idle")
        self.assertIsNone(payload["latest_command"])
        self.assertIsNone(payload["latest_result"])
        self.assertIsNone(payload["pending_approval"])
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["type"], "new_session")

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


if __name__ == "__main__":
    unittest.main()
