from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


DEFAULT_COPILOT_URL = "https://m365.cloud.microsoft/chat"
DEFAULT_PROFILE_DIR = str(Path.home() / ".shellpilot_copilot_profile")


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    WRITE_FILE = "write_file"
    NETWORK = "network"
    DANGEROUS = "dangerous"


class ApprovalMode(StrEnum):
    ASK = "ask"
    APPROVE_FOR_ME = "approve_for_me"
    FULL_ACCESS = "full_access"


class ShellKind(StrEnum):
    BASH = "bash"
    POWERSHELL = "powershell"
    CMD = "cmd"


class DecisionAction(StrEnum):
    COMMAND = "command"
    SCRIPT = "script"
    DONE = "done"


@dataclass(slots=True)
class RunConfig:
    copilot_url: str = DEFAULT_COPILOT_URL
    user_data_dir: str = DEFAULT_PROFILE_DIR
    max_timeout_s: int = 180
    capture_timeout_s: int = 15
    stability_seconds: float = 2.0
    sample_interval_ms: int = 500
    inter_prompt_delay_s: float = 1.0
    retry_once: bool = True
    max_prompt_attempts: int = 3
    chat_refresh_turns: int = 10
    send_start_timeout_s: float = 6.0
    no_activity_timeout_s: float = 20.0
    run_memory_chars: int = 1000
    stuck_recovery_threshold: int = 2


@dataclass(slots=True)
class SelectorTestReport:
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    composer_found: bool = False
    send_control_found: bool = False
    enter_fallback_available: bool = False
    stop_control_found: bool = False
    assistant_message_found: bool = False
    details: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.composer_found and (self.send_control_found or self.enter_fallback_available)

    @property
    def verdict(self) -> str:
        if not self.composer_found:
            return "fail"
        if self.send_control_found:
            return "pass"
        if self.enter_fallback_available:
            return "enter"
        return "fail"

    def to_json_record(self) -> dict[str, Any]:
        return asdict(self) | {"passed": self.passed, "verdict": self.verdict}


@dataclass(slots=True)
class PromptResult:
    ts: str
    index: int
    prompt: str
    status: str
    duration_s: float
    response_text: str
    tail_fallback: bool = False
    error: str | None = None
    output_path: str = ""
    attempt: int = 1

    @property
    def response_excerpt(self) -> str:
        text = " ".join(self.response_text.split())
        if len(text) <= 180:
            return text
        return f"{text[:177]}..."

    def to_json_record(self) -> dict[str, Any]:
        payload = asdict(self)
        prompt = str(payload.pop("prompt", "") or "")
        compact_prompt = " ".join(prompt.split())
        if len(compact_prompt) > 500:
            compact_prompt = f"{compact_prompt[:497]}..."
        payload["prompt_excerpt"] = compact_prompt
        payload["prompt_chars"] = len(prompt)
        payload["response_chars"] = len(self.response_text or "")
        return payload


@dataclass(slots=True)
class CommandDecision:
    action: DecisionAction
    command: str = ""
    script_lines: list[str] = field(default_factory=list)
    risk: RiskLevel = RiskLevel.READ_ONLY
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["action"] = self.action.value
        payload["risk"] = self.risk.value
        return payload


@dataclass(slots=True)
class RiskAssessment:
    risk: RiskLevel
    allowed_shape: bool
    reason: str

    def to_json_record(self) -> dict[str, Any]:
        return {"risk": self.risk.value, "allowed_shape": self.allowed_shape, "reason": self.reason}


@dataclass(slots=True)
class GitState:
    is_git_repo: bool
    workspace: str
    git_root: str = ""
    branch: str = ""
    status_short: str = ""
    diff_stat: str = ""
    diff_name_status: str = ""
    staged_name_status: str = ""
    error: str = ""

    @property
    def dirty(self) -> bool:
        return any(line.strip() and not line.startswith("##") for line in self.status_short.splitlines())

    def to_json_record(self) -> dict[str, Any]:
        return asdict(self) | {"dirty": self.dirty}


@dataclass(slots=True)
class CommandResult:
    command: str
    cwd: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    timed_out: bool
    approved: bool
    declared_risk: RiskLevel
    computed_risk: RiskLevel
    risk_reason: str
    skipped: bool = False
    skip_reason: str = ""
    shell: str = ShellKind.BASH.value

    @property
    def ok(self) -> bool:
        return not self.skipped and not self.timed_out and self.exit_code == 0

    def to_json_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["declared_risk"] = self.declared_risk.value
        payload["computed_risk"] = self.computed_risk.value
        payload["ok"] = self.ok
        return payload


@dataclass(slots=True)
class TurnRecord:
    turn: int
    ts: str
    task: str
    decision: dict[str, Any]
    git_before: dict[str, Any]
    approval_mode: str = ApprovalMode.ASK.value
    git_after: dict[str, Any] | None = None
    command_result: dict[str, Any] | None = None
    copilot_result: dict[str, Any] | None = None
    approval_required: bool = False
    approval_id: str = ""
    run_memory: str = ""
    done: bool = False
    error: str = ""

    def to_json_record(self) -> dict[str, Any]:
        return asdict(self)
