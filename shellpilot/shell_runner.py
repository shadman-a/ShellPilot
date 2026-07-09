from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .models import CommandResult, RiskLevel, ShellKind


def default_shell_kind() -> ShellKind:
    return ShellKind.POWERSHELL if os.name == "nt" else ShellKind.BASH


def shell_display_name(shell_kind: ShellKind) -> str:
    labels = {
        ShellKind.BASH: "Bash",
        ShellKind.POWERSHELL: "PowerShell",
        ShellKind.CMD: "Windows cmd",
    }
    return labels[shell_kind]


class ShellRunner:
    def __init__(self, shell_kind: ShellKind | str | None = None, shell_path: str | None = None) -> None:
        self.shell_kind = ShellKind(shell_kind or default_shell_kind())
        self.shell_path = shell_path

    def run(
        self,
        *,
        command: str,
        cwd: str | Path,
        timeout_s: int,
        approved: bool,
        declared_risk: RiskLevel,
        computed_risk: RiskLevel,
        risk_reason: str,
    ) -> CommandResult:
        cwd_path = Path(cwd).expanduser().resolve()
        started = time.perf_counter()
        argv, display_shell = self._argv(command)
        if argv is None:
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout="",
                stderr=f"Shell not found: {display_shell}",
                exit_code=None,
                duration_s=duration,
                timed_out=False,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                skipped=True,
                skip_reason=f"Shell not found: {display_shell}",
                shell=self.shell_kind.value,
            )

        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout_s)),
                check=False,
            )
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
                duration_s=duration,
                timed_out=False,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                shell=self.shell_kind.value,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                exit_code=None,
                duration_s=duration,
                timed_out=True,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                shell=self.shell_kind.value,
            )
        except FileNotFoundError:
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout="",
                stderr=f"Shell not found: {display_shell}",
                exit_code=None,
                duration_s=duration,
                timed_out=False,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                skipped=True,
                skip_reason=f"Shell not found: {display_shell}",
                shell=self.shell_kind.value,
            )

    def run_script(
        self,
        *,
        script_path: str | Path,
        cwd: str | Path,
        timeout_s: int,
        approved: bool,
        declared_risk: RiskLevel,
        computed_risk: RiskLevel,
        risk_reason: str,
    ) -> CommandResult:
        cwd_path = Path(cwd).expanduser().resolve()
        script = Path(script_path).expanduser().resolve()
        started = time.perf_counter()
        argv, display_shell = self._script_argv(script)
        command = " ".join(argv) if argv else str(script)
        if argv is None:
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout="",
                stderr=f"Shell not found: {display_shell}",
                exit_code=None,
                duration_s=duration,
                timed_out=False,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                skipped=True,
                skip_reason=f"Shell not found: {display_shell}",
                shell=self.shell_kind.value,
            )

        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout_s)),
                check=False,
            )
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
                duration_s=duration,
                timed_out=False,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                shell=self.shell_kind.value,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                exit_code=None,
                duration_s=duration,
                timed_out=True,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                shell=self.shell_kind.value,
            )
        except FileNotFoundError:
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout="",
                stderr=f"Shell not found: {display_shell}",
                exit_code=None,
                duration_s=duration,
                timed_out=False,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                skipped=True,
                skip_reason=f"Shell not found: {display_shell}",
                shell=self.shell_kind.value,
            )

    def _argv(self, command: str) -> tuple[list[str] | None, str]:
        if self.shell_kind == ShellKind.BASH:
            shell = self.shell_path or "/bin/bash"
            return [shell, "-c", command], shell
        if self.shell_kind == ShellKind.CMD:
            shell = self.shell_path or _first_available(["cmd.exe", "cmd"])
            if not shell:
                return None, "cmd.exe"
            return [shell, "/d", "/s", "/c", command], shell

        shell = self.shell_path or _first_available(["pwsh", "powershell.exe", "powershell"])
        if not shell:
            return None, "pwsh or powershell.exe"
        return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], shell

    def _script_argv(self, script_path: Path) -> tuple[list[str] | None, str]:
        if self.shell_kind == ShellKind.BASH:
            shell = self.shell_path or "/bin/bash"
            return [shell, str(script_path)], shell
        if self.shell_kind == ShellKind.POWERSHELL:
            shell = self.shell_path or _first_available(["pwsh", "powershell.exe", "powershell"])
            if not shell:
                return None, "pwsh or powershell.exe"
            return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)], shell
        return None, "script-capable shell"


def _first_available(candidates: list[str]) -> str:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return ""


def skipped_result(
    *,
    command: str,
    cwd: str | Path,
    reason: str,
    declared_risk: RiskLevel,
    computed_risk: RiskLevel,
    risk_reason: str,
    approved: bool = False,
    shell: ShellKind | str | None = None,
) -> CommandResult:
    shell_kind = ShellKind(shell or default_shell_kind())
    return CommandResult(
        command=command,
        cwd=str(Path(cwd).expanduser().resolve()),
        stdout="",
        stderr="",
        exit_code=None,
        duration_s=0.0,
        timed_out=False,
        approved=approved,
        declared_risk=declared_risk,
        computed_risk=computed_risk,
        risk_reason=risk_reason,
        skipped=True,
        skip_reason=reason,
        shell=shell_kind.value,
    )
