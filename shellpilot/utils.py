from __future__ import annotations

import json
import platform
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable


class StopRequested(Exception):
    """Raised when a stop request is received."""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def make_excerpt(text: str, max_len: int = 180) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


def select_all_shortcut() -> str:
    return "Meta+A" if platform.system().lower() == "darwin" else "Control+A"


def is_transient_error(exc: Exception) -> bool:
    message = str(exc).lower()
    transient_tokens = (
        "timeout",
        "detached",
        "not visible",
        "target closed",
        "navigation",
        "interrupted",
        "stale",
    )
    return any(token in message for token in transient_tokens)


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 40
    return text[:head] + "\n... [trimmed] ...\n" + text[-tail:]


@dataclass(slots=True)
class EventLogger:
    log_path: Path
    ui_callback: Callable[[str], None] | None = None
    _lock: threading.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def log(self, level: str, event: str, **fields: object) -> None:
        record = {
            "ts": now_iso(),
            "level": level.upper(),
            "event": event,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        if self.ui_callback:
            details = ", ".join(f"{key}={value}" for key, value in fields.items())
            formatted = f"[{record['ts']}] {record['level']} {event}"
            if details:
                formatted = f"{formatted} | {details}"
            self.ui_callback(formatted)

    def heartbeat(self, event: str, **fields: object) -> None:
        self.log("INFO", event, monotonic=round(time.monotonic(), 3), **fields)

