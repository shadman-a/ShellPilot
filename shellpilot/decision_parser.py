from __future__ import annotations

import json
import re
from typing import Any

from .models import CommandDecision, DecisionAction, RiskLevel


class DecisionParseError(ValueError):
    pass


def parse_decision(text: str) -> CommandDecision:
    blob = _extract_json_object(text)
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise DecisionParseError(f"Copilot did not return valid JSON: {exc.msg}") from exc
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


def _extract_json_object(text: str) -> str:
    normalized = _normalize_jsonish_text(text or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", normalized, re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = normalized.find("{")
    if start < 0:
        raise DecisionParseError("Copilot response did not contain a JSON object.")

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(normalized)):
        char = normalized[idx]
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
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return normalized[start : idx + 1].strip()

    raise DecisionParseError("Copilot response contained an incomplete JSON object.")


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
) -> str:
    previous_json = json.dumps(previous_result or {"status": "none"}, ensure_ascii=False, indent=2)
    git_json = json.dumps(git_state, ensure_ascii=False, indent=2)
    return f"""
You are ShellPilot, a single-agent local command cockpit.

You are working in this workspace:
{workspace}

User task:
{task}

Current Git state:
{git_json}

Previous command result:
{previous_json}

Rules:
- Return exactly one JSON object and nothing else.
- Choose exactly one Bash command for the next turn, or return done.
- Do not return a plan, markdown, code fences, commentary, or multiple commands.
- Inspect before edit. Prefer read-only commands until you know the repo shape.
- Do not assume repo structure.
- Use Git as the source of truth for status, diffs, and audit trail.
- The local app will risk-check and approval-gate all non-read-only commands.
- Avoid destructive, network, package install, process killing, and system-level commands.
- One command only. No newlines, semicolons, &&, or ||. Pipelines with | are allowed.

Valid command JSON:
{{"action":"command","command":"pwd","risk":"read_only","reason":"Check the current directory first."}}

Valid done JSON:
{{"action":"done","reason":"Task complete."}}

Turn: {turn}
""".strip()

