from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from shellpilot.models import ApprovalMode, CommandDecision, DecisionAction, RiskLevel
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


if __name__ == "__main__":
    unittest.main()
