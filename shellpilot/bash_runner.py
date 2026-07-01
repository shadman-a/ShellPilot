from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .models import CommandResult, RiskLevel


class BashRunner:
    def __init__(self, shell_path: str = "/bin/bash") -> None:
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
        try:
            completed = subprocess.run(
                [self.shell_path, "-c", command],
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
            )
        except FileNotFoundError:
            duration = time.perf_counter() - started
            return CommandResult(
                command=command,
                cwd=str(cwd_path),
                stdout="",
                stderr=f"Shell not found: {self.shell_path}",
                exit_code=None,
                duration_s=duration,
                timed_out=False,
                approved=approved,
                declared_risk=declared_risk,
                computed_risk=computed_risk,
                risk_reason=risk_reason,
                skipped=True,
                skip_reason=f"Shell not found: {self.shell_path}",
            )


def skipped_result(
    *,
    command: str,
    cwd: str | Path,
    reason: str,
    declared_risk: RiskLevel,
    computed_risk: RiskLevel,
    risk_reason: str,
    approved: bool = False,
) -> CommandResult:
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
    )
