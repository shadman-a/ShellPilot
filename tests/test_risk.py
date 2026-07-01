from __future__ import annotations

import unittest

from shellpilot.models import RiskLevel
from shellpilot.risk import classify_command


class RiskTests(unittest.TestCase):
    def test_read_only_command(self) -> None:
        assessment = classify_command("git status --short")
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)
        self.assertTrue(assessment.allowed_shape)

    def test_pipeline_read_only(self) -> None:
        assessment = classify_command("git status --short | cat")
        self.assertEqual(assessment.risk, RiskLevel.READ_ONLY)

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


if __name__ == "__main__":
    unittest.main()

