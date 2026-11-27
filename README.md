# DBVictory Bot

A desktop bot for the DBVictory game client (Windows). It runs next to the
official client, reads the game state from network traffic and client memory,
and provides a desktop panel for healing, combat, cavebot routes, a learned
world atlas, hunt statistics and alerts. It can also be controlled by an AI
coding agent (Codex or Claude Code) through MCP.

![Dashboard](docs/dashboard.png)

> **Read this first.** Using a bot breaks DBVictory's rules and can get your
> account banned. You use this software at your own risk.
>
> The bot only supports **specific builds of the game client**. When the server
> ships a new client, the bot refuses to attach until a matching profile is
> added. See [Supported client builds](#supported-client-builds).

## What it does

**Survival**
- Priority healing rules for HP and Ki (potions, senzu beans, healing spells)
- Barrier band and barrier spell upkeep, speed up, food
- Emergency stop and a global pause on the **Pause/Break** key

**Combat**
- Auto targeting with per-monster rules, attack spells and runes
- Spell conditions (cast only when the required monsters, HP or Ki are present)
- Area (wave) spells when several monsters are in range
- Hunter Knife: skins your own corpses and confirms the loot

**Cavebot and atlas**
- Record and replay hunting routes, including stairs, ladders, ropes, doors and digging
- Lure mode: keep walking until a group follows, then fight before corridors and floor changes
- A world atlas learned passively while you play: floors, transitions, hunt areas and monster sightings
- Atlas routes planned from what the atlas has learned, with a route replay viewer

**Panel**
- Live HP/Ki, position, nearby creatures, items, pets and the world map
- Session statistics (XP/h, kills, loot) and hunt comparisons
- Sound and tray alerts for players on screen and private messages
- Bot profiles per character, so each character keeps its own settings

## How it works

1. The **launcher** (`DBVictory Launcher.exe`, or the `Uruchom bota` shortcut)
   checks the installed version, opens the game client and prepares the engine.
2. You log in and stay on the **character list**, then click **Połącz z grą**
   (Connect to game). The click is your confirmation; the launcher does not take
   screenshots.
3. The engine checks the client's executable against a known profile. Unknown
   builds stop here, before anything is changed in the client.
4. The engine redirects the client's connection through a local proxy on
   `127.0.0.1`, reads the session key, and starts decoding the game traffic.
5. A small observer DLL is loaded into the client to read creatures and the map
   and to show the in-game target marker.
6. The **panel** (Electron) opens. It only listens on `127.0.0.1` and requires a
   per-session token. You enter the game and turn on the features you want.

All learned data (atlas, profiles, reports) stays on your computer in
`local_data/`. Nothing is uploaded anywhere.

```
Launcher ──► run_bot.py ──► MCP engine (mcp_server.py)
                              ├── proxy.py ◄──► game client ◄──► game server
                              ├── packet_observer.py → game_state.py
                              ├── passive_map.py / client_player_state.py (read-only memory)
                              ├── dll_bridge.py ◄── named pipe ◄── observer DLL (in client)
                              ├── actions/*.py (healing, combat, cavebot…)
                              └── dashboard_api.py ──► dashboard/ (Electron + Next.js)
```

## Installation

There is no ready-made download yet, so for now installation means building
from source on Windows.

### Requirements

- Windows 10 or 11
- The DBVictory client, in a supported build
- [Python 3.12](https://www.python.org/downloads/windows/) (3.11+ works)
- [Node.js 22.12+](https://nodejs.org/)
- [MinGW-w64, 32-bit](https://winlibs.com/) installed in `C:\mingw32` (to build the observer DLL)
- .NET Framework 4 (built into Windows; used to build the launcher)

### Steps

Run these in **Windows PowerShell** in the folder where you cloned the repository:

```powershell
# 1. Python environment
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Panel
cd dashboard; npm ci; npm run build; cd ..

# 3. Observer DLL for the current client build
.\build_dll.ps1 -ClientVersion 20261126

# 4. Launcher
.\launcher\build_desktop.ps1 -OutputFile '.\DBVictory Launcher.exe'
```

Then double-click **`Uruchom bota.cmd`** (or `DBVictory Launcher.exe`). On the
first start, use **Zmień** (Change) to point the launcher at your game client
folder, the one that contains `dbvStart.exe`.

### Using it

1. Click **Start**. The launcher opens the client if it is closed.
2. Log in and stay on the character list. Click **Połącz z grą**.
3. Wait for **Zaloguj się do gry** in the panel, then enter the game with your character.
4. Turn on only the features you want. Enabled features are remembered and
   resume on your next login.

Closing the game client ends the bot session.

## Supported client builds

The bot reads the client's memory, so every client build needs its own profile
with verified addresses. Supported builds are listed in
[`client_profiles.py`](client_profiles.py); the current one is the
**2026-11-26** client (`af433830…7486e4`).

If the server updates the client, the bot reports an unknown build and does not
attach. Adding a new build is a developer task described in the
[client update guide](docs/CLIENT_UPDATE_GUIDE.md) and the
[offsets guide](docs/CLIENT_UPDATE_OFFSETS_GUIDE.md).

## Using it with Codex or Claude Code (optional)

The engine is a standard stdio MCP server (`run_mcp.py`), so an AI agent can
read the game state and control the bot with tools such as `get_status`,
`start_bot`, `walk`, `attack`, `toggle_action`, `play_recording` and
`session_activity`. Connecting to the game is always an explicit `start_bot`
call.

- Codex: run `.\scripts\setup-codex.ps1`, then `.\scripts\start-codex.ps1`.
  See [Codex on Windows](docs/CODEX.md).
- Claude Code: edit `.mcp.json` so the paths point at your own `venv` and
  `run_mcp.py`.

A second agent can also attach read-only to a session the launcher started
(`attach_status`, `session_activity`, `packet_journal`).

## Writing your own actions

Actions are Python files in `actions/` with an `async def run(bot)` entry point.
They appear in the panel and can be turned on and off at runtime.

```python
"""Eat food every 10 seconds."""
from protocol import build_use_item_packet

FOOD_ID = 3583  # red ham
INTERVAL = 10

async def run(bot):
    while True:
        if bot.is_connected:
            await bot.inject_to_server(build_use_item_packet(0xFFFF, 0, 0, FOOD_ID, 0, 0))
        await bot.sleep(INTERVAL)
```

See [`bot.py`](bot.py) for everything the `bot` object offers (stats, position,
creatures, `say`, `walk`, `use_item_in_container`, `use_item_on_map`, logging).

## Development

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest -q          # engine tests, no game needed
cd dashboard; npm test; npm run typecheck; npm run build
```

- Contributor rules are in [`AGENTS.md`](AGENTS.md).
- Commit messages drive the version number (`feat:` → minor, `fix:` → patch,
  `!` or `BREAKING CHANGE:` → major). User-visible changes go at the top of
  [`launcher/desktop/release-notes.json`](launcher/desktop/release-notes.json).
- Design notes: [cavebot review](docs/CAVEBOT_REVIEW.md),
  [OTClient analysis](docs/otclient_source_analysis.md),
  [vBot comparison](docs/vbot/architecture.md) (Polish).

## Privacy and safety

- The panel and its API listen only on `127.0.0.1` and require a session token.
- Session keys, credentials and packet payloads are never logged by default.
  Packet capture is off unless you turn it on for diagnosis.
- Your settings (`bot_settings.json`), learned data (`local_data/`) and logs are
  excluded from Git.

## Credits

This project started from the DBVictory proxy bot by **patrykjanik** and has
since been largely rewritten.
