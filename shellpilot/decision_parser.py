from __future__ import annotations

import json
import re
from typing import Any

from .models import CommandDecision, DecisionAction, RiskLevel, ShellKind


class DecisionParseError(ValueError):
    pass


def parse_decision(text: str) -> CommandDecision:
    blob, payload = _extract_latest_json_payload(text)
    if not isinstance(payload, dict):
        raise DecisionParseError("Copilot JSON decision must be an object.")

    action_text = str(payload.get("action") or "").strip().lower()
    try:
        action = DecisionAction(action_text)
    except ValueError as exc:
        raise DecisionParseError("Decision action must be 'command' or 'done'.") from exc

    reason = str(payload.get("reason") or "").strip()
    if action == DecisionAction.DONE:
        return CommandDecision(action=action, reason=reason or "Done.", raw=payload)

    command = str(payload.get("command") or "").strip()
    if not command:
        raise DecisionParseError("Command decision must include a non-empty command.")

    risk_text = str(payload.get("risk") or RiskLevel.DANGEROUS.value).strip().lower()
    try:
        risk = RiskLevel(risk_text)
    except ValueError:
        risk = RiskLevel.DANGEROUS

    return CommandDecision(
        action=action,
        command=command,
        risk=risk,
        reason=reason or "No reason supplied.",
        raw=payload,
    )


def _extract_latest_json_payload(text: str) -> tuple[str, dict[str, Any]]:
    candidates = _extract_json_objects(text)
    normalized = _normalize_jsonish_text(text or "")
    if not candidates and '"action"' in normalized:
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start >= 0 and end > start:
            candidates = [normalized[start : end + 1]]
    if not candidates:
        raise DecisionParseError("Copilot response did not contain a JSON object.")

    last_error: json.JSONDecodeError | None = None
    for blob in reversed(candidates):
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError as exc:
            last_error = exc
            payload = _parse_jsonish_decision_object(blob)
            if payload is None:
                continue
        if isinstance(payload, dict):
            return blob, payload

    if last_error is not None:
        raise DecisionParseError(f"Copilot did not return valid JSON: {last_error.msg}") from last_error
    raise DecisionParseError("Copilot JSON decision must be an object.")


def _extract_json_objects(text: str) -> list[str]:
    normalized = _normalize_jsonish_text(text or "")
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", normalized, re.IGNORECASE | re.DOTALL)
    if fenced:
        return [item.strip() for item in fenced if item.strip()]

    objects: list[str] = []
    depth = 0
    in_string = False
    escape = False
    start = -1

    for idx, char in enumerate(normalized):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(normalized[start : idx + 1].strip())
                start = -1

    return objects


def _parse_jsonish_decision_object(blob: str) -> dict[str, Any] | None:
    action = _extract_jsonish_field(blob, "action")
    command = _extract_jsonish_field(blob, "command", next_field="risk")
    risk = _extract_jsonish_field(blob, "risk")
    reason = _extract_jsonish_field(blob, "reason")
    if not action:
        return None
    payload: dict[str, Any] = {"action": action}
    if command is not None:
        payload["command"] = command
    if risk is not None:
        payload["risk"] = risk
    if reason is not None:
        payload["reason"] = reason
    return payload


def _extract_jsonish_field(blob: str, field: str, *, next_field: str | None = None) -> str | None:
    if next_field:
        pattern = rf'"{re.escape(field)}"\s*:\s*"(.*)"\s*,\s*"{re.escape(next_field)}"\s*:'
        match = re.search(pattern, blob, re.DOTALL)
        if match:
            return match.group(1).strip()

    pattern = rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, blob, re.DOTALL)
    if not match:
        return None
    value = match.group(1)
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def _normalize_jsonish_text(text: str) -> str:
    replacements: dict[str, str] = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.strip()


def decision_prompt(
    *,
    task: str,
    workspace: str,
    git_state: dict[str, Any],
    previous_result: dict[str, Any] | None,
    turn: int,
    shell: ShellKind | str = ShellKind.BASH,
) -> str:
    previous_json = json.dumps(_compact_previous_result(previous_result), ensure_ascii=False, separators=(",", ":"))
    git_json = json.dumps(_compact_git_state(git_state), ensure_ascii=False, separators=(",", ":"))
    shell_kind = ShellKind(shell)
    shell_rules = _shell_rules(shell_kind)
    return f"""
You are ShellPilot. Return one local shell decision as strict JSON.

Workspace:
{workspace}

Shell:
{shell_rules["name"]}

Task:
{task}

Git:
{git_json}

Previous result:
{previous_json}

Rules:
- Return exactly one JSON object and nothing else.
- Choose exactly one {shell_rules["name"]} command for the next turn, or return done.
- No markdown, code fences, commentary, plans, or multiple commands.
- Escape double quotes inside the JSON command string.
- Prefer simple commands that avoid nested double quotes.
{shell_rules["guidance"]}
- Treat every turn as self-contained. Use this prompt, Git state, and previous result.
- If the previous command failed or was skipped, adapt from that result instead of repeating it unchanged.
- Inspect before edit. Prefer read-only commands until you know the repo shape.
- Do not assume repo structure.
- Use Git as the source of truth for status, diffs, and audit trail.
- The local app will risk-check and approval-gate all non-read-only commands.
- Avoid destructive, package install, process killing, and system-level commands.
- One shell command only. No unquoted newlines, unquoted semicolons, &, &&, or ||. Pipes are allowed.
- Semicolons inside a quoted `python3 -c '...'` program are allowed.
- Return done when the task is complete or no safe single next command remains.

Valid command JSON:
{shell_rules["example"]}

Valid done JSON:
{{"action":"done","reason":"Task complete."}}

Turn: {turn}
""".strip()


def _shell_rules(shell: ShellKind) -> dict[str, str]:
    if shell == ShellKind.POWERSHELL:
        return {
            "name": "PowerShell",
            "guidance": (
                "- Prefer PowerShell inspection commands such as `Get-Location`, `Get-ChildItem`, "
                "`Get-Content`, `Select-String`, and `git status --short`.\n"
                "- For CSV transformations, prefer PowerShell cmdlets when simple. If Python is needed, "
                "use `python -c \"...\"` and keep it one line."
            ),
            "example": (
                '{"action":"command","command":"Get-Location","risk":"read_only",'
                '"reason":"Check the current directory first."}'
            ),
        }
    if shell == ShellKind.CMD:
        return {
            "name": "Windows cmd.exe",
            "guidance": (
                "- Prefer cmd inspection commands such as `cd`, `dir`, `type`, `findstr`, and `git status --short`.\n"
                "- For CSV transformations, prefer `python -c \"...\"` and keep it one line."
            ),
            "example": (
                '{"action":"command","command":"cd","risk":"read_only",'
                '"reason":"Check the current directory first."}'
            ),
        }
    return {
        "name": "Bash",
        "guidance": (
            "- For CSV transformations, prefer one `python3 -c '...'` command with the whole Python program "
            "inside one single-quoted shell argument.\n"
            "- In `python3 -c '...'`, use semicolons inside that quoted Python argument. Do not use "
            '`exec("...")`, literal `\\n` escapes, or multiline Python.'
        ),
        "example": (
            '{"action":"command","command":"pwd","risk":"read_only",'
            '"reason":"Check the current directory first."}'
        ),
    }


def _compact_git_state(git_state: dict[str, Any]) -> dict[str, Any]:
    status_lines = _filtered_lines(str(git_state.get("status_short") or ""))
    diff_stat_lines = _filtered_lines(str(git_state.get("diff_stat") or ""))
    diff_name_lines = _filtered_lines(str(git_state.get("diff_name_status") or ""))
    staged_lines = _filtered_lines(str(git_state.get("staged_name_status") or ""))

    return {
        "is_git_repo": bool(git_state.get("is_git_repo")),
        "workspace": git_state.get("workspace") or "",
        "git_root": git_state.get("git_root") or "",
        "branch": git_state.get("branch") or "",
        "dirty": bool(git_state.get("dirty")),
        "status_counts": _status_counts(status_lines),
        "status_preview": _bounded_lines(status_lines, 12),
        "status_omitted": max(0, len(status_lines) - 12),
        "diff_stat_preview": _bounded_lines(diff_stat_lines, 6),
        "diff_stat_omitted": max(0, len(diff_stat_lines) - 6),
        "diff_name_status_preview": _bounded_lines(diff_name_lines, 10),
        "diff_name_status_omitted": max(0, len(diff_name_lines) - 10),
        "staged_name_status_preview": _bounded_lines(staged_lines, 10),
        "staged_name_status_omitted": max(0, len(staged_lines) - 10),
        "error": git_state.get("error") or "",
    }


def _compact_previous_result(previous_result: dict[str, Any] | None) -> dict[str, Any]:
    if not previous_result:
        return {"status": "none"}

    command_result = previous_result.get("command_result")
    decision = previous_result.get("decision")
    if not isinstance(command_result, dict):
        return _compact_generic_previous_result(previous_result)

    previous_command = str(command_result.get("command") or "")
    if not previous_command and isinstance(decision, dict):
        previous_command = str(decision.get("command") or "")

    compact: dict[str, Any] = {
        "turn": previous_result.get("turn"),
        "command": previous_command,
        "ok": bool(command_result.get("ok")),
        "exit_code": command_result.get("exit_code"),
        "timed_out": bool(command_result.get("timed_out")),
        "skipped": bool(command_result.get("skipped")),
        "risk": command_result.get("computed_risk") or command_result.get("declared_risk") or "",
    }
    skip_reason = str(command_result.get("skip_reason") or "")
    stderr = str(command_result.get("stderr") or "")
    stdout = str(command_result.get("stdout") or "")
    if skip_reason:
        compact["skip_reason"] = _trim_inline(skip_reason, 240)
    if stderr:
        compact["stderr"] = _trim_inline(stderr, 600)
    if stdout:
        compact["stdout"] = _trim_inline(stdout, 900)
    return {key: value for key, value in compact.items() if value not in ("", None)}


def _compact_generic_previous_result(previous_result: dict[str, Any]) -> dict[str, Any]:
    compact = dict(previous_result)
    if "git_after" in compact and isinstance(compact["git_after"], dict):
        compact["git_after"] = _compact_git_state(compact["git_after"])
    text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= 1800:
        return compact
    return {"summary": _trim_inline(text, 1800)}


def _trim_inline(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 32
    return f"{text[:head]}\n...[trimmed]...\n{text[-tail:]}"


def _filtered_lines(text: str) -> list[str]:
    noisy_paths = (
        ".shellpilot/",
        ".playwright-cli/",
        "Library/",
    )
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return [line for line in lines if not any(path in line for path in noisy_paths)]


def _bounded_lines(lines: list[str], limit: int) -> list[str]:
    return lines[:limit]


def _status_counts(lines: list[str]) -> dict[str, int]:
    counts = {"modified": 0, "added": 0, "deleted": 0, "renamed": 0, "untracked": 0, "other": 0}
    for line in lines:
        if line.startswith("##"):
            continue
        status = line[:2]
        if status == "??":
            counts["untracked"] += 1
        elif "R" in status:
            counts["renamed"] += 1
        elif "D" in status:
            counts["deleted"] += 1
        elif "A" in status:
            counts["added"] += 1
        elif "M" in status:
            counts["modified"] += 1
        else:
            counts["other"] += 1
    return counts
