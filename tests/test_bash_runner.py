from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from shellpilot.bash_runner import BashRunner
from shellpilot.models import RiskLevel


class BashRunnerTests(unittest.TestCase):
    def test_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = BashRunner().run(
                command="pwd",
                cwd=temp_dir,
                timeout_s=5,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.ok)

    def test_stderr_and_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = BashRunner().run(
                command="echo bad >&2; exit 7",
                cwd=temp_dir,
                timeout_s=5,
                approved=True,
                declared_risk=RiskLevel.DANGEROUS,
                computed_risk=RiskLevel.DANGEROUS,
                risk_reason="test",
            )
        self.assertEqual(result.exit_code, 7)
        self.assertIn("bad", result.stderr)

    def test_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = BashRunner().run(
                command="sleep 2",
                cwd=temp_dir,
                timeout_s=1,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        self.assertTrue(result.timed_out)

    def test_missing_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = BashRunner(shell_path="/missing/bash").run(
                command="pwd",
                cwd=temp_dir,
                timeout_s=1,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        self.assertTrue(result.skipped)
        self.assertIn("Shell not found", result.skip_reason)

    def test_uses_non_login_shell_to_preserve_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("shellpilot.bash_runner.subprocess.run") as run_mock:
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""
            run_mock.return_value.returncode = 0
            BashRunner().run(
                command="python3 --version",
                cwd=temp_dir,
                timeout_s=5,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv[:2], ["/bin/bash", "-c"])


if __name__ == "__main__":
    unittest.main()
