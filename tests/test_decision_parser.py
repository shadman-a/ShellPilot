from __future__ import annotations

import unittest

from shellpilot.decision_parser import DecisionParseError, decision_prompt, parse_decision
from shellpilot.models import DecisionAction, RiskLevel, ShellKind


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

    def test_chat_tail_uses_latest_json_object(self) -> None:
        response = """
        older response {"action":"command","command":"pwd","risk":"read_only","reason":"old"}
        latest response {"action":"command","command":"git status --short","risk":"read_only","reason":"new"}
        """
        decision = parse_decision(response)
        self.assertEqual(decision.command, "git status --short")
        self.assertEqual(decision.reason, "new")

    def test_jsonish_command_with_unescaped_inner_quotes(self) -> None:
        response = (
            '{"action":"command","command":"python3 -c "import csv;'
            "rows=list(csv.reader(open('account_export.csv')));print(len(rows))"
            '"","risk":"dangerous","reason":"inspect csv with python"}'
        )
        decision = parse_decision(response)
        self.assertEqual(decision.action, DecisionAction.COMMAND)
        self.assertIn("python3 -c", decision.command)
        self.assertEqual(decision.risk, RiskLevel.DANGEROUS)

    def test_jsonish_exec_command_with_unescaped_nested_quotes(self) -> None:
        response = (
            '{"action":"command","command":"python3 -c \'exec("import csv\\n'
            'rows=list(csv.DictReader(open(\\"account_export.csv\\")))")\'",'
            '"risk":"medium","reason":"create csv"}'
        )
        decision = parse_decision(response)
        self.assertEqual(decision.action, DecisionAction.COMMAND)
        self.assertIn("python3 -c", decision.command)
        self.assertIn("exec(", decision.command)

    def test_decision_prompt_compacts_large_git_state(self) -> None:
        git_state = {
            "is_git_repo": True,
            "workspace": "/tmp/work",
            "git_root": "/tmp/work",
            "branch": "main",
            "dirty": True,
            "status_short": "\n".join(["## main", *[f"?? generated/file_{idx}.txt" for idx in range(100)]]),
            "diff_stat": "\n".join([f"file_{idx}.py | 10 +-" for idx in range(80)]),
            "diff_name_status": "\n".join([f"M\tfile_{idx}.py" for idx in range(80)]),
            "staged_name_status": "",
        }
        prompt = decision_prompt(task="test", workspace="/tmp/work", git_state=git_state, previous_result=None, turn=1)
        self.assertLess(len(prompt), 8000)
        self.assertIn("status_omitted", prompt)
        self.assertNotIn("generated/file_99.txt", prompt)

    def test_decision_prompt_is_shell_aware(self) -> None:
        git_state = {"is_git_repo": False, "workspace": "/tmp/work"}
        prompt = decision_prompt(
            task="test",
            workspace="/tmp/work",
            git_state=git_state,
            previous_result=None,
            turn=1,
            shell=ShellKind.POWERSHELL,
        )
        self.assertIn("Local command shell:\nPowerShell", prompt)
        self.assertIn("Get-Location", prompt)
        self.assertNotIn("Choose exactly one Bash command", prompt)


if __name__ == "__main__":
    unittest.main()
