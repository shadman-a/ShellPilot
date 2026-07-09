from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from shellpilot.copilot_connector import CopilotConnector, CopilotWorker
from shellpilot.models import RunConfig


class CopilotConnectorTests(unittest.TestCase):
    def test_response_wait_uses_latest_assistant_without_full_chat_probe(self) -> None:
        connector = CopilotConnector()
        config = RunConfig(max_timeout_s=5, sample_interval_ms=200, stability_seconds=0.8)
        assistant_text = iter(["", '{"action":"done"}', '{"action":"done"}', '{"action":"done"}'])

        with (
            patch.object(connector, "_require_page", return_value=object()),
            patch.object(connector, "_snapshot_latest_assistant_text", side_effect=lambda: next(assistant_text, '{"action":"done"}')),
            patch("shellpilot.copilot_connector.selectors.has_stop_control_visible", return_value=False),
            patch("shellpilot.copilot_connector.selectors.read_chat_text") as read_chat_text,
        ):
            connector._wait_for_response_completion(config, threading.Event(), previous_chat_text="old chat")

        read_chat_text.assert_not_called()

    def test_send_start_prefers_composer_state_before_full_chat_probe(self) -> None:
        connector = CopilotConnector()
        with (
            patch.object(connector, "_require_page", return_value=object()),
            patch.object(connector, "_composer_still_has_prompt", return_value=False),
            patch("shellpilot.copilot_connector.selectors.has_stop_control_visible", return_value=False),
            patch("shellpilot.copilot_connector.selectors.read_chat_text") as read_chat_text,
        ):
            submitted = connector._wait_for_send_start(object(), "prompt", "old chat", timeout_s=2)

        self.assertTrue(submitted)
        read_chat_text.assert_not_called()

    def test_worker_timeout_budget_scales_with_prompt_attempts(self) -> None:
        config = RunConfig(max_timeout_s=20, capture_timeout_s=5, send_start_timeout_s=4, max_prompt_attempts=3)
        timeout_s = CopilotWorker._call_timeout("send_turn", {"config": config})
        self.assertEqual(timeout_s, 3 * (20 + 5 + 4 + 20))


if __name__ == "__main__":
    unittest.main()
