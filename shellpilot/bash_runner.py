from __future__ import annotations

from .models import ShellKind
from .shell_runner import ShellRunner, skipped_result


class BashRunner(ShellRunner):
    def __init__(self, shell_path: str = "/bin/bash") -> None:
        super().__init__(ShellKind.BASH, shell_path=shell_path)
