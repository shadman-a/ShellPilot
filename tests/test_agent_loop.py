from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from shellpilot import storage
from shellpilot.agent_loop import ShellPilotLoop
from shellpilot.models import ApprovalMode, PromptResult, RunConfig, ShellKind


class FakeCopilot:
    def __init__(self, response_text: str | list[str | PromptResult]) -> None:
        self.responses = list(response_text) if isinstance(response_text, list) else [response_text]
        self.prompts: list[str] = []
        self.start_new_chat_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if method == "set_event_logger":
            return None
        if method == "start_new_chat":
            self.start_new_chat_calls.append((args, kwargs))
            return {"ok": True, "method": "fake", "url": "https://m365.cloud.microsoft/chat"}
        if method == "send_turn":
            prompt = str(kwargs["prompt"])
            self.prompts.append(prompt)
            response = self.responses.pop(0) if self.responses else '{"action":"done","reason":"complete"}'
            if isinstance(response, PromptResult):
                return response
            return PromptResult(
                ts="2026-07-09T00:00:00",
                index=int(kwargs["index"]),
                prompt=prompt,
                status="success",
                duration_s=0.01,
                response_text=str(response),
            )
        raise AssertionError(f"Unexpected fake Copilot call: {method}")


class AgentLoopTests(unittest.TestCase):
    def test_run_memory_added_after_first_turn_and_carried_into_scheduled_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()

            app_data = Path(temp_dir) / "app" / ".shellpilot"
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="Memory")
                fake_copilot = FakeCopilot(
                    [
                        '{"action":"command","command":"pwd","risk":"read_only","reason":"inspect"}',
                        '{"action":"done","reason":"complete"}',
                    ]
                )
                events: list[tuple[str, dict[str, Any]]] = []
                loop = ShellPilotLoop(
                    copilot=fake_copilot,
                    output_paths=paths,
                    event_callback=lambda event, payload: events.append((event, payload)),
                    approval_callback=lambda *_: True,
                    approval_mode=ApprovalMode.FULL_ACCESS,
                    shell_kind=ShellKind.BASH,
                    max_turns=2,
                )

                loop.run(
                    task="inspect workspace",
                    workspace_dir=workspace,
                    run_config=RunConfig(chat_refresh_turns=1, run_memory_chars=300),
                    stop_event=threading.Event(),
                )

                records = [json.loads(line) for line in paths.turns_jsonl_path.read_text(encoding="utf-8").splitlines()]

        self.assertNotIn("Run memory:", fake_copilot.prompts[0])
        self.assertIn("Run memory:", fake_copilot.prompts[1])
        memory = fake_copilot.prompts[1].split("Run memory:\n", 1)[1].split("\n\nRules:", 1)[0]
        self.assertLessEqual(len(memory), 300)
        self.assertEqual(len(fake_copilot.start_new_chat_calls), 1)
        self.assertIn("scheduled_every_1_turns", [payload.get("reason") for event, payload in events if event == "chat_refreshed"])
        self.assertTrue(records[0]["run_memory"])

    def test_repeated_invalid_json_triggers_fresh_chat_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()

            app_data = Path(temp_dir) / "app" / ".shellpilot"
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="Invalid JSON")
                fake_copilot = FakeCopilot(["not json", "not json", '{"action":"done","reason":"complete"}'])
                events: list[tuple[str, dict[str, Any]]] = []
                loop = ShellPilotLoop(
                    copilot=fake_copilot,
                    output_paths=paths,
                    event_callback=lambda event, payload: events.append((event, payload)),
                    approval_callback=lambda *_: True,
                    approval_mode=ApprovalMode.FULL_ACCESS,
                    shell_kind=ShellKind.BASH,
                    max_turns=3,
                )

                loop.run(
                    task="inspect workspace",
                    workspace_dir=workspace,
                    run_config=RunConfig(stuck_recovery_threshold=2),
                    stop_event=threading.Event(),
                )

        self.assertEqual(len(fake_copilot.start_new_chat_calls), 1)
        self.assertIn("Run memory:", fake_copilot.prompts[2])
        self.assertIn("stuck:invalid_json", [payload.get("reason") for event, payload in events if event == "chat_refreshed"])
        self.assertTrue(any(payload.get("recovery_planned") for event, payload in events if event == "stuck_signal_detected"))

    def test_no_assistant_activity_triggers_fresh_chat_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()

            app_data = Path(temp_dir) / "app" / ".shellpilot"
            prompt_error = PromptResult(
                ts="2026-07-09T00:00:00",
                index=1,
                prompt="",
                status="error",
                duration_s=0.01,
                response_text="",
                error="No assistant response activity detected after sending the prompt.",
            )
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="No activity")
                fake_copilot = FakeCopilot([prompt_error, prompt_error, '{"action":"done","reason":"complete"}'])
                events: list[tuple[str, dict[str, Any]]] = []
                loop = ShellPilotLoop(
                    copilot=fake_copilot,
                    output_paths=paths,
                    event_callback=lambda event, payload: events.append((event, payload)),
                    approval_callback=lambda *_: True,
                    approval_mode=ApprovalMode.FULL_ACCESS,
                    shell_kind=ShellKind.BASH,
                    max_turns=3,
                )

                loop.run(
                    task="inspect workspace",
                    workspace_dir=workspace,
                    run_config=RunConfig(stuck_recovery_threshold=2),
                    stop_event=threading.Event(),
                )

        self.assertEqual(len(fake_copilot.start_new_chat_calls), 1)
        self.assertIn("stuck:no_assistant_activity", [payload.get("reason") for event, payload in events if event == "chat_refreshed"])

    def test_repeated_same_decision_triggers_fresh_chat_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()

            app_data = Path(temp_dir) / "app" / ".shellpilot"
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="Repeated")
                fake_copilot = FakeCopilot(
                    [
                        '{"action":"command","command":"pwd","risk":"read_only","reason":"inspect"}',
                        '{"action":"command","command":"pwd","risk":"read_only","reason":"inspect"}',
                        '{"action":"done","reason":"complete"}',
                    ]
                )
                events: list[tuple[str, dict[str, Any]]] = []
                loop = ShellPilotLoop(
                    copilot=fake_copilot,
                    output_paths=paths,
                    event_callback=lambda event, payload: events.append((event, payload)),
                    approval_callback=lambda *_: True,
                    approval_mode=ApprovalMode.FULL_ACCESS,
                    shell_kind=ShellKind.BASH,
                    max_turns=3,
                )

                loop.run(
                    task="inspect workspace",
                    workspace_dir=workspace,
                    run_config=RunConfig(stuck_recovery_threshold=2),
                    stop_event=threading.Event(),
                )

        self.assertEqual(len(fake_copilot.start_new_chat_calls), 1)
        self.assertIn("stuck:repeated_decision", [payload.get("reason") for event, payload in events if event == "chat_refreshed"])

    def test_repeated_skipped_command_triggers_fresh_chat_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()

            app_data = Path(temp_dir) / "app" / ".shellpilot"
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="Skipped")
                fake_copilot = FakeCopilot(
                    [
                        '{"action":"command","command":"touch note.txt","risk":"write_file","reason":"write"}',
                        '{"action":"command","command":"touch note.txt","risk":"write_file","reason":"write"}',
                        '{"action":"done","reason":"complete"}',
                    ]
                )
                events: list[tuple[str, dict[str, Any]]] = []
                loop = ShellPilotLoop(
                    copilot=fake_copilot,
                    output_paths=paths,
                    event_callback=lambda event, payload: events.append((event, payload)),
                    approval_callback=lambda *_: True,
                    approval_mode=ApprovalMode.ASK,
                    shell_kind=ShellKind.BASH,
                    max_turns=3,
                )

                loop.run(
                    task="write note",
                    workspace_dir=workspace,
                    run_config=RunConfig(stuck_recovery_threshold=2),
                    stop_event=threading.Event(),
                )

        self.assertEqual(len(fake_copilot.start_new_chat_calls), 1)
        self.assertIn("stuck:same_skipped_command", [payload.get("reason") for event, payload in events if event == "chat_refreshed"])

    def test_script_decision_saves_artifact_and_executes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()
            (workspace / "alpha.txt").write_text("x\n", encoding="utf-8")

            app_data = Path(temp_dir) / "app" / ".shellpilot"
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="Script")
                fake_copilot = FakeCopilot(
                    '{"action":"script","script_lines":["pwd","ls"],"risk":"read_only","reason":"inspect together"}'
                )
                events: list[tuple[str, dict[str, Any]]] = []
                loop = ShellPilotLoop(
                    copilot=fake_copilot,
                    output_paths=paths,
                    event_callback=lambda event, payload: events.append((event, payload)),
                    approval_callback=lambda *_: True,
                    approval_mode=ApprovalMode.FULL_ACCESS,
                    shell_kind=ShellKind.BASH,
                    max_turns=1,
                )

                loop.run(
                    task="inspect workspace",
                    workspace_dir=workspace,
                    run_config=RunConfig(),
                    stop_event=threading.Event(),
                )

                script_path = paths.scripts_dir / "turn_001.sh"
                script_exists = script_path.exists()
                script_text = script_path.read_text(encoding="utf-8")
                records = [json.loads(line) for line in paths.turns_jsonl_path.read_text(encoding="utf-8").splitlines()]

        self.assertTrue(script_exists)
        self.assertIn("set -euo pipefail\npwd\nls\n", script_text)
        self.assertEqual(records[0]["decision"]["action"], "script")
        self.assertIn("alpha.txt", records[0]["command_result"]["stdout"])
        self.assertTrue(records[0]["command_result"]["ok"])
        self.assertTrue(any(event == "script_saved" for event, _ in events))
        step_values = [payload.get("step") for event, payload in events if event == "step"]
        self.assertIn("Running script (1/1)", step_values)
        self.assertIn("Recording result (1/1)", step_values)

    def test_prompt_omits_git_details_but_turn_records_keep_full_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()
            subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
            (workspace / "note.txt").write_text("dirty\n", encoding="utf-8")

            app_data = Path(temp_dir) / "app" / ".shellpilot"
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="Inspect")
                fake_copilot = FakeCopilot(
                    '{"action":"command","command":"pwd","risk":"read_only","reason":"inspect"}'
                )
                events: list[tuple[str, dict[str, Any]]] = []
                loop = ShellPilotLoop(
                    copilot=fake_copilot,
                    output_paths=paths,
                    event_callback=lambda event, payload: events.append((event, payload)),
                    approval_callback=lambda *_: True,
                    approval_mode=ApprovalMode.FULL_ACCESS,
                    shell_kind=ShellKind.BASH,
                    max_turns=1,
                )

                loop.run(
                    task="inspect workspace",
                    workspace_dir=workspace,
                    run_config=RunConfig(),
                    stop_event=threading.Event(),
                )

                records = [json.loads(line) for line in paths.turns_jsonl_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(records), 1)
        self.assertIn('"details_included":false', fake_copilot.prompts[0])
        self.assertNotIn("note.txt", fake_copilot.prompts[0])
        self.assertIn("note.txt", records[0]["git_before"]["status_short"])
        self.assertIn("note.txt", records[0]["git_after"]["status_short"])
        self.assertTrue(any(event == "turn_result" for event, _ in events))


if __name__ == "__main__":
    unittest.main()
