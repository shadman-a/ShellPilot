from __future__ import annotations

import re
import shlex
from typing import Any

from .utils import make_excerpt


def build_run_memory(turns: list[dict[str, Any]], *, max_chars: int = 1000, max_bullets: int = 6) -> str:
    if not turns:
        return ""

    char_limit = max(0, int(max_chars))
    bullet_limit = max(1, int(max_bullets))
    if char_limit <= 0:
        return ""

    bullets: list[str] = []
    latest = turns[-1]
    bullets.append(_progress_bullet(turns, latest))

    last_success = _last_success(turns)
    if last_success:
        bullets.append(_success_bullet(last_success))

    changed_paths = _git_changed_paths(turns)
    if changed_paths:
        bullets.append(f"- Git changes seen: {_format_items(changed_paths, 5)}.")

    referenced_paths = _referenced_paths(turns)
    if referenced_paths:
        bullets.append(f"- Paths referenced by executed decisions: {_format_items(referenced_paths, 5)}.")

    issues = _recent_issues(turns)
    if issues:
        bullets.append(f"- Recent issues: {'; '.join(issues)}.")

    status = _task_status(turns)
    if status:
        bullets.append(status)

    return _fit_bullets(bullets[:bullet_limit], char_limit)


def _progress_bullet(turns: list[dict[str, Any]], latest: dict[str, Any]) -> str:
    status = "active"
    if latest.get("done"):
        status = "done"
    elif latest.get("error"):
        status = "error"
    elif (latest.get("command_result") or {}).get("skipped"):
        status = "skipped"
    elif latest.get("command_result"):
        status = "ok" if (latest.get("command_result") or {}).get("ok") else "failed"
    return f"- Progress: {len(turns)} turn(s) recorded; latest turn {latest.get('turn') or len(turns)} status is {status}."


def _last_success(turns: list[dict[str, Any]]) -> dict[str, Any]:
    for turn in reversed(turns):
        result = turn.get("command_result")
        if isinstance(result, dict) and result.get("ok"):
            return turn
    return {}


def _success_bullet(turn: dict[str, Any]) -> str:
    result = turn.get("command_result") or {}
    command = _decision_text(turn.get("decision") or {}) or str(result.get("command") or "")
    stdout = make_excerpt(str(result.get("stdout") or ""), 140)
    if stdout:
        return f"- Last success: turn {turn.get('turn')}: `{make_excerpt(command, 120)}`; stdout: {stdout}."
    return f"- Last success: turn {turn.get('turn')}: `{make_excerpt(command, 150)}` exited 0."


def _git_changed_paths(turns: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for turn in turns:
        for key in ("git_before", "git_after"):
            git_state = turn.get(key)
            if not isinstance(git_state, dict):
                continue
            for line in str(git_state.get("status_short") or "").splitlines():
                path = _path_from_git_status_line(line)
                if path:
                    paths.append(path)
    return _dedupe(paths)


def _path_from_git_status_line(line: str) -> str:
    text = line.strip()
    if not text or text.startswith("##"):
        return ""
    if text.startswith("??"):
        return text[2:].strip()
    path = text[2:].strip() if len(text) > 2 else ""
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[-1].strip()
    return path


def _referenced_paths(turns: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for turn in turns:
        decision = turn.get("decision")
        result = turn.get("command_result")
        if not isinstance(decision, dict) or not isinstance(result, dict) or not result.get("command"):
            continue
        for command in _decision_commands(decision):
            paths.extend(_path_tokens(command))
    return _dedupe(paths)


def _decision_commands(decision: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    command = str(decision.get("command") or "").strip()
    if command:
        commands.append(command)
    script_lines = decision.get("script_lines")
    if isinstance(script_lines, list):
        commands.extend(str(line).strip() for line in script_lines if str(line).strip())
    return commands


def _decision_text(decision: dict[str, Any]) -> str:
    commands = _decision_commands(decision)
    return " | ".join(commands)


def _path_tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    candidates: list[str] = []
    for token in tokens[1:]:
        cleaned = token.strip().strip(",;")
        if not cleaned or cleaned.startswith("-") or "://" in cleaned:
            continue
        if cleaned in {".", "..", "|"}:
            continue
        basename = re.split(r"[/\\]", cleaned)[-1]
        if "/" in cleaned or "\\" in cleaned or "." in basename:
            candidates.append(cleaned)
    return candidates


def _recent_issues(turns: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for turn in reversed(turns):
        turn_id = turn.get("turn")
        error = str(turn.get("error") or "").strip()
        if error:
            issues.append(f"turn {turn_id}: {make_excerpt(error, 100)}")
            continue
        result = turn.get("command_result")
        if not isinstance(result, dict):
            continue
        if result.get("skipped"):
            reason = str(result.get("skip_reason") or result.get("risk_reason") or "skipped")
            issues.append(f"turn {turn_id}: skipped ({make_excerpt(reason, 90)})")
        elif result.get("timed_out"):
            issues.append(f"turn {turn_id}: timed out")
        elif result.get("exit_code") not in (None, 0):
            stderr = str(result.get("stderr") or "").strip()
            detail = make_excerpt(stderr, 90) if stderr else f"exit {result.get('exit_code')}"
            issues.append(f"turn {turn_id}: failed ({detail})")
        if len(issues) >= 2:
            break
    return list(reversed(issues))


def _task_status(turns: list[dict[str, Any]]) -> str:
    latest = turns[-1]
    if latest.get("done"):
        return f"- Current task status: Copilot marked the task complete on turn {latest.get('turn')}."
    return f"- Current task status: task still active after turn {latest.get('turn') or len(turns)}."


def _format_items(items: list[str], limit: int) -> str:
    shown = items[:limit]
    suffix = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(make_excerpt(item, 80) for item in shown) + suffix


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _fit_bullets(bullets: list[str], max_chars: int) -> str:
    fitted: list[str] = []
    for bullet in bullets:
        candidate = "\n".join([*fitted, bullet]) if fitted else bullet
        if len(candidate) <= max_chars:
            fitted.append(bullet)
            continue
        remaining = max_chars - (len("\n".join(fitted)) + (1 if fitted else 0))
        if remaining > 24:
            fitted.append(make_excerpt(bullet, remaining))
        break
    return "\n".join(fitted)[:max_chars].rstrip()
