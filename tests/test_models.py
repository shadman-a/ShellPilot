from __future__ import annotations

import unittest

from shellpilot.models import PromptResult, TurnRecord


class ModelSerializationTests(unittest.TestCase):
    def test_prompt_result_compacts_prompt(self) -> None:
        result = PromptResult(
            ts="2026-07-01T00:00:00",
            index=1,
            prompt="x" * 1000,
            status="success",
            duration_s=1.0,
            response_text="ok",
        )
        payload = result.to_json_record()
        self.assertNotIn("prompt", payload)
        self.assertEqual(payload["prompt_chars"], 1000)
        self.assertEqual(payload["response_chars"], 2)
        self.assertLessEqual(len(payload["prompt_excerpt"]), 500)

    def test_turn_record_serializes_without_prompt_fields(self) -> None:
        record = TurnRecord(
            turn=1,
            ts="2026-07-01T00:00:00",
            task="test",
            decision={"action": "command", "command": "pwd"},
            git_before={"is_git_repo": False},
        )
        payload = record.to_json_record()
        self.assertEqual(payload["turn"], 1)
        self.assertNotIn("prompt_chars", payload)


if __name__ == "__main__":
    unittest.main()
