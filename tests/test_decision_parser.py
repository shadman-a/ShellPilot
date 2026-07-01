from __future__ import annotations

import unittest

from shellpilot.decision_parser import DecisionParseError, parse_decision
from shellpilot.models import DecisionAction, RiskLevel


class DecisionParserTests(unittest.TestCase):
    def test_parse_command_decision(self) -> None:
        decision = parse_decision('{"action":"command","command":"pwd","risk":"read_only","reason":"inspect"}')
        self.assertEqual(decision.action, DecisionAction.COMMAND)
        self.assertEqual(decision.command, "pwd")
        self.assertEqual(decision.risk, RiskLevel.READ_ONLY)

    def test_parse_fenced_json(self) -> None:
        decision = parse_decision('```json\n{"action":"done","reason":"complete"}\n```')
        self.assertEqual(decision.action, DecisionAction.DONE)
        self.assertEqual(decision.reason, "complete")

    def test_parse_smart_quotes(self) -> None:
        decision = parse_decision("{\u201caction\u201d:\u201ccommand\u201d,\u201ccommand\u201d:\u201cls\u201d,\u201crisk\u201d:\u201cread_only\u201d}")
        self.assertEqual(decision.command, "ls")

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(DecisionParseError):
            parse_decision("not json")

    def test_command_requires_command_text(self) -> None:
        with self.assertRaises(DecisionParseError):
            parse_decision('{"action":"command","risk":"read_only"}')


if __name__ == "__main__":
    unittest.main()

