from __future__ import annotations

import re
import shlex

from .models import RiskAssessment, RiskLevel


READ_ONLY_COMMANDS = {
    "pwd",
    "ls",
    "find",
    "cat",
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


def classify_command(command: str) -> RiskAssessment:
    command = (command or "").strip()
    if not command:
        return RiskAssessment(RiskLevel.DANGEROUS, False, "Empty command.")
    if _has_blocked_command_separator(command):
        return RiskAssessment(RiskLevel.DANGEROUS, False, "Multiple commands are not allowed.")
    if _has_write_redirection(command):
        return RiskAssessment(RiskLevel.WRITE_FILE, True, "Shell redirection writes to files.")

    lowered = command.lower()
    if any(pattern in lowered for pattern in DANGEROUS_GIT_PATTERNS):
        return RiskAssessment(RiskLevel.DANGEROUS, True, "Dangerous Git cleanup/reset command.")

    pipeline_parts = [part.strip() for part in command.split("|") if part.strip()]
    if not pipeline_parts:
        return RiskAssessment(RiskLevel.DANGEROUS, False, "No executable command found.")

    highest = RiskLevel.READ_ONLY
    reasons: list[str] = []
    for part in pipeline_parts:
        risk, reason = _classify_single(part)
        reasons.append(reason)
        highest = _max_risk(highest, risk)
    return RiskAssessment(highest, True, "; ".join(reasons))


def requires_approval(risk: RiskLevel) -> bool:
    return risk != RiskLevel.READ_ONLY


def _has_blocked_command_separator(command: str) -> bool:
    if "\n" in command or "\r" in command:
        return True
    return any(token in command for token in (";", "&&", "||"))


def _has_write_redirection(command: str) -> bool:
    return bool(re.search(r"(^|[^<])>>?($|\s|\S)", command))


def _classify_single(command: str) -> tuple[RiskLevel, str]:
    try:
        tokens = shlex.split(command, posix=True)
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

