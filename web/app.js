const els = {
  taskInput: document.getElementById("taskInput"),
  workspaceInput: document.getElementById("workspaceInput"),
  urlInput: document.getElementById("urlInput"),
  profileInput: document.getElementById("profileInput"),
  approvalModeSelect: document.getElementById("approvalModeSelect"),
  approvalModeHelp: document.getElementById("approvalModeHelp"),
  maxTurnsInput: document.getElementById("maxTurnsInput"),
  commandTimeoutInput: document.getElementById("commandTimeoutInput"),
  copilotTimeoutInput: document.getElementById("copilotTimeoutInput"),
  captureTimeoutInput: document.getElementById("captureTimeoutInput"),
  openBtn: document.getElementById("openBtn"),
  checkBtn: document.getElementById("checkBtn"),
  runBtn: document.getElementById("runBtn"),
  stopBtn: document.getElementById("stopBtn"),
  themeToggle: document.getElementById("themeToggle"),
  themeToggleIcon: document.getElementById("themeToggleIcon"),
  approveBtn: document.getElementById("approveBtn"),
  denyBtn: document.getElementById("denyBtn"),
  newSessionBtn: document.getElementById("newSessionBtn"),
  sessionStatus: document.getElementById("sessionStatus"),
  connectionPill: document.getElementById("connectionPill"),
  runStatus: document.getElementById("runStatus"),
  turnStatus: document.getElementById("turnStatus"),
  turnBadge: document.getElementById("turnBadge"),
  runFolder: document.getElementById("runFolder"),
  approvalPanel: document.getElementById("approvalPanel"),
  approvalReason: document.getElementById("approvalReason"),
  approvalCommand: document.getElementById("approvalCommand"),
  computedRisk: document.getElementById("computedRisk"),
  declaredRisk: document.getElementById("declaredRisk"),
  commandReason: document.getElementById("commandReason"),
  commandText: document.getElementById("commandText"),
  outputSubtitle: document.getElementById("outputSubtitle"),
  outputState: document.getElementById("outputState"),
  exitCode: document.getElementById("exitCode"),
  duration: document.getElementById("duration"),
  timedOut: document.getElementById("timedOut"),
  approved: document.getElementById("approved"),
  cwdValue: document.getElementById("cwdValue"),
  stdoutBox: document.getElementById("stdoutBox"),
  stderrBox: document.getElementById("stderrBox"),
  gitBranch: document.getElementById("gitBranch"),
  gitBefore: document.getElementById("gitBefore"),
  gitAfter: document.getElementById("gitAfter"),
  recentCommands: document.getElementById("recentCommands"),
  eventLog: document.getElementById("eventLog"),
};

let latestState = null;
let initialized = false;
const THEME_STORAGE_KEY = "shellpilot.theme";

function getSystemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getSavedTheme() {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    return saved === "dark" || saved === "light" ? saved : null;
  } catch {
    return null;
  }
}

function applyTheme(theme, source) {
  const resolved = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeSource = source;
  els.themeToggleIcon.textContent = resolved === "dark" ? "☀" : "☾";
  els.themeToggle.setAttribute("aria-label", `Switch to ${resolved === "dark" ? "light" : "dark"} mode`);
  els.themeToggle.title = source === "system" ? `Using system ${resolved} theme` : `Using ${resolved} theme`;
}

function initializeTheme() {
  const saved = getSavedTheme();
  applyTheme(saved || getSystemTheme(), saved ? "user" : "system");

  if (window.matchMedia) {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", (event) => {
      if (!getSavedTheme()) {
        applyTheme(event.matches ? "dark" : "light", "system");
      }
    });
  }
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const next = current === "dark" ? "light" : "dark";
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    // Theme persistence is optional; the visual toggle still works.
  }
  applyTheme(next, "user");
}

async function api(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

async function fetchState() {
  const response = await fetch("/api/state", { cache: "no-store" });
  latestState = await response.json();
  renderState(latestState);
}

function formPayload() {
  return {
    task: els.taskInput.value,
    workspace_dir: els.workspaceInput.value,
    url: els.urlInput.value,
    profile_dir: els.profileInput.value,
    max_turns: Number(els.maxTurnsInput.value || 12),
    command_timeout_s: Number(els.commandTimeoutInput.value || 120),
    copilot_timeout_s: Number(els.copilotTimeoutInput.value || 180),
    capture_timeout_s: Number(els.captureTimeoutInput.value || 15),
    approval_mode: selectedApprovalMode(),
  };
}

function renderState(state) {
  if (!initialized) {
    els.workspaceInput.value = state.workspace_dir || "";
    els.urlInput.value = state.copilot_url || "";
    els.profileInput.value = state.profile_dir || "";
    initialized = true;
  }

  const events = state.events || [];
  const latestTurn = latestTurnPayload(events);
  const latestGit = latestTurn.git_after || latestTurn.git_before || latestGitFromEvents(events);
  const sessionLabel = sessionText(state.session_status);

  els.sessionStatus.textContent = sessionLabel;
  els.connectionPill.textContent = sessionLabel === "Ready" || sessionLabel === "Opened" ? "Copilot Connected" : `Copilot ${sessionLabel}`;
  els.connectionPill.className = `pill ${sessionLabel === "Ready" || sessionLabel === "Opened" ? "ready" : "neutral"}`;
  els.runStatus.textContent = state.running ? state.current_step || "Running" : "Idle";
  els.turnStatus.textContent = `Turn ${state.current_turn || 0}`;
  els.turnBadge.textContent = `Turn #${state.current_turn || 0}`;
  els.runFolder.textContent = state.run_folder || "(none)";

  els.openBtn.disabled = state.running;
  els.checkBtn.disabled = state.running;
  els.runBtn.disabled = state.running;
  els.stopBtn.disabled = !state.running;
  els.newSessionBtn.disabled = state.running;

  renderApprovalMode(state.approval_mode || "ask", state.running);
  renderApproval(state.pending_approval);
  renderLatest(state.latest_command, state.latest_result, latestTurn);
  renderGit(latestGit);
  renderRecentCommands(events);
  renderEvents(events);
}

function selectedApprovalMode() {
  return els.approvalModeSelect.value || "ask";
}

function renderApprovalMode(mode, running) {
  els.approvalModeSelect.value = mode;
  els.approvalModeSelect.disabled = Boolean(running);
  const help = {
    ask: "Ask before write, network, or dangerous commands.",
    approve_for_me: "Auto-run write and network commands; ask for dangerous commands.",
    full_access: "Auto-run every classified risk level, including dangerous commands.",
  };
  els.approvalModeHelp.textContent = help[mode] || help.ask;
  els.approvalModeHelp.className = mode === "full_access" ? "mode-help warning" : "mode-help";
}

function sessionText(status) {
  const map = {
    not_opened: "Not opened",
    opening: "Opening",
    opened: "Opened",
    checking: "Checking",
    ready: "Ready",
    needs_attention: "Needs attention",
  };
  return map[status] || status || "Unknown";
}

function renderApproval(pending) {
  if (!pending) {
    els.approvalPanel.classList.add("hidden");
    return;
  }
  const decision = pending.decision || {};
  const assessment = pending.assessment || {};
  els.approvalPanel.classList.remove("hidden");
  els.approvalReason.textContent = `${assessment.risk || "risk"}: ${assessment.reason || decision.reason || ""}`;
  els.approvalCommand.textContent = decision.command || "";
  els.approveBtn.dataset.id = pending.id;
  els.denyBtn.dataset.id = pending.id;
}

function renderLatest(decision, result, latestTurn) {
  const commandDecision = decision || latestTurn.decision || {};
  const commandResult = result || latestTurn.command_result || {};
  const computedRisk = commandResult.computed_risk || "none";
  const decisionForDisplay = commandDecision.command
    ? {
        command: commandDecision.command,
        risk: commandDecision.risk || "unknown",
        reason: commandDecision.reason || "",
      }
    : null;

  els.commandText.textContent = decisionForDisplay ? JSON.stringify(decisionForDisplay, null, 2) : "(no command yet)";
  els.declaredRisk.textContent = commandDecision.risk || "none";
  els.commandReason.textContent = commandDecision.reason || "none";
  els.computedRisk.textContent = computedRisk;
  els.computedRisk.className = `risk ${computedRisk}`;

  const ok = commandResult.ok === true;
  const skipped = commandResult.skipped === true;
  const timedOut = commandResult.timed_out === true;
  els.outputState.textContent = skipped ? "Skipped" : timedOut ? "Timed out" : ok ? "Completed" : commandResult.command ? "Failed" : "Idle";
  els.outputSubtitle.textContent = commandResult.command ? commandResult.command : "Waiting for a command result.";
  els.exitCode.textContent = valueOrDash(commandResult.exit_code);
  els.duration.textContent = commandResult.duration_s == null ? "-" : `${Number(commandResult.duration_s).toFixed(0)}ms`;
  els.timedOut.textContent = boolText(commandResult.timed_out);
  els.approved.textContent = boolText(commandResult.approved);
  els.cwdValue.textContent = commandResult.cwd || "-";
  els.stdoutBox.textContent = renderTerminal(commandResult);
  els.stderrBox.textContent = commandResult.stderr || commandResult.skip_reason || "";
}

function renderTerminal(result) {
  if (!result || !result.command) return "";
  const prompt = `$ ${result.command}`;
  const output = result.stdout || "";
  return `${prompt}${output ? "\n" + output : ""}`;
}

function renderGit(git) {
  if (!git || Object.keys(git).length === 0) {
    els.gitBranch.innerHTML = "Branch: <strong>-</strong>";
    els.gitBefore.textContent = "(none)";
    els.gitAfter.textContent = "(none)";
    return;
  }
  if (!git.is_git_repo) {
    els.gitBranch.innerHTML = "Branch: <strong>not a Git repo</strong>";
    els.gitBefore.textContent = git.error || "Not a Git repository";
    els.gitAfter.textContent = "(none)";
    return;
  }
  els.gitBranch.innerHTML = `Branch: <strong>${escapeHtml(git.branch || "unknown")}</strong>`;
  els.gitBefore.textContent = git.status_short || "(clean)";
  els.gitAfter.textContent = formatDiffStat(git);
}

function formatDiffStat(git) {
  const chunks = [];
  if (git.diff_stat) chunks.push(git.diff_stat);
  if (git.diff_name_status) chunks.push(`name-status:\n${git.diff_name_status}`);
  if (git.staged_name_status) chunks.push(`staged:\n${git.staged_name_status}`);
  return chunks.join("\n\n") || "(no diff)";
}

function latestTurnPayload(events) {
  const event = [...events].reverse().find((item) => item.type === "turn_result" || item.type === "done");
  return event ? event.payload || {} : {};
}

function latestGitFromEvents(events) {
  const event = [...events].reverse().find((item) => item.payload && item.payload.git_before);
  return event ? event.payload.git_after || event.payload.git_before || {} : {};
}

function renderRecentCommands(events) {
  const turns = [...events]
    .filter((event) => event.type === "turn_result" && event.payload && event.payload.command_result)
    .slice(-8)
    .reverse();

  els.recentCommands.innerHTML = "";
  if (!turns.length) {
    const empty = document.createElement("div");
    empty.className = "recent-item";
    empty.innerHTML = '<span></span><span class="command">No commands yet</span><span class="time">-</span>';
    els.recentCommands.appendChild(empty);
    return;
  }

  for (const turn of turns) {
    const result = turn.payload.command_result || {};
    const row = document.createElement("div");
    row.className = `recent-item ${result.ok ? "ok" : ""}`;

    const icon = document.createElement("span");
    icon.textContent = result.ok ? "✓" : result.skipped ? "!" : "×";

    const command = document.createElement("span");
    command.className = "command";
    command.textContent = result.command || "";

    const time = document.createElement("span");
    time.className = "time";
    time.textContent = result.duration_s == null ? "-" : `${Number(result.duration_s).toFixed(2)}s`;

    row.append(icon, command, time);
    els.recentCommands.appendChild(row);
  }
}

function renderEvents(events) {
  els.eventLog.innerHTML = "";
  for (const event of [...events].reverse().slice(0, 40)) {
    const row = document.createElement("div");
    row.className = "event";

    const type = document.createElement("div");
    type.className = "event-type";
    type.textContent = event.type || "";

    const body = document.createElement("div");
    body.className = "event-body";
    body.textContent = summarizePayload(event.payload || {});

    row.append(type, body);
    els.eventLog.appendChild(row);
  }
}

function summarizePayload(payload) {
  if (payload.line) return payload.line;
  if (payload.error) return payload.error;
  if (payload.step) return payload.step;
  if (payload.run_folder) return payload.run_folder;
  if (payload.response_excerpt) return payload.response_excerpt;
  if (payload.decision && payload.decision.command) return payload.decision.command;
  if (payload.command_result && payload.command_result.command) return payload.command_result.command;
  return JSON.stringify(payload);
}

function valueOrDash(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function boolText(value) {
  if (value === true) return "Yes";
  if (value === false) return "No";
  return "-";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return entities[char];
  });
}

function wireEvents() {
  const source = new EventSource("/events");
  source.addEventListener("message", () => {
    fetchState().catch((error) => addLocalEvent("state_error", error.message));
  });
  source.onerror = () => {
    addLocalEvent("events", "Reconnecting to event stream...");
  };
}

function addLocalEvent(type, message) {
  if (!latestState) return;
  latestState.events = latestState.events || [];
  latestState.events.push({ ts: new Date().toISOString().slice(0, 19), type, payload: { line: message } });
  renderEvents(latestState.events);
}

els.openBtn.addEventListener("click", async () => {
  try {
    await api("/api/open_copilot", formPayload());
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.checkBtn.addEventListener("click", async () => {
  try {
    await api("/api/check_session", {});
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.runBtn.addEventListener("click", async () => {
  try {
    await api("/api/run", formPayload());
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.stopBtn.addEventListener("click", async () => {
  try {
    await api("/api/stop", {});
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.themeToggle.addEventListener("click", toggleTheme);

els.approveBtn.addEventListener("click", async () => {
  const id = els.approveBtn.dataset.id;
  if (!id) return;
  await api("/api/approval", { id, approved: true });
  await fetchState();
});

els.denyBtn.addEventListener("click", async () => {
  const id = els.denyBtn.dataset.id;
  if (!id) return;
  await api("/api/approval", { id, approved: false });
  await fetchState();
});

els.newSessionBtn.addEventListener("click", async () => {
  try {
    await api("/api/new_session", {});
    els.taskInput.value = "";
    els.approvalPanel.classList.add("hidden");
    els.taskInput.focus();
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.approvalModeSelect.addEventListener("change", async () => {
  const mode = selectedApprovalMode();
  renderApprovalMode(mode, latestState && latestState.running);
  try {
    await api("/api/approval_mode", { approval_mode: mode });
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
    if (latestState) renderApprovalMode(latestState.approval_mode || "ask", latestState.running);
  }
});

for (const button of document.querySelectorAll("[data-refresh]")) {
  button.addEventListener("click", () => fetchState().catch((error) => addLocalEvent("error", error.message)));
}

initializeTheme();
fetchState().then(wireEvents).catch((error) => addLocalEvent("error", error.message));
