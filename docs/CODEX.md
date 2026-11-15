# Codex on Windows

This fork exposes the existing bot through standard MCP over stdio. Codex sends
tool requests; Python runs enabled background actions. No Anthropic API or Claude
subscription is required. The integration does not make the game automation
compatible with a new client version automatically.

## Install

Use native Windows Python 3.11+ (3.12 recommended), Node.js 22.12+ and an installed,
signed-in Codex CLI. Start with a normal, non-elevated PowerShell terminal. The
bot and game should run under the same Windows account and integrity level.

From the repository root:

```powershell
.\scripts\setup-codex.ps1
.\scripts\start-codex.ps1
```

The setup script creates `venv`, installs the pinned direct Python dependencies
(including PyCryptodome), runs `npm ci` and a production dashboard build, then
registers `dbvictory-bot` with Codex using absolute paths. It does not launch the
game, patch memory, change PowerShell policy, or elevate privileges. Review the
script before running it. If script execution is blocked, follow your own
Windows policy; the equivalent manual steps are below.

`-PythonCommand C:\Path\To\python.exe` selects a Python installation.
`-SkipDashboard` skips Node installation/build for MCP-only setup. Move the
repository later? Run setup again to register the new paths. Registration uses
the server name `dbvictory-bot` and replaces any existing registration with that
name; other MCP servers are unaffected.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Push-Location dashboard
npm ci
npm run build
Pop-Location
codex mcp add dbvictory-bot --env PYTHONUTF8=1 --env PYTHONUNBUFFERED=1 -- "$PWD\venv\Scripts\python.exe" "$PWD\run_mcp.py"
codex mcp list
codex
```

For manual TOML configuration, merge `config/codex.example.toml` into your Codex
configuration after changing the example paths. Do not replace the entire file.
The repository's legacy `.mcp.json` is for Claude Code, not Codex.

Official MCP configuration reference: https://developers.openai.com/codex/mcp

## Verify without connecting to the game

In Codex, use `/mcp` to check that `dbvictory-bot` is available. Ask:

> Use only get_status and list_actions. Do not start the bot or enable actions.

The status should show a running MCP server and an inactive proxy. No game client
is needed for this check. Starting MCP alone does not launch the dashboard.

For automated checks (also supported on Linux):

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest -q
```

The suite includes a real MCP stdio handshake from a different working directory,
read-only status calls, HTTP authorization checks, and WebSocket authentication.
It does not inject the DLL or log into the game.

## Windows acceptance test — still required

1. Build `dll/dbvbot.dll` from source with a 32-bit MinGW toolchain (`cd dll; make`),
   as described in the main README. Review the existing build scripts before use;
   the Codex setup script does not install a compiler or download a prebuilt DLL.
2. Open `dbvStart.exe` at its login screen. Review `list_actions` and disable any
   previously saved actions you don't want to run. Fresh installs enable no user
   actions; the internal DLL bridge starts when the game connects.
3. Ask Codex to call `start_bot`. This changes the running client's memory and
   starts loopback proxies and the Electron panel. It returns while waiting for
   login in the background (up to 120 seconds).
4. Enter credentials in the game client only. Check proxy, player and DLL status.
   Do not paste account credentials into Codex or attach packet dumps containing
   personal data.
5. Enable one requested action, verify its effect, then disable it and confirm it
   stops. Check the dashboard receives state and its controls work.
6. Test reconnect/reset. It should stop only the dashboard processes launched by
   this MCP session; unrelated Electron applications must remain open.
7. Exit the MCP session normally, close the game, and restart the game to discard
   memory patches/DLL injection. Forced process termination may leave children
   running; stop only this bot's processes if recovery is needed.

If attachment fails, check the process name, client version, bitness and Windows
integrity levels. Do not solve it by routinely elevating the entire coding agent.
Some native operations may require elevation; privilege separation is not
implemented in this fork. WSL-only Python cannot attach through these Windows APIs.

## Dashboard and privacy changes

- Normal launches use a production Next.js build bound to `127.0.0.1:4747`.
- HTTP (`8089`) and WebSocket (`8090`) bind to loopback. HTTP requires a bearer
  token and validates Host and browser Origin. WebSocket requires the exact
  dashboard Origin and a token as its first message before sending any state.
- A fresh random token is generated per MCP process. It goes only to the launched
  Electron child through its environment, then to the trusted main frame via a
  narrow IPC bridge. It isn't embedded in the JavaScript bundle or put in URLs.
- Open the panel through `start_bot`. Ordinary browser access and separately
  launched Electron instances no longer receive a token and cannot control the
  bot. This is intentional. Restart the MCP session if you need a fresh token.
- Session keys are redacted from proxy logs. `packet_sniffer` logs only opcode and
  length by default. Raw diagnostic capture requires explicitly setting
  `DBV_ALLOW_PACKET_CAPTURE=1` before MCP startup; dumps can include private chat.
- These changes protect against unauthenticated local API calls and unrelated
  websites. They do not protect against malware already running as your Windows
  user, compromised trusted dependencies, or an incompatible game DLL.

Python direct dependencies and security-sensitive Node packages are pinned.
PostCSS, nanoid, source-map-js and sharp overrides keep the Next.js 15 dependency tree on patched versions; review these overrides when upgrading Next.js. Python transitive dependencies are not fully locked. Review updates regularly;
a clean dependency scan does not prove the whole program safe.
