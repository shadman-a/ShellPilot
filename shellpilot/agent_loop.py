from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .copilot_connector import CopilotWorker
from .decision_parser import DecisionParseError, decision_prompt, parse_decision
from .git_state import collect_git_state
from .models import ApprovalMode, CommandDecision, DecisionAction, RiskLevel, RunConfig, ShellKind, TurnRecord
from .risk import classify_command, classify_script_lines
from .run_memory import build_run_memory
from .shell_runner import ShellRunner, skipped_result
from .storage import OutputPaths, append_turn, save_script
from .utils import EventLogger, make_excerpt, now_iso, trim_text


EventCallback = Callable[[str, dict[str, Any]], None]
ApprovalCallback = Callable[[str, CommandDecision, dict[str, Any], dict[str, Any]], bool]


@dataclass(slots=True)
class StuckSignalState:
    last_values: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    pending_refresh_reason: str = ""


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
        shell_kind: ShellKind = ShellKind.BASH,
        command_timeout_s: int = 120,
        max_turns: int = 100,
    ) -> None:
        self.copilot = copilot
        self.output_paths = output_paths
        self.event_callback = event_callback
        self.approval_callback = approval_callback
        self.approval_mode = approval_mode
        self.shell_kind = shell_kind
        self.command_timeout_s = max(1, int(command_timeout_s))
        self.max_turns = max(1, int(max_turns))
        self.runner = ShellRunner(shell_kind)

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
        run_memory = ""
        turn_records: list[dict[str, Any]] = []
        stuck_state = StuckSignalState()
        previous_decision_key = ""
        consecutive_decision_errors = 0
        max_decision_errors = 3

        try:
            for turn in range(1, self.max_turns + 1):
                if stop_event.is_set():
                    self._emit("stopped", {"reason": "Stop requested."})
                    return

                refresh_every = max(0, int(run_config.chat_refresh_turns))
                refresh_reason = ""
                if stuck_state.pending_refresh_reason:
                    refresh_reason = stuck_state.pending_refresh_reason
                elif refresh_every and turn > 1 and (turn - 1) % refresh_every == 0:
                    refresh_reason = f"scheduled_every_{refresh_every}_turns"
                if refresh_reason:
                    refreshed = self._refresh_chat(
                        turn=turn,
                        total=self.max_turns,
                        run_config=run_config,
                        reason=refresh_reason,
                    )
                    if refreshed and refresh_reason.startswith("stuck:"):
                        consecutive_decision_errors = 0
                        stuck_state = StuckSignalState()

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
                    shell=self.shell_kind,
                    run_memory=run_memory,
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
                    run_memory = self._finalize_record_memory(
                        record=record,
                        turn_records=turn_records,
                        run_config=run_config,
                    )
                    self._emit("turn_error", record.to_json_record())
                    signal = _prompt_failure_stuck_signal(prompt_result.error or "")
                    if signal:
                        self._note_stuck_signal(
                            state=stuck_state,
                            turn=turn,
                            signal=signal[0],
                            value=signal[1],
                            threshold=run_config.stuck_recovery_threshold,
                        )
                        previous_result = {
                            "status": "prompt_error",
                            "error": make_excerpt(prompt_result.error or "Copilot turn failed.", 500),
                            "hint": "If this repeats, ShellPilot will start a fresh Copilot chat with run memory.",
                        }
                        continue
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
                    run_memory = self._finalize_record_memory(
                        record=record,
                        turn_records=turn_records,
                        run_config=run_config,
                    )
                    previous_result = {
                        "status": "decision_error",
                        "error": str(exc),
                        "hint": "Return one valid JSON object. Avoid nested double quotes and multiline command strings.",
                    }
                    self._emit("turn_error", record.to_json_record())
                    self._note_stuck_signal(
                        state=stuck_state,
                        turn=turn,
                        signal="invalid_json",
                        value="Copilot response was not a valid JSON decision.",
                        threshold=run_config.stuck_recovery_threshold,
                    )
                    if consecutive_decision_errors >= max_decision_errors:
                        self._emit(
                            "run_error",
                            {"error": f"Stopped after {consecutive_decision_errors} consecutive invalid Copilot JSON responses."},
                        )
                        return
                    continue

                consecutive_decision_errors = 0
                if bool(decision.raw.get("repaired")):
                    self._emit(
                        "decision_repaired",
                        {
                            "turn": turn,
                            "decision": decision.to_json_record(),
                            "source": decision.raw.get("repair_source") or "",
                        },
                    )
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
                    run_memory = self._finalize_record_memory(
                        record=record,
                        turn_records=turn_records,
                        run_config=run_config,
                    )
                    self._emit("done", record.to_json_record())
                    return

                is_script = decision.action == DecisionAction.SCRIPT
                decision_display = _decision_display(decision)
                decision_key = _decision_key(decision)
                if previous_decision_key == decision_key:
                    self._note_stuck_signal(
                        state=stuck_state,
                        turn=turn,
                        signal="repeated_decision",
                        value=decision_key,
                        threshold=run_config.stuck_recovery_threshold,
                    )
                else:
                    stuck_state.last_values["repeated_decision"] = decision_key
                    stuck_state.counts["repeated_decision"] = 1
                previous_decision_key = decision_key
                if is_script:
                    assessment = classify_script_lines(decision.script_lines, shell=self.shell_kind)
                else:
                    assessment = classify_command(decision.command, shell=self.shell_kind)
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
                        command=decision_display,
                        cwd=workspace,
                        reason=assessment.reason,
                        declared_risk=decision.risk,
                        computed_risk=assessment.risk,
                        risk_reason=assessment.reason,
                        shell=self.shell_kind,
                    )
                elif inspect_blocked:
                    command_result = skipped_result(
                        command=decision_display,
                        cwd=workspace,
                        reason="Write commands require a successful read-only inspection first.",
                        declared_risk=decision.risk,
                        computed_risk=assessment.risk,
                        risk_reason=assessment.reason,
                        shell=self.shell_kind,
                    )
                elif approval_required and not approved:
                    command_result = skipped_result(
                        command=decision_display,
                        cwd=workspace,
                        reason="Command denied.",
                        declared_risk=decision.risk,
                        computed_risk=assessment.risk,
                        risk_reason=assessment.reason,
                        shell=self.shell_kind,
                    )
                elif is_script:
                    script_path = save_script(
                        self.output_paths,
                        turn=turn,
                        shell_kind=self.shell_kind.value,
                        script_lines=decision.script_lines,
                    )
                    self._emit(
                        "script_saved",
                        {
                            "turn": turn,
                            "path": str(script_path),
                            "line_count": len(decision.script_lines),
                        },
                    )
                    command_result = self.runner.run_script(
                        script_path=script_path,
                        cwd=workspace,
                        timeout_s=self.command_timeout_s,
                        approved=approved,
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
                if command_result.skipped:
                    self._note_stuck_signal(
                        state=stuck_state,
                        turn=turn,
                        signal="same_skipped_command",
                        value=f"{decision_display}\n{command_result.skip_reason}",
                        threshold=run_config.stuck_recovery_threshold,
                    )

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
                run_memory = self._finalize_record_memory(
                    record=record,
                    turn_records=turn_records,
                    run_config=run_config,
                )
                self._emit("turn_result", record.to_json_record())

                previous_result = self._result_context(record)

            self._emit("max_turns", {"max_turns": self.max_turns})
        finally:
            try:
                self.copilot.call("set_event_logger", None)
            except Exception:
                pass

    def _refresh_chat(self, *, turn: int, total: int, run_config: RunConfig, reason: str) -> bool:
        self._emit("step", {"step": f"Refreshing chat ({turn}/{total})", "turn": turn, "reason": reason})
        try:
            refresh_result = self.copilot.call(
                "start_new_chat",
                run_config.copilot_url,
                run_config.user_data_dir,
            )
            self._emit("chat_refreshed", {"turn": turn, "reason": reason, "result": refresh_result})
            return True
        except Exception as exc:  # noqa: BLE001
            self._emit("chat_refresh_failed", {"turn": turn, "reason": reason, "error": str(exc)})
            return False

    def _finalize_record_memory(
        self,
        *,
        record: TurnRecord,
        turn_records: list[dict[str, Any]],
        run_config: RunConfig,
    ) -> str:
        candidate_record = record.to_json_record()
        run_memory = build_run_memory(
            [*turn_records, candidate_record],
            max_chars=run_config.run_memory_chars,
            max_bullets=6,
        )
        record.run_memory = run_memory
        append_turn(self.output_paths, record)
        turn_records.append(record.to_json_record())
        if run_memory:
            self._emit(
                "run_memory_updated",
                {
                    "turn": record.turn,
                    "chars": len(run_memory),
                    "bullets": sum(1 for line in run_memory.splitlines() if line.strip().startswith("- ")),
                    "excerpt": make_excerpt(run_memory, 180),
                },
            )
        return run_memory

    def _note_stuck_signal(
        self,
        *,
        state: StuckSignalState,
        turn: int,
        signal: str,
        value: str,
        threshold: int,
    ) -> None:
        value_excerpt = make_excerpt(value, 240)
        if state.last_values.get(signal) == value_excerpt:
            count = state.counts.get(signal, 0) + 1
        else:
            count = 1
        state.last_values[signal] = value_excerpt
        state.counts[signal] = count
        threshold = max(1, int(threshold))
        recovery_planned = count >= threshold
        if recovery_planned:
            state.pending_refresh_reason = f"stuck:{signal}"
        self._emit(
            "stuck_signal_detected",
            {
                "turn": turn,
                "signal": signal,
                "count": count,
                "threshold": threshold,
                "recovery_planned": recovery_planned,
                "value_excerpt": value_excerpt,
            },
        )

    def _result_context(self, record: TurnRecord) -> dict[str, Any]:
        result = dict(record.command_result or {})
        result["stdout"] = trim_text(str(result.get("stdout") or ""), 900)
        result["stderr"] = trim_text(str(result.get("stderr") or ""), 600)
        return {
            "turn": record.turn,
            "decision": record.decision,
            "command_result": result,
        }

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        self.event_callback(event, payload)


def _decision_display(decision: CommandDecision) -> str:
    if decision.action == DecisionAction.SCRIPT:
        return "\n".join(decision.script_lines)
    return decision.command


def _decision_key(decision: CommandDecision) -> str:
    return f"{decision.action.value}:{_decision_display(decision).strip()}"


def _prompt_failure_stuck_signal(error: str) -> tuple[str, str] | None:
    lowered = str(error or "").lower()
    if "no assistant response activity" in lowered:
        return "no_assistant_activity", "prompt sent but no assistant response activity was detected"
    return None
