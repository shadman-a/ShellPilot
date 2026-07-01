from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from shellpilot.git_state import collect_git_state


class GitStateTests(unittest.TestCase):
    def test_non_git_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = collect_git_state(temp_dir)
        self.assertFalse(state.is_git_repo)

    def test_git_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True, text=True, check=True)
            Path(temp_dir, "notes.txt").write_text("hello\n", encoding="utf-8")
            state = collect_git_state(temp_dir)
        self.assertTrue(state.is_git_repo)
        self.assertIn("notes.txt", state.status_short)


if __name__ == "__main__":
    unittest.main()

