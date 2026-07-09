from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shellpilot.bash_runner import BashRunner
from shellpilot.models import RiskLevel, ShellKind
from shellpilot.shell_runner import ShellRunner


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
        with tempfile.TemporaryDirectory() as temp_dir, patch("shellpilot.shell_runner.subprocess.run") as run_mock:
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

    def test_powershell_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("shellpilot.shell_runner.subprocess.run") as run_mock:
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""
            run_mock.return_value.returncode = 0
            ShellRunner(ShellKind.POWERSHELL, shell_path="pwsh").run(
                command="Get-Location",
                cwd=temp_dir,
                timeout_s=5,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv, ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "Get-Location"])

    def test_cmd_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("shellpilot.shell_runner.subprocess.run") as run_mock:
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""
            run_mock.return_value.returncode = 0
            ShellRunner(ShellKind.CMD, shell_path="cmd.exe").run(
                command="dir",
                cwd=temp_dir,
                timeout_s=5,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv, ["cmd.exe", "/d", "/s", "/c", "dir"])

    def test_bash_run_script_executes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "script.sh"
            script.write_text("echo script-ok\n", encoding="utf-8")
            result = ShellRunner(ShellKind.BASH).run_script(
                script_path=script,
                cwd=temp_dir,
                timeout_s=5,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("script-ok", result.stdout)
        self.assertTrue(result.ok)

    def test_powershell_run_script_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("shellpilot.shell_runner.subprocess.run") as run_mock:
            script = Path(temp_dir) / "script.ps1"
            script.write_text("Get-Location\n", encoding="utf-8")
            run_mock.return_value.stdout = ""
            run_mock.return_value.stderr = ""
            run_mock.return_value.returncode = 0
            ShellRunner(ShellKind.POWERSHELL, shell_path="pwsh").run_script(
                script_path=script,
                cwd=temp_dir,
                timeout_s=5,
                approved=True,
                declared_risk=RiskLevel.READ_ONLY,
                computed_risk=RiskLevel.READ_ONLY,
                risk_reason="test",
            )
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv, ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script.resolve())])


if __name__ == "__main__":
    unittest.main()
