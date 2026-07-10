const els = {
  taskInput: document.getElementById("taskInput"),
  composerProject: document.getElementById("composerProject"),
  composerProjectName: document.getElementById("composerProjectName"),
  urlInput: document.getElementById("urlInput"),
  profileInput: document.getElementById("profileInput"),
  approvalModeSelect: document.getElementById("approvalModeSelect"),
  approvalModeHelp: document.getElementById("approvalModeHelp"),
  shellKindSelect: document.getElementById("shellKindSelect"),
  maxTurnsInput: document.getElementById("maxTurnsInput"),
  chatRefreshInput: document.getElementById("chatRefreshInput"),
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
  newProjectBtn: document.getElementById("newProjectBtn"),
  sessionStatus: document.getElementById("sessionStatus"),
  connectionPill: document.getElementById("connectionPill"),
  runStatus: document.getElementById("runStatus"),
  activityStatus: document.getElementById("activityStatus"),
  activityLabel: document.getElementById("activityLabel"),
  activityElapsed: document.getElementById("activityElapsed"),
  runModeToggle: document.getElementById("runModeToggle"),
  runModeHelp: document.getElementById("runModeHelp"),
  turnStatus: document.getElementById("turnStatus"),
  runFolder: document.getElementById("runFolder"),
  activeProjectTitle: document.getElementById("activeProjectTitle"),
  activeSessionTitle: document.getElementById("activeSessionTitle"),
  chatTranscript: document.getElementById("chatTranscript"),
  projectList: document.getElementById("projectList"),
  approvalPanel: document.getElementById("approvalPanel"),
  approvalReason: document.getElementById("approvalReason"),
  approvalCommand: document.getElementById("approvalCommand"),
  planPanel: document.getElementById("planPanel"),
  planTitle: document.getElementById("planTitle"),
  planStatus: document.getElementById("planStatus"),
  planRevision: document.getElementById("planRevision"),
  planTasks: document.getElementById("planTasks"),
  planActions: document.getElementById("planActions"),
  planApproveBtn: document.getElementById("planApproveBtn"),
  planRejectBtn: document.getElementById("planRejectBtn"),
  eventLog: document.getElementById("eventLog"),
  workspaceBrowser: document.getElementById("workspaceBrowser"),
  workspaceBrowserPath: document.getElementById("workspaceBrowserPath"),
  workspaceBrowserRoots: document.getElementById("workspaceBrowserRoots"),
  workspaceBrowserList: document.getElementById("workspaceBrowserList"),
  browseCloseBtn: document.getElementById("browseCloseBtn"),
  browseHomeBtn: document.getElementById("browseHomeBtn"),
  browseParentBtn: document.getElementById("browseParentBtn"),
  browseUseBtn: document.getElementById("browseUseBtn"),
};

const { getJson, postJson } = window.shellPilotResponse;

let latestState = null;
let initialized = false;
let browserPath = "";
let browserHome = "";
let browserParent = "";
let showAllTranscript = false;
let reconnectRefreshTimer = null;
let lastReconnectNotice = 0;
let runStartedAt = 0;
let lastRunDurationMs = 0;
let activityTimer = null;
const THEME_STORAGE_KEY = "shellpilot.theme";
const COLLAPSED_PROJECTS_KEY = "shellpilot.collapsed-projects";
const MAX_VISIBLE_TURNS = 24;
const TRANSCRIPT_EVENT_TYPES = new Set([
  "run_started",
  "new_session",
  "turn_result",
  "approval_required",
  "approval_answered",
  "done",
  "turn_error",
  "run_error",
  "stopped",
  "max_turns",
  "plan_replan_required",
  "plan_rejected",
  "plan_error",
  "plan_completed",
  "project_selected",
  "session_loaded",
]);
const collapsedProjects = new Set(readCollapsedProjects());

function readCollapsedProjects() {
  try {
    const value = JSON.parse(localStorage.getItem(COLLAPSED_PROJECTS_KEY) || "[]");
    return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function persistCollapsedProjects() {
  try {
    localStorage.setItem(COLLAPSED_PROJECTS_KEY, JSON.stringify([...collapsedProjects]));
  } catch {
    // Sidebar preference persistence is optional.
  }
}

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
    // Theme persistence is optional.
  }
  applyTheme(next, "user");
}

async function fetchState() {
  const started = performance.now();
  latestState = await getJson("/api/state");
  showAllTranscript = false;
  renderState(latestState);
  const duration = performance.now() - started;
  if (duration >= 40) {
    console.debug("[ShellPilot] ui_state_refresh", { duration_ms: Math.round(duration), event_count: (latestState.events || []).length });
  }
}

function formPayload() {
  return {
    task: els.taskInput.value,
    workspace_dir: latestState ? latestState.workspace_dir : "",
    url: els.urlInput.value,
    profile_dir: els.profileInput.value,
    max_turns: Number(els.maxTurnsInput.value || 100),
    chat_refresh_turns: Number(els.chatRefreshInput.value || 0),
    command_timeout_s: Number(els.commandTimeoutInput.value || 120),
    copilot_timeout_s: Number(els.copilotTimeoutInput.value || 180),
    capture_timeout_s: Number(els.captureTimeoutInput.value || 15),
    approval_mode: selectedApprovalMode(),
    shell_kind: els.shellKindSelect.value || "bash",
    run_mode: selectedRunMode(),
  };
}

function renderState(state) {
  if (!initialized) {
    els.urlInput.value = state.copilot_url || "";
    els.profileInput.value = state.profile_dir || "";
    els.shellKindSelect.value = state.shell_kind || "bash";
    initialized = true;
  }

  renderRunChrome(state);
  renderProjects(state);
  renderTranscript(state);
  renderEvents(state.events || []);
}

function renderRunChrome(state) {
  const sessionLabel = sessionText(state.session_status);
  els.sessionStatus.textContent = sessionLabel;
  els.connectionPill.textContent =
    sessionLabel === "Ready" || sessionLabel === "Opened" ? "Copilot Connected" : `Copilot ${sessionLabel}`;
  els.connectionPill.className = `status-pill ${sessionLabel === "Ready" || sessionLabel === "Opened" ? "ready" : "neutral"}`;
  const activity = friendlyStep(state.running ? state.current_step : state.current_step === "Idle" ? "Idle" : state.current_step);
  els.runStatus.textContent = activity;
  els.turnStatus.textContent = `Turn ${state.current_turn || 0}`;
  els.runFolder.textContent = shortPath(state.run_folder || "(no artifacts)");
  els.runFolder.title = state.run_folder || "";

  els.openBtn.disabled = state.running;
  els.checkBtn.disabled = state.running;
  els.runBtn.disabled = state.running;
  els.stopBtn.disabled = !state.running;
  els.newSessionBtn.disabled = state.running;
  els.newProjectBtn.disabled = state.running;
  els.shellKindSelect.disabled = state.running;

  renderActivity(state);
  renderApprovalMode(state.approval_mode || "ask", state.running);
  renderApproval(state.pending_approval);
  renderRunMode(state.run_mode || "direct", state.running);
  renderPlan(state);
  renderHeader(state);
}

function renderActivity(state) {
  if (!els.activityStatus) return;
  if (state.running && !runStartedAt) runStartedAt = Date.now();
  if (!state.running && runStartedAt && !lastRunDurationMs) {
    lastRunDurationMs = Date.now() - runStartedAt;
  }

  const label = state.running ? friendlyStep(state.current_step) : friendlyStep(state.current_step || "Idle");
  const elapsed = state.running ? Date.now() - runStartedAt : lastRunDurationMs;
  els.activityLabel.textContent = label;
  els.activityElapsed.textContent = elapsed > 0 ? formatElapsed(elapsed) : "";
  els.activityStatus.className = `activity-status ${state.running ? "running" : state.current_step === "Error" ? "error" : "neutral"}`;
  els.activityStatus.title = state.running ? `${label}. Elapsed ${formatElapsed(elapsed)}.` : label;

  if (state.running && !activityTimer) {
    activityTimer = window.setInterval(() => {
      if (latestState?.running) {
        const currentElapsed = Date.now() - runStartedAt;
        els.activityElapsed.textContent = formatElapsed(currentElapsed);
        els.activityStatus.title = `${friendlyStep(latestState.current_step)}. Elapsed ${formatElapsed(currentElapsed)}.`;
      }
    }, 1000);
  } else if (!state.running && activityTimer) {
    window.clearInterval(activityTimer);
    activityTimer = null;
  }
}

function friendlyStep(step) {
  const value = String(step || "Idle").replace(/\s+\(\d+\/\d+\)$/, "");
  if (value === "Idle") return "Idle";
  if (value.startsWith("Typing")) return "Preparing prompt";
  if (value.startsWith("Waiting for approval")) return "Waiting for approval";
  if (value.startsWith("Waiting")) return "Waiting for Copilot";
  if (value.startsWith("Capturing")) return "Capturing response";
  if (value.startsWith("Recovering")) return "Recovering connection";
  if (value.startsWith("Refreshing chat")) return "Refreshing chat";
  if (value.startsWith("Drafting plan")) return "Drafting plan";
  if (value.startsWith("Replanning")) return "Updating plan";
  if (value.startsWith("Running script")) return "Running script";
  if (value.startsWith("Running command")) return "Running command";
  if (value.startsWith("Recording result")) return "Recording result";
  if (value.startsWith("Saving")) return "Saving result";
  return value;
}

function formatElapsed(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function applyLiveEvent(event) {
  if (!latestState || !event || !event.type) {
    fetchState().catch((error) => addLocalEvent("state_error", error.message));
    return;
  }
  if (event.id && Number(event.id) <= Number(latestState.event_seq || 0)) return;

  const started = performance.now();
  latestState.event_seq = Math.max(Number(latestState.event_seq || 0), Number(event.id || 0));
  latestState.events = [...(latestState.events || []), event].slice(-500);
  applyEventToState(latestState, event);
  renderRunChrome(latestState);
  if (TRANSCRIPT_EVENT_TYPES.has(event.type)) {
    renderTranscript(latestState);
  }
  renderEvents(latestState.events);
  const duration = performance.now() - started;
  if (duration >= 25) {
    console.debug("[ShellPilot] ui_live_event", { type: event.type, duration_ms: Math.round(duration) });
  }
}

function applyEventToState(state, event) {
  const payload = event.payload || {};
  switch (event.type) {
    case "run_started":
      state.running = true;
      runStartedAt = Date.now();
      lastRunDurationMs = 0;
      state.current_turn = 0;
      state.current_step = "Starting";
      state.active_project_id = payload.project_id || state.active_project_id;
      state.active_session_id = payload.session_id || state.active_session_id;
      state.workspace_dir = payload.workspace_dir || state.workspace_dir;
      state.run_folder = payload.run_folder || state.run_folder;
      state.approval_mode = payload.approval_mode || state.approval_mode;
      state.shell_kind = payload.shell_kind || state.shell_kind;
      state.run_mode = payload.run_mode || state.run_mode || "direct";
      state.plan_state = null;
      state.pending_plan = null;
      state.active_session = {
        ...(state.active_session || {}),
        session_id: state.active_session_id,
        project_id: state.active_project_id,
        title: payload.session_title || state.active_session?.title || "New chat",
        status: "running",
      };
      showAllTranscript = false;
      break;
    case "session_status":
      state.session_status = payload.status || state.session_status;
      break;
    case "turn_started":
      state.current_turn = Number(payload.turn || state.current_turn || 0);
      break;
    case "step":
      state.current_step = payload.step || state.current_step;
      break;
    case "approval_required":
      state.pending_approval = payload;
      break;
    case "approval_answered":
      state.pending_approval = null;
      break;
    case "plan_proposed":
      state.plan_state = payload.plan || state.plan_state;
      break;
    case "plan_approval_required":
      state.plan_state = payload.plan || state.plan_state;
      state.pending_plan = payload;
      break;
    case "plan_answered":
      state.pending_plan = null;
      break;
    case "plan_approved":
      state.plan_state = payload.plan || state.plan_state;
      state.pending_plan = null;
      break;
    case "plan_task_started":
    case "plan_task_updated":
      state.plan_state = payload.plan || state.plan_state;
      break;
    case "plan_replan_required":
      state.current_step = "Replanning";
      break;
    case "plan_completed":
      state.plan_state = payload.plan || state.plan_state;
      state.current_step = "Plan complete";
      state.pending_plan = null;
      break;
    case "plan_rejected":
      if (payload.plan) state.plan_state = {...payload.plan, status:"rejected", reason:payload.reason || "Plan rejected."};
      state.pending_plan = null;
      state.current_step = "Plan rejected";
      break;
    case "plan_error":
      state.current_step = "Error";
      state.pending_plan = null;
      break;
    case "turn_result":
      state.current_turn = Number(payload.turn || state.current_turn || 0);
      state.latest_command = payload.decision || null;
      state.latest_result = payload.command_result || null;
      break;
    case "done":
      state.current_step = "Done";
      state.pending_approval = null;
      break;
    case "turn_error":
    case "run_error":
      state.current_step = "Error";
      state.pending_approval = null;
      break;
    case "stopped":
      state.current_step = "Stopped";
      state.pending_approval = null;
      break;
    case "max_turns":
      state.current_step = "Max turns";
      state.pending_approval = null;
      break;
    case "run_finished":
      state.running = false;
      if (runStartedAt) lastRunDurationMs = Date.now() - runStartedAt;
      state.current_step = "Idle";
      state.pending_approval = null;
      break;
    case "new_session":
      state.running = false;
      runStartedAt = 0;
      lastRunDurationMs = 0;
      state.current_turn = 0;
      state.current_step = "Idle";
      state.pending_approval = null;
      state.pending_plan = null;
      state.plan_state = null;
      state.run_mode = "direct";
      state.active_project_id = payload.project_id || state.active_project_id;
      state.active_session_id = payload.session_id || state.active_session_id;
      state.run_folder = payload.run_folder || state.run_folder;
      state.events = [event];
      showAllTranscript = false;
      break;
    default:
      break;
  }
}

function renderHeader(state) {
  const project = (state.projects || []).find((item) => item.project_id === state.active_project_id);
  const session = state.active_session || (state.sessions || []).find((item) => item.session_id === state.active_session_id);
  const projectTitle = project ? project.title || "Project" : "ShellPilot";
  els.activeProjectTitle.textContent = projectTitle;
  els.composerProjectName.textContent = projectTitle;
  els.composerProject.title = state.workspace_dir || "";
  const parts = [];
  if (session) parts.push(session.title || "New chat");
  if (state.workspace_dir) parts.push(state.workspace_dir);
  els.activeSessionTitle.textContent = parts.join(" · ") || "Choose a project or start a new chat.";
}

function renderProjects(state) {
  const projects = state.projects || [];
  const activeProjectId = state.active_project_id || "";
  const activeSessionId = state.active_session_id || "";
  const projectSessions = state.project_sessions || {};
  els.projectList.innerHTML = "";
  if (!projects.length) {
    els.projectList.appendChild(emptyRow("No projects yet"));
    return;
  }
  for (const project of projects) {
    const wrapper = document.createElement("div");
    const isCollapsed = collapsedProjects.has(project.project_id);
    wrapper.className = `project-group ${project.project_id === activeProjectId ? "active" : ""} ${isCollapsed ? "collapsed" : ""}`;

    const projectRow = document.createElement("div");
    projectRow.className = "project-row";

    const toggleButton = document.createElement("button");
    toggleButton.type = "button";
    toggleButton.className = "project-toggle";
    toggleButton.textContent = isCollapsed ? "▸" : "▾";
    toggleButton.title = isCollapsed ? "Show chats" : "Hide chats";
    toggleButton.setAttribute("aria-label", `${isCollapsed ? "Show" : "Hide"} chats for ${project.title || "project"}`);
    toggleButton.setAttribute("aria-expanded", String(!isCollapsed));
    toggleButton.addEventListener("click", () => {
      if (collapsedProjects.has(project.project_id)) collapsedProjects.delete(project.project_id);
      else collapsedProjects.add(project.project_id);
      persistCollapsedProjects();
      renderProjects(latestState || state);
    });

    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-button";
    const sessions = projectSessions[project.project_id] || [];
    const sessionCountLabel = sessions.length >= 8 ? "8+" : sessions.length ? String(sessions.length) : "";
    button.innerHTML = `<span class="project-icon">▣</span><span class="item-title"></span><span class="item-count"></span>`;
    button.querySelector(".item-title").textContent = project.title || "Project";
    button.querySelector(".item-count").textContent = sessionCountLabel;
    button.title = project.workspace_path || "";
    button.addEventListener("click", () => selectProject(project.project_id));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "delete-button";
    deleteButton.title = "Delete project";
    deleteButton.textContent = "×";
    deleteButton.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteProject(project);
    });

    projectRow.append(toggleButton, button, deleteButton);
    wrapper.appendChild(projectRow);

    const sessionList = document.createElement("div");
    sessionList.className = `nested-session-list ${isCollapsed ? "collapsed" : ""}`;
    if (!sessions.length && project.project_id === activeProjectId) {
      sessionList.appendChild(emptyRow("No chats yet"));
    }
    for (const session of sessions) {
      const sessionRow = document.createElement("div");
      sessionRow.className = `session-row ${session.session_id === activeSessionId ? "active" : ""}`;

      const sessionButton = document.createElement("button");
      sessionButton.type = "button";
      sessionButton.className = "session-button";
      sessionButton.innerHTML = `<span class="item-title"></span><span class="item-time"></span>`;
      sessionButton.querySelector(".item-title").textContent = session.title || "New chat";
      sessionButton.querySelector(".item-time").textContent = relativeDate(session.updated_at);
      sessionButton.addEventListener("click", () => loadSession(session.session_id));

      const deleteSessionButton = document.createElement("button");
      deleteSessionButton.type = "button";
      deleteSessionButton.className = "delete-button";
      deleteSessionButton.title = "Delete chat";
      deleteSessionButton.textContent = "×";
      deleteSessionButton.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSession(session);
      });

      sessionRow.append(sessionButton, deleteSessionButton);
      sessionList.appendChild(sessionRow);
    }
    wrapper.appendChild(sessionList);
    els.projectList.appendChild(wrapper);
  }
}

function renderTranscript(state) {
  const events = (state.events || []).filter((event) => TRANSCRIPT_EVENT_TYPES.has(event.type));
  const runStart = events.find((event) => event.type === "run_started");
  const detailEvents = events.filter((event) => event !== runStart);
  const hiddenCount = Math.max(0, detailEvents.length - MAX_VISIBLE_TURNS);
  const visibleEvents = showAllTranscript ? detailEvents : detailEvents.slice(-MAX_VISIBLE_TURNS);
  const wasNearBottom = els.chatTranscript.scrollHeight - els.chatTranscript.scrollTop - els.chatTranscript.clientHeight < 120;
  els.chatTranscript.innerHTML = "";

  if (!events.length) {
    const welcome = document.createElement("article");
    welcome.className = "chat-message assistant";
    welcome.innerHTML = `
      <div class="avatar-bubble">SP</div>
      <div class="message-body">
        <div class="message-card">
          <h3>Start a ShellPilot chat</h3>
          <p>Pick a workspace, choose a shell and approval mode, then send a task. Each chat is saved under the selected project.</p>
        </div>
      </div>
    `;
    els.chatTranscript.appendChild(welcome);
    return;
  }

  if (runStart) {
    appendEventMessage(runStart, state.pending_approval);
  }
  if (hiddenCount && !showAllTranscript) {
    addTranscriptHistoryToggle(hiddenCount);
  }
  for (const event of visibleEvents) {
    appendEventMessage(event, state.pending_approval);
  }
  if (wasNearBottom || !state.running) {
    els.chatTranscript.scrollTop = els.chatTranscript.scrollHeight;
  }
}

function addTranscriptHistoryToggle(hiddenCount) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "transcript-history-toggle";
  button.textContent = `Show ${hiddenCount} older turn${hiddenCount === 1 ? "" : "s"}`;
  button.addEventListener("click", () => {
    showAllTranscript = true;
    renderTranscript(latestState || { events: [] });
  });
  els.chatTranscript.appendChild(button);
}

function appendEventMessage(event, pendingApproval) {
  const payload = event.payload || {};
  if (event.type === "run_started") {
    addMessage("user", "You", `
      <p>${escapeHtml(payload.task || "New task")}</p>
      <div class="meta-row">
        <span>${escapeHtml(payload.shell_kind || "")}</span>
        <span>${escapeHtml(payload.approval_mode || "")}</span>
        <span title="${escapeHtml(payload.workspace_dir || "")}">${escapeHtml(shortPath(payload.workspace_dir || ""))}</span>
      </div>
    `);
    return;
  }

  if (event.type === "turn_result") {
    addTurnResult(payload);
    return;
  }

  if (event.type === "approval_required") {
    const isPending = pendingApproval && pendingApproval.id === payload.id;
    addMessage("assistant", "Approval", renderApprovalCard(payload, isPending));
    return;
  }

  if (event.type === "approval_answered") {
    addSystemMessage(payload.approved ? "Approved command." : "Denied command.");
    return;
  }

  if (event.type === "plan_replan_required") {
    addSystemMessage("A task was blocked. ShellPilot is drafting a replacement plan.");
    return;
  }

  if (event.type === "plan_rejected") {
    addSystemMessage("Plan rejected. No commands were run from that plan.");
    return;
  }

  if (event.type === "plan_error") {
    addMessage("assistant error", "Plan error", `<pre>${escapeHtml(payload.error || "Could not create a plan.")}</pre>`);
    return;
  }

  if (event.type === "plan_completed") {
    addSystemMessage("Plan complete.");
    return;
  }

  if (event.type === "done") {
    const reason = (payload.decision && payload.decision.reason) || "Task complete.";
    addMessage("assistant", "ShellPilot", `<p>${escapeHtml(reason)}</p>`);
    return;
  }

  if (event.type === "turn_error" || event.type === "run_error") {
    addMessage("assistant error", "Error", `<pre>${escapeHtml(payload.error || summarizePayload(payload))}</pre>`);
    return;
  }

  if (event.type === "stopped" || event.type === "max_turns") {
    addSystemMessage(summarizePayload(payload));
    return;
  }

  if (event.type === "new_session") {
    addSystemMessage(`New chat created${payload.copilot_new_chat ? " and Copilot thread reset" : ""}.`);
    return;
  }

  if (event.type === "project_selected" || event.type === "session_loaded") {
    addSystemMessage(event.type === "project_selected" ? "Project selected." : "Chat loaded.");
  }
}

function addTurnResult(turn) {
  const decision = turn.decision || {};
  const result = turn.command_result || {};
  const gitBefore = turn.git_before || {};
  const gitAfter = turn.git_after || {};
  const decisionText = formatDecisionText(decision);

  addMessage("assistant", "Copilot command", `
    <div class="command-card">
      <div class="command-header">
        <span class="risk ${escapeHtml(result.computed_risk || decision.risk || "read_only")}">${escapeHtml(result.computed_risk || decision.risk || "read_only")}</span>
        <span>${escapeHtml(decision.reason || "No reason provided.")}</span>
      </div>
      <pre>${escapeHtml(decisionText || "(no command)")}</pre>
    </div>
  `);

  addMessage("tool", "Command result", `
    <div class="result-summary">
      <span class="${result.ok ? "ok-text" : "warn-text"}">${escapeHtml(result.skipped ? "Skipped" : result.ok ? "Completed" : "Failed")}</span>
      <span>exit ${valueOrDash(result.exit_code)}</span>
      <span>${formatDuration(result.duration_s)}</span>
      <span>${escapeHtml(result.shell || "")}</span>
    </div>
    <pre class="terminal-output">${escapeHtml(renderTerminal(result))}</pre>
    ${result.stderr || result.skip_reason ? `<pre class="terminal-output stderr">${escapeHtml(result.stderr || result.skip_reason)}</pre>` : ""}
  `);

  addMessage("tool compact", "Git", `
    <div class="git-grid">
      <div>
        <strong>Before</strong>
        <pre>${escapeHtml(formatGit(gitBefore))}</pre>
      </div>
      <div>
        <strong>After</strong>
        <pre>${escapeHtml(formatGit(gitAfter))}</pre>
      </div>
    </div>
  `);
}

function addMessage(kind, label, html) {
  const article = document.createElement("article");
  article.className = `chat-message ${kind}`;
  article.innerHTML = `
    <div class="avatar-bubble">${kind.includes("user") ? "You" : kind.includes("tool") ? "sh" : "SP"}</div>
    <div class="message-body">
      <div class="message-label">${escapeHtml(label)}</div>
      <div class="message-card">${html}</div>
    </div>
  `;
  els.chatTranscript.appendChild(article);
}

function addSystemMessage(text) {
  const row = document.createElement("div");
  row.className = "system-message";
  row.textContent = text || "";
  els.chatTranscript.appendChild(row);
}

function renderApprovalCard(payload, isPending) {
  const decision = payload.decision || {};
  const assessment = payload.assessment || {};
  const buttons = isPending
    ? `<div class="inline-actions"><button class="danger" data-deny="${escapeHtml(payload.id)}">Deny</button><button data-approve="${escapeHtml(payload.id)}">Approve</button></div>`
    : "";
  return `
    <p>${escapeHtml(assessment.reason || decision.reason || "Approval required.")}</p>
    <pre>${escapeHtml(formatDecisionText(decision))}</pre>
    ${buttons}
  `;
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

function renderApproval(pending) {
  if (!pending) {
    els.approvalPanel.classList.add("hidden");
    return;
  }
  const decision = pending.decision || {};
  const assessment = pending.assessment || {};
  els.approvalPanel.classList.remove("hidden");
  els.approvalReason.textContent = `${assessment.risk || "risk"}: ${assessment.reason || decision.reason || ""}`;
  els.approvalCommand.textContent = formatDecisionText(decision);
  els.approveBtn.dataset.id = pending.id;
  els.denyBtn.dataset.id = pending.id;
}

function selectedRunMode() {
  const active = els.runModeToggle?.querySelector("button[aria-pressed='true']");
  return active?.dataset.runMode || "direct";
}

function renderRunMode(mode, running) {
  const selected = mode === "plan" ? "plan" : "direct";
  for (const button of els.runModeToggle?.querySelectorAll("button") || []) {
    const active = button.dataset.runMode === selected;
    button.setAttribute("aria-pressed", String(active));
    button.disabled = Boolean(running);
  }
  els.runModeHelp.textContent = selected === "plan"
    ? "Draft a checklist, approve it once, then execute task by task."
    : "Run one safe next command at a time.";
}

function renderPlan(state) {
  const pending = state.pending_plan;
  const plan = pending?.plan || state.plan_state;
  if (!plan || !Array.isArray(plan.tasks) || !plan.tasks.length) {
    els.planPanel.classList.add("hidden");
    return;
  }

  els.planPanel.classList.remove("hidden");
  const isPending = Boolean(pending);
  const completed = plan.tasks.filter((task) => task.status === "completed").length;
  const active = plan.tasks.find((task) => task.status === "in_progress");
  const status = isPending
    ? "Review and approve this checklist before execution."
    : plan.status === "rejected"
      ? "Plan rejected. No commands were run from it."
    : plan.status === "completed"
      ? "All planned tasks completed."
      : active
        ? `Task ${plan.tasks.indexOf(active) + 1} of ${plan.tasks.length} is in progress.`
        : `${completed} of ${plan.tasks.length} tasks completed.`;
  els.planTitle.textContent = isPending ? "Execution plan ready" : "Execution plan";
  els.planStatus.textContent = status;
  els.planRevision.textContent = `Revision ${plan.revision || 1}`;
  els.planTasks.innerHTML = "";

  for (const [index, task] of plan.tasks.entries()) {
    const item = document.createElement("li");
    const statusClass = task.status || "pending";
    item.className = `plan-task ${statusClass}`;
    const icon = task.status === "completed" ? "✓" : task.status === "in_progress" ? "•" : task.status === "blocked" ? "!" : "";
    item.innerHTML = `
      <span class="plan-task-icon" aria-hidden="true">${icon}</span>
      <span class="plan-task-copy"><strong></strong><small></small></span>
      <span class="plan-task-state"></span>
    `;
    item.querySelector("strong").textContent = task.title || `Task ${index + 1}`;
    item.querySelector("small").textContent = task.detail || "";
    item.querySelector("small").classList.toggle("hidden", !task.detail);
    item.querySelector(".plan-task-state").textContent = task.status === "in_progress"
      ? "Working"
      : task.status === "completed"
        ? "Done"
        : task.status === "blocked"
          ? "Blocked"
          : "Next";
    els.planTasks.appendChild(item);
  }

  els.planActions.classList.toggle("hidden", !isPending);
  els.planApproveBtn.disabled = !isPending;
  els.planRejectBtn.disabled = !isPending;
  els.planApproveBtn.dataset.id = pending?.id || "";
  els.planRejectBtn.dataset.id = pending?.id || "";
  els.planApproveBtn.dataset.revision = pending?.revision || plan.revision || "";
  els.planRejectBtn.dataset.revision = pending?.revision || plan.revision || "";
}

function renderEvents(events) {
  els.eventLog.innerHTML = "";
  const rows = [...events].reverse().filter((event) => event.type !== "turn_result").slice(0, 50);
  if (!rows.length) {
    els.eventLog.appendChild(emptyRow("No logs yet"));
    return;
  }
  for (const event of rows) {
    const row = document.createElement("div");
    row.className = "event";
    row.innerHTML = `<span class="event-type"></span><span class="event-body"></span>`;
    row.querySelector(".event-type").textContent = event.type || "";
    row.querySelector(".event-body").textContent = summarizePayload(event.payload || {});
    els.eventLog.appendChild(row);
  }
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

function renderTerminal(result) {
  if (!result || !result.command) return "";
  const promptPrefix = result.shell === "cmd" ? ">" : result.shell === "powershell" ? "PS>" : "$";
  const output = result.stdout || "";
  return `${promptPrefix} ${result.command}${output ? "\n" + output : ""}`;
}

function formatGit(git) {
  if (!git || Object.keys(git).length === 0) return "(none)";
  if (!git.is_git_repo) return git.error || "Not a Git repository";
  const chunks = [`branch: ${git.branch || "unknown"}`, git.status_short || "(clean)"];
  if (git.diff_stat) chunks.push(`diff:\n${git.diff_stat}`);
  if (git.diff_name_status) chunks.push(`name-status:\n${git.diff_name_status}`);
  if (git.staged_name_status) chunks.push(`staged:\n${git.staged_name_status}`);
  return chunks.filter(Boolean).join("\n\n");
}

function summarizePayload(payload) {
  if (payload.line) return payload.line;
  if (payload.error) return payload.error;
  if (payload.step) return payload.step;
  if (payload.signal) return `${payload.signal} (${payload.count || 0}/${payload.threshold || 0})`;
  if (payload.reason && payload.result) return `chat refreshed: ${payload.reason}`;
  if (payload.excerpt) return payload.excerpt;
  if (payload.task) return payload.task;
  if (payload.run_folder) return payload.run_folder;
  if (payload.response_excerpt) return payload.response_excerpt;
  if (payload.decision && formatDecisionText(payload.decision)) return formatDecisionText(payload.decision);
  if (payload.command_result && payload.command_result.command) return payload.command_result.command;
  return JSON.stringify(payload);
}

function formatDecisionText(decision) {
  if (!decision) return "";
  if (decision.command) return decision.command;
  if (Array.isArray(decision.script_lines)) return decision.script_lines.join("\n");
  return "";
}

function emptyRow(text) {
  const row = document.createElement("div");
  row.className = "empty-row";
  row.textContent = text;
  return row;
}

function relativeDate(value) {
  if (!value) return "now";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const delta = Date.now() - date.getTime();
  if (delta < 60_000) return "now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h ago`;
  return date.toLocaleDateString();
}

function shortPath(value) {
  const text = String(value || "");
  if (text.length <= 54) return text;
  const parts = text.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 3) return `...${text.slice(-48)}`;
  return `.../${parts.slice(-3).join("/")}`;
}

function formatDuration(value) {
  if (value === null || value === undefined || value === "") return "-";
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return "-";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${seconds.toFixed(2)}s`;
}

function valueOrDash(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return entities[char];
  });
}

async function selectProject(projectId) {
  try {
    await postJson("/api/projects/select", { project_id: projectId });
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
}

async function browseWorkspace(path) {
  const data = await postJson("/api/browse_workspace", { path });
  browserPath = data.path || "";
  browserParent = data.parent || "";
  browserHome = data.home || "";
  renderWorkspaceBrowser(data);
}

function renderWorkspaceBrowser(data) {
  els.workspaceBrowserPath.textContent = data.path || "";
  els.browseParentBtn.disabled = !data.parent;
  els.browseHomeBtn.disabled = !data.home || data.home === data.path;
  els.workspaceBrowserRoots.innerHTML = "";
  els.workspaceBrowserList.innerHTML = "";

  for (const root of data.roots || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "browser-root";
    button.textContent = root;
    button.addEventListener("click", () => browseWorkspace(root).catch((error) => addLocalEvent("error", error.message)));
    els.workspaceBrowserRoots.appendChild(button);
  }

  const entries = data.entries || [];
  if (!entries.length) {
    els.workspaceBrowserList.appendChild(emptyRow(data.truncated ? "No readable folders in first results." : "No subfolders."));
    return;
  }

  for (const entry of entries) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "browser-entry";
    button.innerHTML = `<span>□</span><span></span>`;
    button.querySelector("span:last-child").textContent = entry.name || entry.path;
    button.title = entry.path;
    button.addEventListener("click", () => browseWorkspace(entry.path).catch((error) => addLocalEvent("error", error.message)));
    els.workspaceBrowserList.appendChild(button);
  }
}

function openWorkspaceBrowser() {
  els.workspaceBrowser.classList.remove("hidden");
  browseWorkspace((latestState && latestState.workspace_dir) || ".").catch((error) => addLocalEvent("error", error.message));
}

function closeWorkspaceBrowser() {
  els.workspaceBrowser.classList.add("hidden");
}

async function useBrowserWorkspace() {
  if (!browserPath) return;
  closeWorkspaceBrowser();
  await selectWorkspace(browserPath);
}

async function deleteSession(session) {
  const title = session.title || "this chat";
  if (!window.confirm(`Delete "${title}"? This removes its local ShellPilot artifacts.`)) return;
  try {
    await postJson("/api/session/delete", {
      project_id: session.project_id,
      session_id: session.session_id,
    });
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
}

async function deleteProject(project) {
  const title = project.title || "this project";
  if (!window.confirm(`Delete project "${title}" and all of its local chats? The workspace files are not deleted.`)) return;
  try {
    await postJson("/api/projects/delete", { project_id: project.project_id });
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
}

async function selectWorkspace(path) {
  try {
    await postJson("/api/projects/select", { workspace_dir: path });
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
}

async function loadSession(sessionId) {
  try {
    await getJson(`/api/session/${encodeURIComponent(sessionId)}`);
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
}

function wireEvents() {
  const source = new EventSource("/events");
  source.addEventListener("message", (messageEvent) => {
    try {
      applyLiveEvent(JSON.parse(messageEvent.data));
    } catch (error) {
      addLocalEvent("event_error", `Could not process live event: ${error.message}`);
    }
  });
  source.onerror = () => {
    const now = Date.now();
    if (now - lastReconnectNotice > 5000) {
      lastReconnectNotice = now;
      addLocalEvent("events", "Reconnecting to event stream...");
    }
    if (reconnectRefreshTimer) return;
    reconnectRefreshTimer = window.setTimeout(() => {
      reconnectRefreshTimer = null;
      fetchState().catch((error) => addLocalEvent("state_error", error.message));
    }, 750);
  };
}

function addLocalEvent(type, message) {
  if (!latestState) return;
  latestState.events = latestState.events || [];
  latestState.events.push({ ts: new Date().toISOString().slice(0, 19), type, payload: { line: message } });
  latestState.events = latestState.events.slice(-500);
  renderEvents(latestState.events);
}

els.openBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/open_copilot", formPayload());
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.checkBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/check_session", {});
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.runBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/run", formPayload());
    els.taskInput.value = "";
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.stopBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/stop", {});
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
  }
});

els.newProjectBtn.addEventListener("click", openWorkspaceBrowser);
els.browseCloseBtn.addEventListener("click", closeWorkspaceBrowser);
els.browseHomeBtn.addEventListener("click", () => {
  if (browserHome) browseWorkspace(browserHome).catch((error) => addLocalEvent("error", error.message));
});
els.browseParentBtn.addEventListener("click", () => {
  if (browserParent) browseWorkspace(browserParent).catch((error) => addLocalEvent("error", error.message));
});
els.browseUseBtn.addEventListener("click", () => {
  useBrowserWorkspace().catch((error) => addLocalEvent("error", error.message));
});
els.workspaceBrowser.addEventListener("click", (event) => {
  if (event.target === els.workspaceBrowser) closeWorkspaceBrowser();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !els.workspaceBrowser.classList.contains("hidden")) closeWorkspaceBrowser();
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !els.runBtn.disabled) {
    els.runBtn.click();
  }
});

els.themeToggle.addEventListener("click", toggleTheme);

els.approveBtn.addEventListener("click", async () => {
  const id = els.approveBtn.dataset.id;
  if (!id) return;
  await postJson("/api/approval", { id, approved: true });
  await fetchState();
});

els.denyBtn.addEventListener("click", async () => {
  const id = els.denyBtn.dataset.id;
  if (!id) return;
  await postJson("/api/approval", { id, approved: false });
  await fetchState();
});

els.runModeToggle.addEventListener("click", (event) => {
  const button = event.target.closest("[data-run-mode]");
  if (!button || button.disabled) return;
  for (const item of els.runModeToggle.querySelectorAll("button")) {
    item.setAttribute("aria-pressed", String(item === button));
  }
});

async function answerPlan(action, button) {
  const id = button?.dataset.id;
  if (!id) return;
  button.disabled = true;
  try {
    await postJson("/api/plan", {
      id,
      action,
      revision: Number(button.dataset.revision || 0),
    });
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
    await fetchState().catch(() => {});
  }
}

els.planApproveBtn.addEventListener("click", () => answerPlan("approve", els.planApproveBtn));
els.planRejectBtn.addEventListener("click", () => answerPlan("reject", els.planRejectBtn));

els.chatTranscript.addEventListener("click", async (event) => {
  const approve = event.target.closest("[data-approve]");
  const deny = event.target.closest("[data-deny]");
  if (!approve && !deny) return;
  const id = approve ? approve.dataset.approve : deny.dataset.deny;
  await postJson("/api/approval", { id, approved: Boolean(approve) });
  await fetchState();
});

els.newSessionBtn.addEventListener("click", async () => {
  try {
    await postJson("/api/session/new", {});
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
    await postJson("/api/approval_mode", { approval_mode: mode });
    await fetchState();
  } catch (error) {
    addLocalEvent("error", error.message);
    if (latestState) renderApprovalMode(latestState.approval_mode || "ask", latestState.running);
  }
});

initializeTheme();
fetchState().then(wireEvents).catch((error) => addLocalEvent("error", error.message));
