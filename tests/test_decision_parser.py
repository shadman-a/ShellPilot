from __future__ import annotations

import unittest

from shellpilot.decision_parser import DecisionParseError, decision_prompt, parse_decision
from shellpilot.models import DecisionAction, PlanDecision, RiskLevel, ShellKind


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

    def test_parse_script_decision(self) -> None:
        decision = parse_decision(
            '{"action":"script","script_lines":["pwd","ls"],"risk":"read_only","reason":"inspect"}'
        )
        self.assertEqual(decision.action, DecisionAction.SCRIPT)
        self.assertEqual(decision.script_lines, ["pwd", "ls"])
        self.assertEqual(decision.risk, RiskLevel.READ_ONLY)

    def test_parse_bounded_plan_decision(self) -> None:
        decision = parse_decision(
            '{"action":"plan","tasks":[{"title":"Inspect files","detail":"Find the relevant code."},{"title":"Apply the fix"}],"reason":"Work in two safe steps."}'
        )
        self.assertIsInstance(decision, PlanDecision)
        self.assertEqual([task.task_id for task in decision.tasks], ["task-1", "task-2"])
        self.assertEqual(decision.tasks[0].title, "Inspect files")

    def test_plan_rejects_executable_task_content(self) -> None:
        with self.assertRaises(DecisionParseError):
            parse_decision('{"action":"plan","tasks":[{"title":"Run it","command":"rm -rf /"}]}')

    def test_plan_rejects_more_than_six_tasks(self) -> None:
        tasks = ",".join('{"title":"Task %s"}' % index for index in range(7))
        with self.assertRaises(DecisionParseError):
            parse_decision('{"action":"plan","tasks":[' + tasks + ']}')

    def test_plan_execution_prompt_is_bounded(self) -> None:
        git_state = {"is_git_repo": False, "workspace": "/tmp/work"}
        context = "x" * 2000
        prompt = decision_prompt(
            task="test",
            workspace="/tmp/work",
            git_state=git_state,
            previous_result=None,
            turn=2,
            plan_context=context,
        )
        plan_context = prompt.split("Plan context:\n", 1)[1].split("\n\nRules:", 1)[0]
        self.assertLessEqual(len(plan_context), 600)

    def test_repair_clear_prose_command(self) -> None:
        decision = parse_decision("Run: `git status --short`")
        self.assertEqual(decision.action, DecisionAction.COMMAND)
        self.assertEqual(decision.command, "git status --short")
        self.assertEqual(decision.risk, RiskLevel.DANGEROUS)
        self.assertTrue(decision.raw["repaired"])

    def test_repair_clear_done_response(self) -> None:
        decision = parse_decision("Done - task complete.")
        self.assertEqual(decision.action, DecisionAction.DONE)
        self.assertTrue(decision.raw["repaired"])

    def test_repair_rejects_ambiguous_multi_command_prose(self) -> None:
        with self.assertRaises(DecisionParseError):
            parse_decision("Run `pwd` and then `ls`.")

    def test_decision_prompt_omits_git_details_by_default(self) -> None:
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
        self.assertIn('"details_included":false', prompt)
        self.assertNotIn("status_preview", prompt)
        self.assertNotIn("diff_stat_preview", prompt)
        self.assertNotIn("generated/file_99.txt", prompt)

    def test_decision_prompt_omits_empty_run_memory(self) -> None:
        git_state = {"is_git_repo": False, "workspace": "/tmp/work"}
        prompt = decision_prompt(task="test", workspace="/tmp/work", git_state=git_state, previous_result=None, turn=1)
        self.assertNotIn("Run memory:", prompt)

    def test_decision_prompt_includes_bounded_run_memory(self) -> None:
        git_state = {"is_git_repo": False, "workspace": "/tmp/work"}
        memory = "- Progress: 1 turn recorded.\n- Last success: turn 1: `pwd` exited 0."
        prompt = decision_prompt(
            task="test",
            workspace="/tmp/work",
            git_state=git_state,
            previous_result={"status": "none"},
            turn=2,
            run_memory=memory,
        )
        self.assertIn("Run memory:\n", prompt)
        self.assertIn(memory, prompt)

    def test_decision_prompt_includes_git_details_for_git_task(self) -> None:
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
        prompt = decision_prompt(
            task="show git status",
            workspace="/tmp/work",
            git_state=git_state,
            previous_result=None,
            turn=1,
        )
        self.assertIn('"details_included":true', prompt)
        self.assertIn("status_preview", prompt)
        self.assertIn("status_omitted", prompt)
        self.assertNotIn("generated/file_99.txt", prompt)

    def test_decision_prompt_includes_git_details_after_git_command(self) -> None:
        git_state = {
            "is_git_repo": True,
            "workspace": "/tmp/work",
            "git_root": "/tmp/work",
            "branch": "main",
            "dirty": True,
            "status_short": "## main\n M app.py",
            "diff_stat": "app.py | 2 +-",
            "diff_name_status": "M\tapp.py",
            "staged_name_status": "",
        }
        previous_result = {
            "turn": 1,
            "decision": {"action": "command", "command": "git status --short"},
            "command_result": {"command": "git status --short", "ok": True, "stdout": " M app.py"},
        }
        prompt = decision_prompt(
            task="what next",
            workspace="/tmp/work",
            git_state=git_state,
            previous_result=previous_result,
            turn=2,
        )
        self.assertIn('"details_included":true', prompt)
        self.assertIn("diff_stat_preview", prompt)

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
        self.assertIn("Shell:\nPowerShell", prompt)
        self.assertIn("Get-Location", prompt)
        self.assertNotIn("Choose exactly one Bash command", prompt)


if __name__ == "__main__":
    unittest.main()
