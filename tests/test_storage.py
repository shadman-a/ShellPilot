from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shellpilot import storage


class StorageTests(unittest.TestCase):
    def test_project_id_is_stable_for_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "Example Project"
            workspace.mkdir()
            first = storage.project_id_for_workspace(workspace)
            second = storage.project_id_for_workspace(workspace)

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("example-project-"))

    def test_session_paths_are_app_owned_not_workspace_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_data = Path(temp_dir) / "app" / ".shellpilot"
            workspace = Path(temp_dir) / "target-repo"
            workspace.mkdir()
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                project, session, paths = storage.create_session(
                    workspace,
                    title="Inspect repo",
                    shell_kind="bash",
                    approval_mode="ask",
                )
                self.assertEqual(project["workspace_path"], str(workspace.resolve()))
                self.assertEqual(session["project_id"], project["project_id"])
                self.assertTrue(str(paths.run_folder).startswith(str(app_data / "projects")))
                self.assertFalse((workspace / ".shellpilot").exists())
                self.assertTrue((paths.run_folder / "session.json").exists())
                self.assertTrue(paths.turns_jsonl_path.exists())
                self.assertTrue(paths.scripts_dir.exists())

    def test_save_script_writes_session_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_data = Path(temp_dir) / "app" / ".shellpilot"
            workspace = Path(temp_dir) / "target-repo"
            workspace.mkdir()
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                _, _, paths = storage.create_session(workspace, title="Run script")
                script_path = storage.save_script(paths, turn=2, shell_kind="bash", script_lines=["pwd", "ls"])
                self.assertEqual(script_path.name, "turn_002.sh")
                self.assertIn("set -euo pipefail\npwd\nls\n", script_path.read_text(encoding="utf-8"))

    def test_list_and_load_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_data = Path(temp_dir) / "app" / ".shellpilot"
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                project, session, _ = storage.create_session(workspace, title="Chat one")
                projects = storage.list_projects()
                sessions = storage.list_sessions(project["project_id"])
                loaded = storage.load_session(project["project_id"], session["session_id"])

        self.assertEqual([item["project_id"] for item in projects], [project["project_id"]])
        self.assertEqual([item["session_id"] for item in sessions], [session["session_id"]])
        self.assertEqual(loaded["title"], "Chat one")
        self.assertEqual(loaded["turns"], [])

    def test_delete_session_updates_last_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_data = Path(temp_dir) / "app" / ".shellpilot"
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                project, first, _ = storage.create_session(workspace, title="First")
                _, second, _ = storage.create_session(workspace, title="Second")

                storage.delete_session(project["project_id"], second["session_id"])

                loaded_project = storage.load_project(project["project_id"])
                sessions = storage.list_sessions(project["project_id"])

        self.assertEqual(loaded_project["last_session_id"], first["session_id"])
        self.assertEqual([item["session_id"] for item in sessions], [first["session_id"]])

    def test_delete_project_removes_app_artifacts_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_data = Path(temp_dir) / "app" / ".shellpilot"
            workspace = Path(temp_dir) / "repo"
            workspace.mkdir()
            (workspace / "keep.txt").write_text("workspace file", encoding="utf-8")
            with (
                patch.object(storage, "APP_DATA_ROOT", app_data),
                patch.object(storage, "PROJECTS_ROOT", app_data / "projects"),
            ):
                project, _, _ = storage.create_session(workspace, title="Chat")
                project_dir = app_data / "projects" / project["project_id"]

                storage.delete_project(project["project_id"])

                self.assertFalse(project_dir.exists())
                self.assertTrue((workspace / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
