# ShellPilot

ShellPilot is a local web GUI for a single-agent command cockpit powered by Microsoft 365 Copilot web chat.

It is based on the working SuperPilot proof of concept, but narrowed down:

- Same real Copilot browser automation backend with Playwright.
- Same persistent browser profile idea, so Microsoft login, SSO, and MFA happen manually in the browser and are reused.
- No multi-agent planning, fake IDE, vector database, file-generation workflow, or broad tool protocol.
- One Copilot turn proposes exactly one Bash command.
- ShellPilot risk-checks the command, applies the selected approval mode, captures the result, records Git state, and sends the result back to Copilot.

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
2. Click **Open Copilot / Login**.
3. Complete Microsoft sign-in manually in the Playwright browser window.
4. Click **Check Session**.
5. Enter a task and click **Run**.

Use **Approval mode** beside the task prompt to choose how much ShellPilot asks before running commands. **New Session** clears the current task, run folder, command result, approval prompt, and live event log while keeping the Copilot browser login/profile intact.

## Safety

- ShellPilot uses browser UI automation only. It does not use Microsoft APIs.
- It does not bypass authentication, MFA, CAPTCHAs, or enterprise controls.
- Do not use it if your organization disallows browser automation.
- The MVP runs Bash for local testing on macOS. It uses a non-login shell so commands inherit the app process `PATH`.
- Default mode is **Ask for approval**: only locally classified `read_only` commands run automatically.
- **Approve for me** auto-runs `read_only`, `write_file`, and `network`; it still asks for `dangerous`.
- **Full access** auto-runs every locally classified risk level, including `dangerous`.
- Computed local risk wins over Copilot's declared risk.
- Write commands are refused until at least one successful read-only inspection command has run, except in **Full access**.
- Malformed multi-command strings are still rejected in every mode.

## Artifacts

Each run writes under:

```text
<workspace>/.shellpilot/runs/run_YYYYMMDD_HHMMSS/
```

Artifacts:

- `turns.jsonl`
- `logs/events.log`
- `copilot_responses/`
- `screenshots/`

## Development Checks

```bash
python3 -m compileall shellpilot tests
python3 -m unittest discover -s tests
```
