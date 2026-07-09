from __future__ import annotations

import shlex

from .models import RiskAssessment, RiskLevel, ShellKind


READ_ONLY_COMMANDS = {
    "pwd",
    "ls",
    "dir",
    "find",
    "cat",
    "type",
    "cd",
    "echo",
    "grep",
    "rg",
    "wc",
    "head",
    "tail",
    "sed",
    "sort",
    "uniq",
    "git",
    "file",
    "stat",
    "du",
    "get-location",
    "gl",
    "get-childitem",
    "get-child-item",
    "gci",
    "get-content",
    "gc",
    "select-string",
    "sls",
    "where-object",
    "measure-object",
    "findstr",
}

READ_ONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "rev-parse",
    "branch",
    "ls-files",
    "show",
    "grep",
    "remote",
}

WRITE_GIT_SUBCOMMANDS = {
    "add",
    "commit",
    "checkout",
    "switch",
    "restore",
    "merge",
    "rebase",
    "stash",
    "tag",
}

NETWORK_GIT_SUBCOMMANDS = {"fetch", "pull", "push", "clone", "submodule"}

DANGEROUS_GIT_PATTERNS = (
    "reset --hard",
    "clean -fd",
    "clean -xdf",
    "clean -ffdx",
)


def classify_command(command: str, shell: ShellKind | str = ShellKind.BASH) -> RiskAssessment:
    command = (command or "").strip()
    shell_kind = ShellKind(shell)
    if not command:
        return RiskAssessment(RiskLevel.DANGEROUS, False, "Empty command.")
    if _has_blocked_command_separator(command):
        return RiskAssessment(RiskLevel.DANGEROUS, False, "Multiple commands are not allowed.")
    if _has_write_redirection(command):
        return RiskAssessment(RiskLevel.WRITE_FILE, True, "Shell redirection writes to files.")

    lowered = command.lower()
    if any(pattern in lowered for pattern in DANGEROUS_GIT_PATTERNS):
        return RiskAssessment(RiskLevel.DANGEROUS, True, "Dangerous Git cleanup/reset command.")

    pipeline_parts = _split_unquoted_pipes(command)
    if not pipeline_parts:
        return RiskAssessment(RiskLevel.DANGEROUS, False, "No executable command found.")

    highest = RiskLevel.READ_ONLY
    reasons: list[str] = []
    for part in pipeline_parts:
        risk, reason = _classify_single(part, shell_kind)
        reasons.append(reason)
        highest = _max_risk(highest, risk)
    return RiskAssessment(highest, True, "; ".join(reasons))


def classify_script_lines(script_lines: list[str], shell: ShellKind | str = ShellKind.BASH) -> RiskAssessment:
    shell_kind = ShellKind(shell)
    if shell_kind == ShellKind.CMD:
        return RiskAssessment(RiskLevel.DANGEROUS, False, "Script decisions are only supported for Bash and PowerShell.")

    executable_lines = [
        line.strip()
        for line in script_lines
        if line.strip() and not line.strip().startswith("#")
    ]
    if not executable_lines:
        return RiskAssessment(RiskLevel.DANGEROUS, False, "Script decision did not include executable lines.")

    highest = RiskLevel.READ_ONLY
    reasons: list[str] = []
    for index, line in enumerate(executable_lines, start=1):
        assessment = classify_command(line, shell=shell_kind)
        if not assessment.allowed_shape:
            return RiskAssessment(
                RiskLevel.DANGEROUS,
                False,
                f"Script line {index} is not allowed: {assessment.reason}",
            )
        highest = _max_risk(highest, assessment.risk)
        reasons.append(f"line {index}: {assessment.reason}")

    return RiskAssessment(highest, True, "; ".join(reasons))


def requires_approval(risk: RiskLevel) -> bool:
    return risk != RiskLevel.READ_ONLY


def _has_blocked_command_separator(command: str) -> bool:
    in_single = False
    in_double = False
    escape = False
    idx = 0

    while idx < len(command):
        char = command[idx]
        if escape:
            escape = False
            idx += 1
            continue
        if char == "\\" and not in_single:
            escape = True
            idx += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            idx += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            idx += 1
            continue
        if not in_single and not in_double:
            if char in {";", "\n", "\r", "&"}:
                return True
            if command.startswith("||", idx):
                return True
        idx += 1
    return False


def _has_write_redirection(command: str) -> bool:
    in_single = False
    in_double = False
    escape = False
    for idx, char in enumerate(command):
        if escape:
            escape = False
            continue
        if char == "\\" and not in_single:
            escape = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == ">" and not in_single and not in_double:
            previous = command[idx - 1] if idx else ""
            next_char = command[idx + 1] if idx + 1 < len(command) else ""
            if previous != "<" and next_char != "&":
                return True
    return False


def _split_unquoted_pipes(command: str) -> list[str]:
    parts: list[str] = []
    start = 0
    in_single = False
    in_double = False
    escape = False

    for idx, char in enumerate(command):
        if escape:
            escape = False
            continue
        if char == "\\" and not in_single:
            escape = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "|" and not in_single and not in_double:
            part = command[start:idx].strip()
            if part:
                parts.append(part)
            start = idx + 1

    final_part = command[start:].strip()
    if final_part:
        parts.append(final_part)
    return parts


def _classify_single(command: str, shell: ShellKind) -> tuple[RiskLevel, str]:
    try:
        tokens = shlex.split(command, posix=shell == ShellKind.BASH)
    except ValueError:
        return RiskLevel.DANGEROUS, "Could not parse command safely."
    if not tokens:
        return RiskLevel.DANGEROUS, "Empty pipeline segment."

    executable = tokens[0].lower()
    args_lower = [token.lower() for token in tokens[1:]]
    joined = " ".join([executable, *args_lower])

    dangerous_executables = {
        "rm",
        "rmdir",
        "kill",
        "pkill",
        "killall",
        "sudo",
        "su",
        "doas",
        "shutdown",
        "reboot",
        "chmod",
        "chown",
        "diskutil",
        "launchctl",
        "del",
        "erase",
        "rd",
        "reg",
        "regedit",
        "taskkill",
        "sc",
        "net",
        "stop-process",
        "remove-item",
        "ri",
        "set-executionpolicy",
        "start-process",
        "powershell",
        "powershell.exe",
        "pwsh",
        "cmd",
        "cmd.exe",
    }
    if executable in dangerous_executables:
        return RiskLevel.DANGEROUS, f"{tokens[0]} is dangerous or system-level."

    network_executables = {
        "curl",
        "wget",
        "npm",
        "yarn",
        "pnpm",
        "pip",
        "pip3",
        "brew",
        "python",
        "python3",
        "invoke-webrequest",
        "invoke-restmethod",
        "iwr",
        "irm",
    }
    if executable in network_executables:
        if executable in {"python", "python3"} and ("--version" in args_lower or "-v" in args_lower):
            return RiskLevel.READ_ONLY, "Python version inspection is read-only."
        return RiskLevel.NETWORK, f"{tokens[0]} can access network or run arbitrary code."

    write_executables = {
        "touch",
        "mkdir",
        "cp",
        "mv",
        "tee",
        "install",
        "patch",
        "perl",
        "ruby",
        "copy",
        "xcopy",
        "robocopy",
        "ren",
        "rename",
        "md",
        "new-item",
        "ni",
        "set-content",
        "add-content",
        "out-file",
        "copy-item",
        "move-item",
        "rename-item",
    }
    if executable in write_executables:
        return RiskLevel.WRITE_FILE, f"{tokens[0]} can modify files."

    if executable == "sed" and any(arg.startswith("-i") for arg in args_lower):
        return RiskLevel.WRITE_FILE, "sed -i modifies files."

    if executable == "git":
        if not args_lower:
            return RiskLevel.READ_ONLY, "git with no subcommand is inspection only."
        subcommand = args_lower[0]
        if subcommand in NETWORK_GIT_SUBCOMMANDS:
            return RiskLevel.NETWORK, f"git {subcommand} uses network or changes remote state."
        if subcommand in WRITE_GIT_SUBCOMMANDS:
            return RiskLevel.WRITE_FILE, f"git {subcommand} can modify repository state."
        if subcommand == "reset" or subcommand == "clean":
            return RiskLevel.DANGEROUS, f"git {subcommand} can destroy local work."
        if subcommand in READ_ONLY_GIT_SUBCOMMANDS:
            return RiskLevel.READ_ONLY, f"git {subcommand} is read-only."
        return RiskLevel.DANGEROUS, f"git {subcommand} is not recognized as safe."

    if executable in READ_ONLY_COMMANDS:
        if executable == "find" and any(arg in {"-delete", "-exec"} for arg in args_lower):
            return RiskLevel.DANGEROUS, "find can delete or execute commands."
        return RiskLevel.READ_ONLY, f"{tokens[0]} is treated as read-only."

    if joined.startswith("./") or executable.endswith(".sh"):
        return RiskLevel.DANGEROUS, "Local scripts can do arbitrary work."

    return RiskLevel.DANGEROUS, f"{tokens[0]} is not in the read-only allowlist."


def _max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    order = {
        RiskLevel.READ_ONLY: 0,
        RiskLevel.WRITE_FILE: 1,
        RiskLevel.NETWORK: 2,
        RiskLevel.DANGEROUS: 3,
    }
    return left if order[left] >= order[right] else right
