from __future__ import annotations

import re
import time
from typing import Iterable

from playwright.sync_api import Error, Frame, Locator, Page

from .models import SelectorTestReport


COMPOSER_SELECTORS: tuple[str, ...] = (
    "textarea",
    "input[type='text']",
    "input:not([type])",
    "textarea[placeholder*='message' i]",
    "textarea[aria-label*='message' i]",
    "input[placeholder*='message' i]",
    "input[aria-label*='message' i]",
    "div[contenteditable='true'][role='textbox']",
    "div[contenteditable='true'][aria-label*='message' i]",
    "div[contenteditable='true'][data-testid*='composer' i]",
    "[role='textbox'][contenteditable='true']",
    "[data-testid*='composer' i] [contenteditable='true']",
    "[data-testid*='input' i] [contenteditable='true']",
)

COMPOSER_SHELL_SELECTORS: tuple[str, ...] = (
    "div:has-text('Message Copilot')",
    "div:has-text('Message')",
    "div:has-text('Ask Copilot')",
    "div:has-text('How can I help')",
    "[data-testid*='composer' i]",
    "[data-testid*='input' i]",
    "main",
)

SEND_BUTTON_SELECTORS: tuple[str, ...] = (
    "button[aria-label*='send' i]",
    "button[aria-label*='submit' i]",
    "button[data-testid*='send' i]",
    "button[data-testid*='submit' i]",
    "button[title*='send' i]",
    "button[title*='submit' i]",
    "[data-icon-name*='send' i]",
)

STOP_BUTTON_SELECTORS: tuple[str, ...] = (
    "button[aria-label*='stop generating' i]",
    "button[aria-label*='stop' i]",
    "button[aria-label*='cancel' i]",
    "button[title*='stop' i]",
    "button[title*='cancel' i]",
)

ASSISTANT_MESSAGE_SELECTORS: tuple[str, ...] = (
    "[data-message-author-role='assistant']",
    "[data-author='assistant']",
    "article[data-author*='assistant' i]",
    "div[data-testid*='assistant' i]",
    "div[class*='assistant' i]",
)

CHAT_CONTAINER_SELECTORS: tuple[str, ...] = (
    "main",
    "[role='main']",
    "[data-testid*='chat' i]",
    "[class*='conversation' i]",
    "[class*='chat' i]",
)

COPY_BUTTON_SELECTORS: tuple[str, ...] = (
    "button[aria-label*='copy' i]",
    "button[title*='copy' i]",
    "button[data-testid*='copy' i]",
)

NEW_CHAT_SELECTORS: tuple[str, ...] = (
    "button[aria-label*='new chat' i]",
    "a[aria-label*='new chat' i]",
    "button[aria-label*='new conversation' i]",
    "a[aria-label*='new conversation' i]",
    "button[aria-label*='start new' i]",
    "a[aria-label*='start new' i]",
    "button[title*='new chat' i]",
    "a[title*='new chat' i]",
    "button[title*='new conversation' i]",
    "a[title*='new conversation' i]",
    "button[data-testid*='new-chat' i]",
    "a[data-testid*='new-chat' i]",
    "button[data-testid*='newChat' i]",
    "a[data-testid*='newChat' i]",
)

Context = Page | Frame


def iter_contexts(page: Page, include_frames: bool = True, max_frames: int = 4) -> Iterable[Context]:
    yield page
    if not include_frames:
        return

    yielded = 0
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        try:
            frame_url = (frame.url or "").lower()
        except Error:
            continue
        if frame_url and frame_url != "about:blank":
            if not any(token in frame_url for token in ("m365.cloud", "copilot", "microsoft", "office", "live.com")):
                continue
        yield frame
        yielded += 1
        if yielded >= max_frames:
            break


def _safe_count(locator: Locator) -> int:
    try:
        return locator.count()
    except Error:
        return 0


def _is_visible(locator: Locator, timeout_ms: int = 150) -> bool:
    try:
        return locator.is_visible(timeout=timeout_ms)
    except Error:
        return False


def _first_match(context: Context, selectors: tuple[str, ...], visible_only: bool = True) -> tuple[Locator | None, str | None]:
    for selector in selectors:
        try:
            locator = context.locator(selector).first
        except Error:
            continue
        if _safe_count(locator) == 0:
            continue
        if visible_only and not _is_visible(locator):
            continue
        return locator, selector
    return None, None


def _active_element_is_editable(context: Context) -> bool:
    try:
        return bool(
            context.evaluate(
                """
                () => {
                    const el = document.activeElement;
                    if (!el) return false;
                    const tag = (el.tagName || "").toLowerCase();
                    if (el.isContentEditable) return true;
                    if (tag === "textarea") return true;
                    if (tag === "input") {
                        const type = (el.getAttribute("type") || "text").toLowerCase();
                        return type !== "hidden";
                    }
                    return (el.getAttribute("role") || "").toLowerCase() === "textbox";
                }
                """
            )
        )
    except Error:
        return False


def _focused_locator(context: Context) -> Locator | None:
    try:
        focused = context.locator(":focus").first
    except Error:
        return None
    if _safe_count(focused) == 0:
        return None
    return focused


def focus_composer(page: Page) -> tuple[Locator | None, str | None]:
    probe_selectors = (*COMPOSER_SELECTORS, *COMPOSER_SHELL_SELECTORS)
    for context in iter_contexts(page):
        for selector in probe_selectors:
            try:
                locator = context.locator(selector).first
            except Error:
                continue
            if _safe_count(locator) == 0:
                continue
            if not _is_visible(locator):
                continue
            try:
                locator.click(timeout=1500)
            except Error:
                continue

            time.sleep(0.08)
            focused = _focused_locator(context)
            if focused is not None and _active_element_is_editable(context):
                return focused, f"focus:{selector}"
            if _active_element_is_editable(context):
                return locator, f"focus:{selector}"
    return None, None


def find_composer(page: Page) -> tuple[Locator | None, str | None]:
    for context in iter_contexts(page):
        locator, selector = _first_match(context, COMPOSER_SELECTORS)
        if locator:
            return locator, selector

    for context in iter_contexts(page):
        try:
            candidate = context.get_by_role("textbox").last
        except Error:
            continue
        if _safe_count(candidate) and _is_visible(candidate):
            return candidate, "role=textbox(last)"

    focused, focus_selector = focus_composer(page)
    if focused:
        return focused, focus_selector

    return None, None


def find_send_control(page: Page) -> tuple[Locator | None, str | None]:
    for context in iter_contexts(page):
        locator, selector = _first_match(context, SEND_BUTTON_SELECTORS)
        if locator:
            return locator, selector

    for context in iter_contexts(page):
        try:
            candidate = context.get_by_role("button", name=re.compile("send|submit", re.IGNORECASE)).first
        except Error:
            continue
        if _safe_count(candidate) and _is_visible(candidate):
            return candidate, "role=button[name*=send|submit]"

    return None, None


def find_new_chat_control(page: Page) -> tuple[Locator | None, str | None]:
    for context in iter_contexts(page):
        locator, selector = _first_match(context, NEW_CHAT_SELECTORS)
        if locator:
            return locator, selector

    name_patterns = (
        "new chat",
        "new conversation",
        "start new",
        "new topic",
    )
    for context in iter_contexts(page):
        for pattern in name_patterns:
            for role in ("button", "link"):
                try:
                    candidate = context.get_by_role(role, name=re.compile(pattern, re.IGNORECASE)).first
                except Error:
                    continue
                if _safe_count(candidate) and _is_visible(candidate):
                    return candidate, f"role={role}[name*={pattern}]"

    return None, None


def has_stop_control_visible(page: Page) -> bool:
    for context in iter_contexts(page):
        locator, _ = _first_match(context, STOP_BUTTON_SELECTORS)
        if locator:
            return True
        try:
            candidate = context.get_by_role("button", name=re.compile("stop|cancel", re.IGNORECASE)).first
        except Error:
            continue
        if _safe_count(candidate) and _is_visible(candidate):
            return True
    return False


def has_stop_control_present(page: Page) -> bool:
    for context in iter_contexts(page):
        locator, _ = _first_match(context, STOP_BUTTON_SELECTORS, visible_only=False)
        if locator:
            return True
        try:
            candidate = context.get_by_role("button", name=re.compile("stop|cancel", re.IGNORECASE)).first
        except Error:
            continue
        if _safe_count(candidate):
            return True
    return False


def find_latest_assistant_message(page: Page) -> tuple[Locator | None, str | None]:
    for context in iter_contexts(page):
        for selector in ASSISTANT_MESSAGE_SELECTORS:
            try:
                locator = context.locator(selector)
            except Error:
                continue
            count = _safe_count(locator)
            if count == 0:
                continue
            latest = locator.nth(count - 1)
            return latest, selector
    return None, None


def find_copy_button_in_message(message_locator: Locator) -> tuple[Locator | None, str | None]:
    for selector in COPY_BUTTON_SELECTORS:
        try:
            locator = message_locator.locator(selector).first
        except Error:
            continue
        if _safe_count(locator) and _is_visible(locator):
            return locator, selector

    try:
        candidate = message_locator.get_by_role("button", name=re.compile("copy", re.IGNORECASE)).first
    except Error:
        return None, None
    if _safe_count(candidate) and _is_visible(candidate):
        return candidate, "role=button[name*=copy]"
    return None, None


def read_chat_text(page: Page, include_frames: bool = True, selector_timeout_ms: int = 1200) -> str:
    for context in iter_contexts(page, include_frames=include_frames):
        for selector in CHAT_CONTAINER_SELECTORS:
            try:
                locator = context.locator(selector).first
            except Error:
                continue
            if _safe_count(locator) == 0:
                continue
            if not _is_visible(locator):
                continue
            try:
                text = locator.inner_text(timeout=selector_timeout_ms).strip()
            except Exception:
                continue
            if text:
                return text

    try:
        return page.locator("body").inner_text(timeout=selector_timeout_ms).strip()
    except Exception:
        return ""


def read_clipboard_text(page: Page) -> str:
    try:
        return (
            page.evaluate(
                """
                async () => {
                    try {
                        return await navigator.clipboard.readText();
                    } catch (err) {
                        return "";
                    }
                }
                """
            )
            or ""
        )
    except Exception:
        return ""


def test_selectors(page: Page) -> SelectorTestReport:
    report = SelectorTestReport()
    composer, composer_selector = find_composer(page)
    send, send_selector = find_send_control(page)
    assistant, assistant_selector = find_latest_assistant_message(page)

    report.composer_found = composer is not None
    report.send_control_found = send is not None
    report.enter_fallback_available = report.composer_found
    report.assistant_message_found = assistant is not None
    report.stop_control_found = has_stop_control_present(page)

    if composer_selector:
        report.details.append(f"Composer detected via: {composer_selector}")
    else:
        report.details.append("Composer input not detected.")

    if send_selector:
        report.details.append(f"Send control detected via: {send_selector}")
    elif report.enter_fallback_available:
        report.details.append("Send control not detected; Enter fallback is ready through the composer.")
    else:
        report.details.append("Send control not detected.")

    if assistant_selector:
        report.details.append(f"Assistant message selector: {assistant_selector}")
    else:
        report.details.append("Assistant message container not detected yet.")

    if report.stop_control_found:
        report.details.append("Stop/Cancel control is detectable.")
    else:
        report.details.append("Stop/Cancel control not detected in current DOM state.")

    return report
