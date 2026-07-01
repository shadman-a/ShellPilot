from __future__ import annotations

import subprocess
from pathlib import Path

from .models import GitState


def collect_git_state(workspace_dir: str | Path) -> GitState:
    workspace = Path(workspace_dir).expanduser().resolve()
    if not workspace.exists():
        return GitState(is_git_repo=False, workspace=str(workspace), error="Workspace does not exist.")

    inside = _run_git(workspace, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        return GitState(is_git_repo=False, workspace=str(workspace), error=(inside.stderr or inside.stdout).strip())

    root = _run_git(workspace, ["rev-parse", "--show-toplevel"]).stdout.strip()
    branch = _run_git(workspace, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    status_short = _run_git(workspace, ["status", "--short", "--branch"]).stdout.strip()
    diff_stat = _run_git(workspace, ["diff", "--stat"]).stdout.strip()
    diff_name_status = _run_git(workspace, ["diff", "--name-status"]).stdout.strip()
    staged_name_status = _run_git(workspace, ["diff", "--cached", "--name-status"]).stdout.strip()

    return GitState(
        is_git_repo=True,
        workspace=str(workspace),
        git_root=root,
        branch=branch,
        status_short=status_short,
        diff_stat=diff_stat,
        diff_name_status=diff_name_status,
        staged_name_status=staged_name_status,
    )


def _run_git(workspace: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return subprocess.CompletedProcess(["git", *args], returncode=1, stdout="", stderr=str(exc))

