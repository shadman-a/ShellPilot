from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .bash_runner import BashRunner, skipped_result
from .copilot_connector import CopilotWorker
from .decision_parser import DecisionParseError, decision_prompt, parse_decision
from .git_state import collect_git_state
from .models import ApprovalMode, CommandDecision, DecisionAction, RiskLevel, RunConfig, TurnRecord
from .risk import classify_command
from .storage import OutputPaths, append_turn
from .utils import EventLogger, make_excerpt, now_iso, trim_text


EventCallback = Callable[[str, dict[str, Any]], None]
ApprovalCallback = Callable[[str, CommandDecision, dict[str, Any], dict[str, Any]], bool]


def approval_required_for_risk(mode: ApprovalMode, risk: RiskLevel) -> bool:
    if mode == ApprovalMode.FULL_ACCESS:
        return False
    if mode == ApprovalMode.APPROVE_FOR_ME:
        return risk == RiskLevel.DANGEROUS
    return risk != RiskLevel.READ_ONLY


class ShellPilotLoop:
    def __init__(
        self,
        *,
        copilot: CopilotWorker,
        output_paths: OutputPaths,
        event_callback: EventCallback,
        approval_callback: ApprovalCallback,
        approval_mode: ApprovalMode = ApprovalMode.ASK,
        command_timeout_s: int = 120,
        max_turns: int = 12,
    ) -> None:
        self.copilot = copilot
        self.output_paths = output_paths
        self.event_callback = event_callback
        self.approval_callback = approval_callback
        self.approval_mode = approval_mode
        self.command_timeout_s = max(1, int(command_timeout_s))
        self.max_turns = max(1, int(max_turns))
        self.runner = BashRunner()

    def run(
        self,
        *,
        task: str,
        workspace_dir: str | Path,
        run_config: RunConfig,
        stop_event: threading.Event,
    ) -> None:
        workspace = Path(workspace_dir).expanduser().resolve()
        event_logger = EventLogger(self.output_paths.events_log_path, lambda line: self.event_callback("log", {"line": line}))
        self.copilot.call("set_event_logger", event_logger)
        inspected_ok = False
        previous_result: dict[str, Any] | None = None
        consecutive_decision_errors = 0
        max_decision_errors = 3

        try:
            for turn in range(1, self.max_turns + 1):
                if stop_event.is_set():
                    self._emit("stopped", {"reason": "Stop requested."})
                    return

                git_before = collect_git_state(workspace)
                self._emit(
                    "turn_started",
                    {"turn": turn, "git_before": git_before.to_json_record()},
                )

                prompt = decision_prompt(
                    task=task,
                    workspace=str(workspace),
                    git_state=git_before.to_json_record(),
                    previous_result=previous_result,
                    turn=turn,
                )
                prompt_result = self.copilot.call(
                    "send_turn",
                    prompt=prompt,
                    index=turn,
                    total=self.max_turns,
                    config=run_config,
                    output_paths=self.output_paths,
                    stop_event=stop_event,
                    step_callback=lambda step: self._emit("step", {"step": step, "turn": turn}),
                )
                self._emit(
                    "copilot_response",
                    {
                        "turn": turn,
                        "status": prompt_result.status,
                        "response_excerpt": make_excerpt(prompt_result.response_text or prompt_result.error or "", 260),
                        "output_path": prompt_result.output_path,
                    },
                )

                if prompt_result.status != "success":
                    record = TurnRecord(
                        turn=turn,
                        ts=now_iso(),
                        task=task,
                        decision={},
                        git_before=git_before.to_json_record(),
                        approval_mode=self.approval_mode.value,
                        copilot_result=prompt_result.to_json_record(),
                        error=prompt_result.error or "Copilot turn failed.",
                    )
                    append_turn(self.output_paths, record)
                    self._emit("turn_error", record.to_json_record())
                    return

                try:
                    decision = parse_decision(prompt_result.response_text)
                except DecisionParseError as exc:
                    consecutive_decision_errors += 1
                    record = TurnRecord(
                        turn=turn,
                        ts=now_iso(),
                        task=task,
                        decision={},
                        git_before=git_before.to_json_record(),
                        approval_mode=self.approval_mode.value,
                        copilot_result=prompt_result.to_json_record(),
                        error=str(exc),
                    )
                    append_turn(self.output_paths, record)
                    previous_result = {
                        "status": "decision_error",
                        "error": str(exc),
                        "hint": "Return one valid JSON object. Avoid nested double quotes and multiline command strings.",
                    }
                    self._emit("turn_error", record.to_json_record())
                    if consecutive_decision_errors >= max_decision_errors:
                        self._emit(
                            "run_error",
                            {"error": f"Stopped after {consecutive_decision_errors} consecutive invalid Copilot JSON responses."},
                        )
                        return
                    continue

                consecutive_decision_errors = 0
                if decision.action == DecisionAction.DONE:
                    record = TurnRecord(
                        turn=turn,
                        ts=now_iso(),
                        task=task,
                        decision=decision.to_json_record(),
                        git_before=git_before.to_json_record(),
                        approval_mode=self.approval_mode.value,
                        copilot_result=prompt_result.to_json_record(),
                        done=True,
                    )
                    append_turn(self.output_paths, record)
                    self._emit("done", record.to_json_record())
                    return

                assessment = classify_command(decision.command)
                approval_required = approval_required_for_risk(self.approval_mode, assessment.risk)
                inspect_blocked = (
                    assessment.risk == RiskLevel.WRITE_FILE
                    and not inspected_ok
                    and self.approval_mode != ApprovalMode.FULL_ACCESS
                )
                approval_id = ""
                approved = not approval_required and not inspect_blocked
                if approval_required and not inspect_blocked:
                    approval_id = f"approval-{turn}-{uuid.uuid4().hex[:8]}"
                    approved = self.approval_callback(
                        approval_id,
                        decision,
                        assessment.to_json_record(),
                        git_before.to_json_record(),
                    )

                if not assessment.allowed_shape:
                    command_result = skipped_result(
                        command=decision.command,
                        cwd=workspace,
                        reason=assessment.reason,
                        declared_risk=decision.risk,
                        computed_risk=assessment.risk,
                        risk_reason=assessment.reason,
                    )
                elif inspect_blocked:
                    command_result = skipped_result(
                        command=decision.command,
                        cwd=workspace,
                        reason="Write commands require a successful read-only inspection first.",
                        declared_risk=decision.risk,
                        computed_risk=assessment.risk,
                        risk_reason=assessment.reason,
                    )
                elif approval_required and not approved:
                    command_result = skipped_result(
                        command=decision.command,
                        cwd=workspace,
                        reason="Command denied.",
                        declared_risk=decision.risk,
                        computed_risk=assessment.risk,
                        risk_reason=assessment.reason,
                    )
                else:
                    command_result = self.runner.run(
                        command=decision.command,
                        cwd=workspace,
                        timeout_s=self.command_timeout_s,
                        approved=approved,
                        declared_risk=decision.risk,
                        computed_risk=assessment.risk,
                        risk_reason=assessment.reason,
                    )

                git_after = collect_git_state(workspace)
                if command_result.ok and assessment.risk.value == "read_only":
                    inspected_ok = True

                record = TurnRecord(
                    turn=turn,
                    ts=now_iso(),
                    task=task,
                    decision=decision.to_json_record(),
                    git_before=git_before.to_json_record(),
                    approval_mode=self.approval_mode.value,
                    git_after=git_after.to_json_record(),
                    command_result=command_result.to_json_record(),
                    copilot_result=prompt_result.to_json_record(),
                    approval_required=approval_required or inspect_blocked,
                    approval_id=approval_id,
                )
                append_turn(self.output_paths, record)
                self._emit("turn_result", record.to_json_record())

                previous_result = self._result_context(record)

            self._emit("max_turns", {"max_turns": self.max_turns})
        finally:
            try:
                self.copilot.call("set_event_logger", None)
            except Exception:
                pass

    def _result_context(self, record: TurnRecord) -> dict[str, Any]:
        result = dict(record.command_result or {})
        result["stdout"] = trim_text(str(result.get("stdout") or ""), 1500)
        result["stderr"] = trim_text(str(result.get("stderr") or ""), 1000)
        return {
            "turn": record.turn,
            "decision": record.decision,
            "command_result": result,
        }

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        self.event_callback(event, payload)
