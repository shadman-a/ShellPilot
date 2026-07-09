from __future__ import annotations

import unittest

from shellpilot.run_memory import build_run_memory


class RunMemoryTests(unittest.TestCase):
    def test_build_run_memory_is_capped_and_evidence_based(self) -> None:
        turns = [
            {
                "turn": 1,
                "decision": {"action": "command", "command": "cat README.md"},
                "git_before": {"status_short": "## main"},
                "git_after": {"status_short": "## main\n M app.py\n?? notes.txt"},
                "command_result": {
                    "command": "cat README.md",
                    "stdout": "x" * 2000,
                    "stderr": "",
                    "exit_code": 0,
                    "ok": True,
                    "skipped": False,
                },
            },
            {
                "turn": 2,
                "decision": {"action": "command", "command": "touch app.py"},
                "git_after": {"status_short": "## main\n M app.py\n?? notes.txt"},
                "command_result": {
                    "command": "touch app.py",
                    "stdout": "",
                    "stderr": "",
                    "exit_code": None,
                    "ok": False,
                    "skipped": True,
                    "skip_reason": "Write commands require a successful read-only inspection first.",
                },
            },
        ]

        memory = build_run_memory(turns, max_chars=300, max_bullets=6)

        self.assertLessEqual(len(memory), 300)
        self.assertLessEqual(sum(1 for line in memory.splitlines() if line.startswith("- ")), 6)
        self.assertIn("app.py", memory)
        self.assertIn("README.md", memory)
        self.assertIn("skipped", memory)
        self.assertNotIn("invented.py", memory)

    def test_empty_or_zero_cap_omits_memory(self) -> None:
        self.assertEqual(build_run_memory([], max_chars=1000), "")
        self.assertEqual(build_run_memory([{"turn": 1}], max_chars=0), "")


if __name__ == "__main__":
    unittest.main()
