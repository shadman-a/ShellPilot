from __future__ import annotations

import unittest

from shellpilot.models import RiskLevel, ShellKind
from shellpilot.risk import classify_command, classify_script_lines


class RiskTests(unittest.TestCase):
    def test_read_only_command(self) -> None:
        assessment = classify_command("git status --short")
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)
        self.assertTrue(assessment.allowed_shape)

    def test_pipeline_read_only(self) -> None:
        assessment = classify_command("git status --short | cat")
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)

    def test_quoted_pipe_does_not_split_pipeline(self) -> None:
        command = "find . -maxdepth 2 -type f | grep -E 'sfdx-project.json|package.json|README|\\.csv$' | sort"
        assessment = classify_command(command)
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)
        self.assertTrue(assessment.allowed_shape)

    def test_quoted_python_semicolon_is_not_shell_separator(self) -> None:
        command = 'python3 -c \'import csv;f=open("account_classified.csv","w");f.close()\''
        assessment = classify_command(command)
        self.assertTrue(assessment.allowed_shape)
        self.assertNotEqual(assessment.reason, "Multiple commands are not allowed.")

    def test_write_redirection(self) -> None:
        assessment = classify_command("echo hello > notes.txt")
        self.assertEqual(assessment.risk, RiskLevel.WRITE_FILE)

    def test_network_command(self) -> None:
        assessment = classify_command("curl https://example.com")
        self.assertEqual(assessment.risk, RiskLevel.NETWORK)

    def test_dangerous_command(self) -> None:
        assessment = classify_command("rm -rf .")
        self.assertEqual(assessment.risk, RiskLevel.DANGEROUS)

    def test_multi_command_rejected(self) -> None:
        assessment = classify_command("pwd && ls")
        self.assertEqual(assessment.risk, RiskLevel.DANGEROUS)
        self.assertFalse(assessment.allowed_shape)

    def test_single_ampersand_rejected_for_windows_shells(self) -> None:
        assessment = classify_command("dir & type README.md", shell=ShellKind.CMD)
        self.assertEqual(assessment.risk, RiskLevel.DANGEROUS)
        self.assertFalse(assessment.allowed_shape)

    def test_powershell_read_only(self) -> None:
        assessment = classify_command("Get-ChildItem -Force", shell=ShellKind.POWERSHELL)
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)

    def test_powershell_write(self) -> None:
        assessment = classify_command("Set-Content notes.txt hello", shell=ShellKind.POWERSHELL)
        self.assertEqual(assessment.risk, RiskLevel.WRITE_FILE)

    def test_cmd_read_only(self) -> None:
        assessment = classify_command("dir", shell=ShellKind.CMD)
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)

    def test_cmd_delete_dangerous(self) -> None:
        assessment = classify_command("del notes.txt", shell=ShellKind.CMD)
        self.assertEqual(assessment.risk, RiskLevel.DANGEROUS)

    def test_script_lines_read_only(self) -> None:
        assessment = classify_script_lines(["pwd", "ls"])
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)
        self.assertTrue(assessment.allowed_shape)

    def test_script_lines_block_compound_shell_line(self) -> None:
        assessment = classify_script_lines(["pwd && ls"])
        self.assertEqual(assessment.risk, RiskLevel.DANGEROUS)
        self.assertFalse(assessment.allowed_shape)
        self.assertIn("Script line 1", assessment.reason)

    def test_powershell_script_write(self) -> None:
        assessment = classify_script_lines(["Get-Location", "Set-Content notes.txt hello"], shell=ShellKind.POWERSHELL)
        self.assertEqual(assessment.risk, RiskLevel.WRITE_FILE)
        self.assertTrue(assessment.allowed_shape)

    def test_cmd_script_not_supported(self) -> None:
        assessment = classify_script_lines(["dir"], shell=ShellKind.CMD)
        self.assertEqual(assessment.risk, RiskLevel.DANGEROUS)
        self.assertFalse(assessment.allowed_shape)


if __name__ == "__main__":
    unittest.main()
