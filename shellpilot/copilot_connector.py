from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import BrowserContext, Error, Locator, Page, Playwright, sync_playwright

from . import selectors, storage
from .models import PromptResult, RunConfig, SelectorTestReport
from .storage import OutputPaths
from .utils import EventLogger, StopRequested, now_iso, select_all_shortcut


StepCallback = Callable[[str], None]


class CopilotConnector:
    def __init__(self, ui_log_callback: Callable[[str], None] | None = None) -> None:
        self._ui_log_callback = ui_log_callback
        self._event_logger: EventLogger | None = None
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._profile_dir: Path | None = None

    def set_event_logger(self, event_logger: EventLogger | None) -> None:
        self._event_logger = event_logger

    def open_copilot(self, copilot_url: str, user_data_dir: str) -> str:
        profile = Path(user_data_dir).expanduser().resolve()
        profile.mkdir(parents=True, exist_ok=True)

        if self._context and not self._context_is_alive():
            self._log("WARNING", "stale_context_detected")
            self._close_context()

        if self._context and self._profile_dir and self._profile_dir != profile:
            self._log("INFO", "profile_changed", from_profile=str(self._profile_dir), to_profile=str(profile))
            self._close_context()

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        if self._context is None:
            self._context = self._launch_persistent_context(profile)
            self._profile_dir = profile

        self._page = self._ensure_page()
        self._page.goto(copilot_url, wait_until="domcontentloaded")
        self._page.bring_to_front()
        self._log("INFO", "navigated", url=self._page.url)
        return self._page.url

    def close(self) -> None:
        self._close_context()
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._log("INFO", "connector_closed")

    def test_selectors(self) -> SelectorTestReport:
        report = SelectorTestReport()
        try:
            page = self._require_page()
            report = selectors.test_selectors(page)
            self._log(
                "INFO",
                "selectors_tested",
                composer_found=report.composer_found,
                send_control_found=report.send_control_found,
                enter_fallback_available=report.enter_fallback_available,
                stop_control_found=report.stop_control_found,
                assistant_message_found=report.assistant_message_found,
            )
        except Exception as exc:  # noqa: BLE001
            report.details.append(f"Selector test error: {exc}")
            self._log("WARNING", "selectors_test_failed", error=str(exc))
        return report

    def start_new_chat(self, copilot_url: str, user_data_dir: str) -> dict[str, Any]:
        config = RunConfig(copilot_url=copilot_url, user_data_dir=user_data_dir)
        self._ensure_ready(config)
        page = self._require_page()
        before_url = page.url

        method = "navigate"
        selector = ""
        new_chat_control, new_chat_selector = selectors.find_new_chat_control(page)
        if new_chat_control is not None:
            try:
                new_chat_control.click(timeout=3000)
                method = "new_chat_control"
                selector = new_chat_selector or "unknown"
                self._log("INFO", "new_chat_clicked", selector=selector)
            except Error as exc:
                self._log("WARNING", "new_chat_click_failed", selector=new_chat_selector or "unknown", error=str(exc))
                page.goto(copilot_url, wait_until="domcontentloaded")
        else:
            page.goto(copilot_url, wait_until="domcontentloaded")

        self._wait_for_new_chat_ready()
        page.bring_to_front()
        self._log("INFO", "new_chat_ready", method=method, from_url=before_url, to_url=page.url)
        return {
            "ok": True,
            "method": method,
            "selector": selector,
            "from_url": before_url,
            "url": page.url,
        }

    def send_turn(
        self,
        *,
        prompt: str,
        index: int,
        total: int,
        config: RunConfig,
        output_paths: OutputPaths,
        stop_event: threading.Event,
        step_callback: StepCallback | None = None,
    ) -> PromptResult:
        self._ensure_ready(config)
        self._log("INFO", "prompt_prepared", index=index, total=total, chars=len(prompt))
        return self._run_single_prompt(
            prompt=prompt,
            index=index,
            total=total,
            config=config,
            output_paths=output_paths,
            stop_event=stop_event,
            step_callback=step_callback,
        )

    def _run_single_prompt(
        self,
        prompt: str,
        index: int,
        total: int,
        config: RunConfig,
        output_paths: OutputPaths,
        stop_event: threading.Event,
        step_callback: StepCallback | None,
    ) -> PromptResult:
        attempts = max(1, int(config.max_prompt_attempts)) if config.retry_once else 1
        progress = f"{index}/{total}"

        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            response_text = ""
            tail_fallback = False
            status = "success"
            error_text: str | None = None
            baseline_chat_text = ""
            baseline_assistant_text = ""

            try:
                baseline_chat_text = self._snapshot_chat_text()
                baseline_assistant_text = self._snapshot_latest_assistant_text()
                self._emit_step(step_callback, f"Typing ({progress})")
                self._send_prompt(
                    prompt,
                    previous_chat_text=baseline_chat_text,
                    send_start_timeout_s=config.send_start_timeout_s,
                )

                self._emit_step(step_callback, f"Waiting ({progress})")
                self._wait_for_response_completion(
                    config,
                    stop_event,
                    previous_chat_text=baseline_chat_text,
                    previous_assistant_text=baseline_assistant_text,
                )

                self._emit_step(step_callback, f"Capturing ({progress})")
                response_text, tail_fallback = self._capture_latest_response(
                    config,
                    previous_chat_text=baseline_chat_text,
                )
                if tail_fallback:
                    response_text = self._normalize_tail_fallback_response(response_text)
                    if self._looks_like_prompt_echo(response_text):
                        raise RuntimeError("Response capture returned the prompt text instead of Copilot's latest answer.")
                if not response_text.strip():
                    raise RuntimeError("Could not capture response text from the latest assistant reply.")

            except StopRequested as exc:
                status = "stopped"
                error_text = str(exc)
                self._log("INFO", "prompt_stopped", index=index, attempt=attempt)

            except Exception as exc:  # noqa: BLE001
                screenshot = self._capture_failure_screenshot(output_paths, index, attempt)
                status = "error"
                error_text = str(exc)
                if screenshot:
                    error_text = f"{error_text} (screenshot: {screenshot})"

                self._log(
                    "ERROR",
                    "prompt_failed",
                    index=index,
                    attempt=attempt,
                    error=str(exc),
                    screenshot=screenshot or "",
                )

                if attempt < attempts:
                    self._log("WARNING", "prompt_retrying", index=index, attempt=attempt + 1)
                    self._emit_step(step_callback, f"Recovering ({progress})")
                    self._recover_after_prompt_failure(config)
                    continue

            duration = time.perf_counter() - started
            result = PromptResult(
                ts=now_iso(),
                index=index,
                prompt=prompt,
                status=status,
                duration_s=duration,
                response_text=response_text,
                tail_fallback=tail_fallback,
                error=error_text,
                output_path="",
                attempt=attempt,
            )
            if response_text.strip() or error_text:
                result.output_path = str(storage.save_copilot_response(output_paths, result))

            self._emit_step(step_callback, f"Saving ({progress})")
            self._log(
                "INFO",
                "prompt_completed",
                index=index,
                status=status,
                duration_s=round(duration, 3),
                response_chars=len(response_text or ""),
                tail_fallback=tail_fallback,
            )
            if status == "stopped":
                stop_event.set()
            return result

        return PromptResult(
            ts=now_iso(),
            index=index,
            prompt=prompt,
            status="error",
            duration_s=0.0,
            response_text="",
            error="Prompt execution did not complete.",
            output_path="",
            attempt=attempts,
        )

    def _snapshot_chat_text(self) -> str:
        try:
            page = self._require_page()
        except Exception:
            return ""
        text = selectors.read_chat_text(page, include_frames=False, selector_timeout_ms=500).strip()
        if not text:
            text = selectors.read_chat_text(page, include_frames=True, selector_timeout_ms=500).strip()
        return self._normalize_tail_fallback_response(text) if text else ""

    def _snapshot_latest_assistant_text(self) -> str:
        try:
            page = self._require_page()
            assistant_message, _ = selectors.find_latest_assistant_message(page)
            if assistant_message is None:
                return ""
            return assistant_message.inner_text(timeout=700).strip()
        except Exception:
            return ""

    @staticmethod
    def _extract_new_chat_text(current_text: str, previous_text: str) -> str:
        current = (current_text or "").strip()
        previous = (previous_text or "").strip()
        if not current or not previous:
            return current
        if current == previous:
            return ""
        if current.startswith(previous):
            return current[len(previous) :].lstrip()

        max_overlap = min(len(current), len(previous), 4000)
        for size in range(max_overlap, 16, -1):
            if previous[-size:] == current[:size]:
                return current[size:].lstrip()

        anchor = previous[-240:]
        if anchor:
            idx = current.find(anchor)
            if idx >= 0:
                return current[idx + len(anchor) :].lstrip()
        return current

    def _normalize_tail_fallback_response(self, response_text: str) -> str:
        text = response_text.strip()
        if not text:
            return text

        marker = "Copilot said:"
        if marker in text:
            text = text.split(marker)[-1]

        lines = text.splitlines()
        cleaned_lines: list[str] = []
        ignored_prefixes = (
            "Message Copilot",
            "Tools",
            "Sources",
            "AI-generated content may be incorrect",
            "Show more lines",
        )
        for raw in lines:
            line = raw.rstrip()
            if any(line.strip().startswith(prefix) for prefix in ignored_prefixes):
                continue
            cleaned_lines.append(line)

        cleaned = "\n".join(cleaned_lines).strip()
        if cleaned.lower().startswith("copilot\n"):
            cleaned = cleaned.split("\n", 1)[-1].strip()
        return cleaned or text

    @staticmethod
    def _looks_like_prompt_echo(text: str) -> bool:
        markers = (
            "Current Git state:",
            "Previous command result:",
            "Workspace:",
            "Task:",
            "Git:",
            "Previous result:",
            "Run memory:",
            "Rules:",
            "Valid command JSON:",
            "Valid done JSON:",
            "You are ShellPilot",
        )
        marker_count = sum(1 for marker in markers if marker in text)
        if marker_count < 2:
            return False
        stripped = text.lstrip()
        return not (stripped.startswith('{"action"') or stripped.startswith("{'action'"))

    def _ensure_ready(self, config: RunConfig) -> None:
        page_closed = False
        if self._page is not None:
            try:
                page_closed = self._page.is_closed()
            except Exception:
                page_closed = True

        if self._context is None or not self._context_is_alive() or self._page is None or page_closed:
            self.open_copilot(config.copilot_url, config.user_data_dir)
            return

        expected = Path(config.user_data_dir).expanduser().resolve()
        if self._profile_dir != expected:
            self.open_copilot(config.copilot_url, config.user_data_dir)
            return

        if not self._page.url.startswith("http"):
            self._page.goto(config.copilot_url, wait_until="domcontentloaded")

    def _launch_persistent_context(self, profile: Path) -> BrowserContext:
        assert self._playwright is not None
        try:
            context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                channel="msedge",
                headless=False,
            )
            self._log("INFO", "browser_launched", channel="msedge", profile=str(profile))
            return context
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", "edge_launch_failed", error=str(exc))
            context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=False,
            )
            self._log("INFO", "browser_launched", channel="chromium", profile=str(profile))
            return context

    def _ensure_page(self) -> Page:
        assert self._context is not None
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    return self._page
            except Exception:
                pass
        pages = [item for item in self._context.pages if not item.is_closed()]
        page = pages[0] if pages else self._context.new_page()
        self._page = page
        return page

    def _require_page(self) -> Page:
        page = self._page
        if page is None:
            raise RuntimeError("ShellPilot Copilot session is not open. Click 'Open Copilot / Login' first.")
        try:
            if page.is_closed():
                self._page = None
                raise RuntimeError("ShellPilot Copilot session window was closed. Reopen it from the GUI.")
        except Error as exc:
            self._page = None
            raise RuntimeError(f"ShellPilot Copilot session is unavailable ({exc}). Sign in again.") from exc
        return page

    def _send_prompt(self, prompt: str, *, previous_chat_text: str = "", send_start_timeout_s: float = 6.0) -> None:
        page = self._require_page()
        composer, composer_selector = selectors.find_composer(page)
        if composer is None:
            raise RuntimeError("Could not find prompt input box. Click in the composer once, then check the session.")

        self._log("INFO", "composer_detected", selector=composer_selector or "unknown")
        self._fill_composer(composer, prompt)

        send_button, send_selector = selectors.find_send_control(page)
        if send_button is not None:
            try:
                send_button.click(timeout=2500)
                if self._wait_for_send_start(composer, prompt, previous_chat_text, timeout_s=send_start_timeout_s):
                    self._log("INFO", "prompt_sent", method="send_button", selector=send_selector or "unknown")
                    return
                self._log("WARNING", "send_button_did_not_submit", selector=send_selector or "unknown")
            except Error as exc:
                self._log("WARNING", "send_button_click_failed", selector=send_selector or "unknown", error=str(exc))

        self._focus_composer(composer)
        try:
            composer.press("Enter")
        except Error:
            page.keyboard.press("Enter")
        if self._wait_for_send_start(composer, prompt, previous_chat_text, timeout_s=send_start_timeout_s):
            self._log("INFO", "prompt_sent", method="enter_key")
            return

        raise TimeoutError("Prompt did not leave the composer before timeout; Copilot may not have received it.")

    def _fill_composer(self, composer: Locator, prompt: str) -> None:
        page = self._require_page()
        self._focus_composer(composer)

        filled = False
        try:
            composer.fill(prompt, timeout=8000)
            filled = True
        except Error:
            filled = False

        if not filled:
            try:
                page.keyboard.press(select_all_shortcut())
                page.keyboard.insert_text(prompt)
                filled = True
            except Error:
                filled = False

        if not self._composer_still_has_prompt(composer, prompt):
            try:
                composer.evaluate(
                    """
                    (el, value) => {
                        const tag = (el.tagName || "").toLowerCase();
                        if (tag === "textarea" || tag === "input") {
                            const proto = tag === "textarea" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                            const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
                            if (setter) setter.call(el, value);
                            else el.value = value;
                        } else {
                            el.textContent = value;
                        }
                        el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
                        el.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                    """,
                    prompt,
                )
                filled = True
            except Error:
                filled = False

        if not filled or not self._composer_still_has_prompt(composer, prompt):
            raise RuntimeError("Could not place the prompt text into the Copilot composer.")

    def _focus_composer(self, composer: Locator) -> None:
        try:
            composer.click(timeout=5000)
        except Error:
            try:
                composer.focus(timeout=1000)
            except Error:
                pass

    def _wait_for_send_start(self, composer: Locator, prompt: str, previous_chat_text: str, *, timeout_s: float) -> bool:
        page = self._require_page()
        deadline = time.monotonic() + max(1.0, timeout_s)
        previous = self._normalize_tail_fallback_response(previous_chat_text or "")
        while time.monotonic() < deadline:
            if selectors.has_stop_control_visible(page):
                return True
            if not self._composer_still_has_prompt(composer, prompt):
                return True
            current_text = selectors.read_chat_text(page, include_frames=False, selector_timeout_ms=300).strip()
            if current_text:
                current = self._normalize_tail_fallback_response(current_text)
                if previous and current != previous:
                    return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _normalize_prompt_for_compare(text: str) -> str:
        return " ".join((text or "").split())

    def _composer_still_has_prompt(self, composer: Locator, prompt: str) -> bool:
        text = selectors.read_composer_text(composer)
        current = self._normalize_prompt_for_compare(text)
        expected = self._normalize_prompt_for_compare(prompt)
        if not current or not expected:
            return False
        if current == expected:
            return True
        return expected[:240] in current or current[:240] in expected

    def _wait_for_response_completion(
        self,
        config: RunConfig,
        stop_event: threading.Event,
        *,
        previous_chat_text: str = "",
        previous_assistant_text: str = "",
    ) -> None:
        page = self._require_page()
        deadline = time.monotonic() + max(5, config.max_timeout_s)
        interval_s = max(0.2, config.sample_interval_ms / 1000.0)
        stability_window = max(0.8, config.stability_seconds)
        no_activity_timeout_s = max(8.0, config.no_activity_timeout_s)

        last_chat_text = previous_chat_text or selectors.read_chat_text(page, include_frames=False, selector_timeout_ms=900)
        last_assistant_text = previous_assistant_text
        last_change = time.monotonic()
        started = time.monotonic()
        saw_chat_activity = False
        saw_assistant_activity = False
        stop_seen = selectors.has_stop_control_visible(page)

        while time.monotonic() < deadline:
            if stop_event.is_set():
                raise StopRequested("Stopped by user request.")

            stop_visible = selectors.has_stop_control_visible(page)
            if stop_visible:
                stop_seen = True

            assistant_text = self._snapshot_latest_assistant_text()
            if assistant_text and assistant_text != last_assistant_text:
                saw_assistant_activity = True
                last_assistant_text = assistant_text
                last_change = time.monotonic()

            current_text = selectors.read_chat_text(page, include_frames=False, selector_timeout_ms=900)
            if current_text != last_chat_text:
                saw_chat_activity = True
                last_chat_text = current_text
                last_change = time.monotonic()

            stable_for = time.monotonic() - last_change
            elapsed = time.monotonic() - started

            if elapsed >= no_activity_timeout_s and not saw_assistant_activity and not stop_seen:
                raise RuntimeError(
                    "No assistant response activity detected after sending the prompt. Check the Copilot session, then try again."
                )

            if elapsed >= 1.0 and saw_assistant_activity and stable_for >= stability_window:
                if not stop_seen:
                    return
                if stop_seen and not stop_visible:
                    return
            if elapsed >= no_activity_timeout_s and saw_chat_activity and not saw_assistant_activity and stable_for >= stability_window:
                self._log("WARNING", "assistant_selector_missing_using_chat_activity")
                return

            time.sleep(interval_s)

        raise TimeoutError(f"Response did not finish before timeout ({config.max_timeout_s}s).")

    def _recover_after_prompt_failure(self, config: RunConfig) -> None:
        try:
            self.start_new_chat(config.copilot_url, config.user_data_dir)
            self._log("INFO", "prompt_retry_recovered", method="new_chat")
            return
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", "new_chat_recovery_failed", error=str(exc))

        page = self._page
        if page is None:
            return
        try:
            page.goto(config.copilot_url, wait_until="domcontentloaded")
            self._wait_for_new_chat_ready(timeout_s=8.0)
            self._log("INFO", "prompt_retry_recovered", method="navigate")
        except Exception as exc:  # noqa: BLE001
            self._log("WARNING", "navigate_recovery_failed", error=str(exc))

    def _wait_for_new_chat_ready(self, timeout_s: float = 8.0) -> None:
        page = self._require_page()
        deadline = time.monotonic() + timeout_s
        last_error = ""
        while time.monotonic() < deadline:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=500)
            except Exception:
                pass
            try:
                composer, _ = selectors.find_composer(page)
                if composer is not None:
                    return
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            time.sleep(0.25)
        if last_error:
            raise RuntimeError(f"New Copilot chat did not become ready: {last_error}")
        raise RuntimeError("New Copilot chat did not become ready; composer was not detected.")

    def _capture_latest_response(self, config: RunConfig, *, previous_chat_text: str = "") -> tuple[str, bool]:
        page = self._require_page()
        capture_deadline = time.monotonic() + max(4, int(config.capture_timeout_s))

        assistant_message, message_selector = selectors.find_latest_assistant_message(page)
        if assistant_message is not None:
            self._log("INFO", "assistant_message_detected", selector=message_selector or "unknown")

            message_text = ""
            remaining_ms = int((capture_deadline - time.monotonic()) * 1000)
            if remaining_ms > 0:
                inner_text_timeout_ms = int(min(1500, max(250, remaining_ms)))
                try:
                    message_text = assistant_message.inner_text(timeout=inner_text_timeout_ms).strip()
                except Exception:
                    message_text = ""

            if message_text:
                self._log("INFO", "response_captured", method="assistant_container")
                return message_text, False

            copy_button, copy_selector = selectors.find_copy_button_in_message(assistant_message)
            if copy_button is not None:
                click_timeout_ms = int(min(1200, max(250, (capture_deadline - time.monotonic()) * 1000)))
                if click_timeout_ms > 0:
                    try:
                        copy_button.click(timeout=click_timeout_ms)
                        clipboard_deadline = min(capture_deadline, time.monotonic() + 1.4)
                        while time.monotonic() < clipboard_deadline:
                            clipboard_text = selectors.read_clipboard_text(page).strip()
                            if clipboard_text:
                                self._log("INFO", "response_captured", method="copy_button", selector=copy_selector or "unknown")
                                return clipboard_text, False
                            time.sleep(0.15)
                    except Error:
                        self._log("WARNING", "copy_button_capture_failed", selector=copy_selector or "unknown")

        chat_tail = selectors.read_chat_text(page, include_frames=False, selector_timeout_ms=700).strip()
        if not chat_tail and time.monotonic() < capture_deadline:
            chat_tail = selectors.read_chat_text(page, include_frames=True, selector_timeout_ms=700).strip()

        if chat_tail:
            chat_tail = self._extract_new_chat_text(
                self._normalize_tail_fallback_response(chat_tail),
                previous_chat_text,
            ) or chat_tail
            self._log("WARNING", "response_captured", method="chat_tail_fallback")
            return chat_tail[-5000:], True

        if time.monotonic() >= capture_deadline:
            self._log("WARNING", "response_capture_timeout", timeout_s=config.capture_timeout_s)

        return "", True

    def _capture_failure_screenshot(self, paths: OutputPaths, index: int, attempt: int) -> str:
        page = self._page
        if page is None:
            return ""
        screenshot = paths.screenshots_dir / f"prompt_{index:03d}_attempt_{attempt}.png"
        try:
            page.screenshot(path=str(screenshot), full_page=True)
            return str(screenshot)
        except Exception:
            return ""

    def _emit_step(self, step_callback: StepCallback | None, value: str) -> None:
        if step_callback:
            step_callback(value)

    def _close_context(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None
        self._page = None
        self._profile_dir = None

    def _context_is_alive(self) -> bool:
        if self._context is None:
            return False
        try:
            _ = self._context.pages
            return True
        except Exception:
            return False

    def _log(self, level: str, event: str, **fields: object) -> None:
        if self._event_logger:
            self._event_logger.log(level, event, **fields)
            return
        if self._ui_log_callback:
            details = ", ".join(f"{key}={value}" for key, value in fields.items())
            line = f"[{now_iso()}] {level.upper()} {event}"
            if details:
                line = f"{line} | {details}"
            self._ui_log_callback(line)


class CopilotWorker:
    """Owns the sync Playwright connector on a single worker thread."""

    def __init__(self, ui_log_callback: Callable[[str], None] | None = None) -> None:
        self._ui_log_callback = ui_log_callback
        self._queue: queue.Queue[tuple[str, tuple[Any, ...], dict[str, Any], queue.Queue[Any]]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="shellpilot-copilot", daemon=True)
        self._thread.start()

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        result_queue: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._queue.put((method, args, kwargs, result_queue))
        ok, payload = result_queue.get()
        if ok:
            return payload
        raise payload

    def _run(self) -> None:
        connector = CopilotConnector(ui_log_callback=self._ui_log_callback)
        while True:
            method, args, kwargs, result_queue = self._queue.get()
            try:
                if method == "__close__":
                    connector.close()
                    result_queue.put((True, None))
                    break
                result = getattr(connector, method)(*args, **kwargs)
                result_queue.put((True, result))
            except Exception as exc:  # noqa: BLE001
                result_queue.put((False, exc))

    def close(self) -> None:
        try:
            self.call("__close__")
        except Exception:
            pass
