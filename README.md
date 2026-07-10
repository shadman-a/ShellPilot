# ShellPilot

ShellPilot is a local web GUI for a single-agent command cockpit powered by Microsoft 365 Copilot web chat.

It is based on the working SuperPilot proof of concept, but narrowed down:

- Same real Copilot browser automation backend with Playwright.
- Same persistent browser profile idea, so Microsoft login, SSO, and MFA happen manually in the browser and are reused.
- No multi-agent orchestration, fake IDE, vector database, file-generation workflow, or broad tool protocol.
- Direct mode uses one Copilot turn per local command. Optional Plan first mode creates a bounded checklist, waits for approval, and then executes one task at a time.
- ShellPilot risk-checks the command, applies the selected approval mode, captures the result, records Git state, and sends the result back to Copilot.
- Each selected workspace path is treated as a project, and each run/new session is saved as a chat under that project.

## Install

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

## Run

```bash
python3 shellpilot.py
```

The app opens a local browser page. If it does not open automatically, use the printed URL, usually:

```text
http://127.0.0.1:8765/
```

## First Use

1. Set the workspace path.
   - Use **New Project** in the sidebar to pick a local folder with ShellPilot's built-in folder browser.
   - ShellPilot creates/selects a local project for that exact path.
2. Click **Open Copilot / Login**.
3. Complete Microsoft sign-in manually in the Playwright browser window.
4. Click **Check Session**.
5. Enter a task and click **Run**.

Use **Approval mode** beside the task prompt to choose how much ShellPilot asks before running commands. **New Chat** creates a new local chat under the current project, clears the active transcript, and starts a fresh Copilot chat thread if Copilot is open while keeping the browser login/profile intact.

Use **Plan first** beside the task prompt when the work has multiple dependent steps. ShellPilot shows up to six ordered tasks with an active loader and completion checkmarks, waits for one checklist approval, and keeps the existing command risk and approval rules for every execution. Rejecting a plan stops the run without executing it. Failed or blocked tasks pause execution and trigger a replacement plan proposal for the remaining work.

For longer runs, **Refresh chat every** starts a fresh Copilot chat after the selected number of turns while preserving ShellPilot's local task, Git state, and previous command result in the next prompt. The default is 10 turns to avoid slow or stuck Copilot threads during extended sessions.

The left sidebar works like Codex:

- **Projects** are selected workspace paths.
- **Chats** are saved ShellPilot sessions for the active project.
- Selecting an old chat reloads its saved turns, command results, Git summaries, and logs.
- Deleting a chat or project removes only ShellPilot's local saved artifacts. It does not delete workspace files.
- The composer shows the active project name. Project switching happens from the sidebar.

## Safety

- ShellPilot uses browser UI automation only. It does not use Microsoft APIs.
- It does not bypass authentication, MFA, CAPTCHAs, or enterprise controls.
- Do not use it if your organization disallows browser automation.
- The command shell is selectable in **Session settings**: Bash, PowerShell, or Windows cmd.
- On Windows, the default shell is PowerShell. On macOS/Linux, the default shell is Bash.
- Bash uses a non-login shell so commands inherit the app process `PATH`.
- PowerShell runs with `-NoProfile -ExecutionPolicy Bypass -Command`.
- Windows cmd runs with `/d /s /c`.
- Default mode is **Ask for approval**: only locally classified `read_only` commands run automatically.
- **Approve for me** auto-runs `read_only`, `write_file`, and `network`; it still asks for `dangerous`.
- **Full access** auto-runs every locally classified risk level, including `dangerous`.
- Computed local risk wins over Copilot's declared risk.
- Write commands are refused until at least one successful read-only inspection command has run, except in **Full access**.
- Malformed multi-command strings are still rejected in every mode.

## Artifacts

ShellPilot keeps artifacts in its own app folder, not inside the workspace repo:

```text
/Users/shadman/Documents/ShellPilot/.shellpilot/projects/<project_id>/sessions/<session_id>/
```

No `.shellpilot` folder should be created inside the target workspace.

Artifacts:

- `session.json`
- `turns.jsonl`
- `logs/events.log`
- `copilot_responses/`
- `screenshots/`

## Development Checks

```bash
python3 -m compileall shellpilot tests
python3 -m unittest discover -s tests
```
