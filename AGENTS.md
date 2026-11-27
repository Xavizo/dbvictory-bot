# Working on this project

- Each commit advances the displayed version. Use `feat(scope):` for a
  backwards-compatible feature and `type(scope)!:` or a `BREAKING CHANGE:`
  footer for an incompatible change. Fixes and other commits advance patch.

- This is a Windows game client integration, exposed through standard stdio MCP.
  `run_mcp.py` only starts MCP. Connecting to the game is an explicit `start_bot`
  tool call. Never use that call as a smoke test.
- Keep stdout reserved for MCP JSON-RPC. Send diagnostics to stderr. Never log
  credentials, session keys, dashboard tokens, or raw packets by default.
- Start with `get_status` and `list_actions`. Only enable actions the user requests;
  enabled actions persist in `bot_settings.json` and resume on game login.
- Do not enable packet payload capture unless explicitly requested for diagnosis.
  Do not commit user settings, logs, secrets, or recordings made during tests.
- Do not elevate the entire coding agent or disable its approval/sandbox controls
  as a routine setup step. Game attachment depends on Windows process permissions.
- Validate Python changes with `python -m pytest -q` after installing
  `requirements-dev.txt`. The MCP smoke test must work without a game or DLL.
- Validate dashboard changes with `npm test`, `npm run typecheck` and `npm run build` in
  `dashboard/`. Use the lockfile with `npm ci`; check `npm audit` after updates.
- The Electron dashboard receives a session token from the parent process. Keep
  HTTP and WebSocket authentication, exact Origin checks, loopback binding, and
  IPC sender validation intact. Do not expose the panel to the LAN or Internet.
- Linux checks do not establish Windows compatibility. Report DLL compilation,
  attachment, login, dashboard launch, and gameplay as untested until exercised on
  the matching Windows client. Offsets in this repository are client-specific.
