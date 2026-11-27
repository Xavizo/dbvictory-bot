# DBVictory Bot

A desktop bot for the DBVictory game client (Windows). It runs next to the
official client, reads the game state from network traffic and client memory,
and provides a desktop panel for healing, combat, cavebot routes, a learned
world atlas, hunt statistics and alerts. It can also be controlled by an AI
coding agent (Codex or Claude Code) through MCP.

![Loop planning in the atlas: three route variants across floors 4 to 7](docs/screenshots/atlas-loop-variants.png)

> **Read this first.** Using a bot breaks DBVictory's rules and can get your
> account banned. You use this software at your own risk.
>
> **Compatible client:** the DBVictory client build of **26 November 2026**
> (`dbvStart.exe`, SHA-256 `af433830…7486e4`). The bot only supports specific
> client builds. When the server ships a new client, the bot refuses to attach
> until a matching profile is added. See
> [Supported client builds](#supported-client-builds).

## What it does

**Survival**
- Priority healing rules for HP and Ki (potions, senzu beans, healing spells)
- Barrier band and barrier spell upkeep, speed up, food
- Emergency stop and a global pause on the **Pause/Break** key

**Combat**
- Auto targeting with per-monster rules, attack spells and runes
- Spell conditions (cast only when the required monsters, HP or Ki are present)
- Area (wave) spells when several monsters are in range
- Hunter Knife: skins your own corpses and confirms the loot (only some
  monsters for now, see [Hunter Knife](#hunter-knife))

**Cavebot and atlas**
- Record and replay hunting routes, including stairs, ladders, ropes, doors and digging
- Lure mode: keep walking until a group follows, then fight before corridors and floor changes
- A world atlas learned passively while you play: floors, transitions, hunt areas and monster sightings
- Atlas routes planned from what the atlas has learned, with a route replay viewer

**Panel**
- Live HP/Ki, position, nearby creatures, items, pets and the world map
- Session statistics (XP/h, kills, loot) and hunt comparisons
- Alerts with sound for private messages, chat, players on screen and Ki drops
- Bot profiles per character, so each character keeps its own settings

## A tour of the panel

The panel is in Polish. Labels are quoted as they appear, with a translation.
The screenshots use a real learned atlas with a demo character.

### Automation

The **Automatyka** (Automation) tab holds everything that runs while you play
or hunt: healing rules, combat spells, buffs and the Hunter Knife. Each feature
has its own switch, and enabled features resume on your next login.

![Automation tab with healing rules](docs/screenshots/automation.png)

Healing is a list of **Reguły leczenia** (healing rules). Each rule says
*when* (HP or Ki below a threshold) and *what* to use (a potion, senzu bean or
spell). Rules are checked in priority order and share the item cooldown, so the
most urgent one always wins.

The session strip at the top shows XP/h, kills, Senzu/h and how long you have
been hunting.

### Atlas

**Atlas** opens a full-screen map with three views:

- **Świat** (World): every area you have discovered, with the hunting grounds the
  bot found and approximate monster spawns.
- **Zapisana mapa** (Saved map): the learned map, floor by floor, with stairs,
  ladders, holes and other transitions the bot has confirmed.
- **Mapa na żywo** (Live map): what the client sees around you right now.

![The world as the atlas has learned it](docs/screenshots/atlas-world.png)

![Live map with your character, nearby monsters and a floor transition](docs/screenshots/atlas-live.png)

The atlas fills itself while you play. You don't need to record anything: walk
around, change floors and fight, and the bot remembers tiles, transitions,
monster sightings and where you gained XP. All of it is stored locally in
`local_data/`.

### What the live map sees

The live map reads every tile in the client's view (18 × 14 tiles) straight
from the client, not from screenshots. For each tile it knows:

- whether you can walk there, or it is blocked, has no ground or should be avoided
- every item on it, with the client's own flags (for example whether it blocks
  movement or can be moved)
- who stands there, by type: you, other players, NPCs, pets and monsters

It also recognises the objects you can use to get somewhere: **doors, stairs,
ladders, holes, covered holes (shovel), rope spots, teleports, spider webs and
level doors** (with the level they require). A movable corpse lying on a tile is
treated as an obstacle, so routes go around it or clear it.

**Tools are found automatically.** When a route needs a rope, a shovel or an
AiO, the bot looks for it in your open backpacks and equipment
slots. It recognises the rope, the shovel and both the **small and medium
All-in-One (AiO)** tools, including an AiO worn in its equipment slot. Spider
webs need an AiO to cut.

### The atlas learns, and you can teach it

Every time you walk through a door, take stairs, fall through a hole or use a
rope spot during manual play, the atlas records the entry, the exit and what you
did. It counts how often it saw each passage, and the route planner uses the
confirmed ones. Learning pauses while automation is running or
your character is dead, so the bot only learns from what you did yourself.

You don't have to wait for it to learn. On the live map, right-click a tile (or
press **Shift+F10**) and choose the object type, such as stairs, hole or door.
Your marks are saved with the atlas and used by the planner straight away. The
learned passages are listed under **Diagnostyka → Wyuczone przejścia**.

### Creating a route

There are three ways to create a cavebot route. All of them end as a
**szkic** (draft) that you can review and edit on the map before using it.

**1. Plan it from a hunting ground (no recording).**
Open **Atlas → Świat** and click **Planuj pętlę** (plan a loop). Pick a hunting
ground from the list. The bot adds one automatically whenever you gain XP
somewhere during manual play, and it remembers every floor you fought on. Choose
**Całe expowisko** (whole hunting ground) or select sectors yourself, then click
**Porównaj pętle** (compare loops). The atlas proposes three variants:

- **Krótka** (short): a compact loop, well under the respawn time, that still
  meets monsters often.
- **Pod respawn** (paced to respawn): the most expected monster encounters per
  hour, with a loop time close to the respawn time and as many different rooms
  as possible.
- **Pełna** (full): covers every selected sector it can reach.

Each variant shows the loop time, how many sectors it covers, an estimated
XP/h, and coverage per floor. If part of the ground can only be reached through
an unconfirmed passage, the planner suggests a **łącznik** (connector) and lists
the transitions to check in game. Save the variant you like as a draft.

**2. Record it while you hunt.**
Start **Nagrywanie adaptacyjne** (adaptive recording) and hunt normally. A
progress bar tells you when the recording is good enough:

1. Gain XP in the first spot.
2. Visit the next room or go back for another group.
3. The bot looks for a way back to sectors you've already visited.
4. A loop is detected, and you can stop recording.

The bot turns what you did into a draft. **Optymalizuj trasę** (optimize route)
removes detours and backtracking. You can compare the result with the original
before accepting it.

**3. Draw it on the map.**
In a new draft, click points on the map or draw a path. The editor connects them
using known paths and transitions, and marks any section it can't connect.

![Route editor: stages by floor, doors and waypoints](docs/screenshots/route-editor.png)

In the editor you can drag waypoints, insert points after a waypoint, choose a
specific transition (stairs, ladder, rope spot) for a floor change, and show the
route one stage or floor at a time. Save the draft with a name when you're done.

### Running a route

On the **Cavebot** tab, choose the route and set up combat:

![Cavebot tab with a route and lure settings](docs/screenshots/cavebot.png)

- When to attack: **Gdy potwór jest na ekranie** (when a monster is on screen) or
  **Gdy podejdzie na X SQM** (when it comes within X squares).
- Target priority: **Najbliższy** (nearest), **Najmniej HP** (lowest HP) or
  **Najwięcej HP** (highest HP).
- Fighting style: **Stój i walcz** (stand and fight), **Goń** (chase) or
  **Luruj** (lure: gather a group of a set size before fighting).
- Chase distance, loot time limit and spell conditions.

Press start. The run bar at the top shows the current phase (route, lure, fight,
chase, return), the current waypoint and any problem, such as a transition that
needs checking. **Pauza** (pause) there pauses only the route; healing and
other actions keep running. To pause all automation at once, use **Pauza
wszystkich** (pause all) on the Automation tab or press **Pause/Break**. **Historia decyzji i raporty okrążeń** (decision history and loop reports)
explains why the bot did what it did and sums up each loop.

### Hunter Knife

The Hunter Knife skins only corpses of monsters you killed and confirms the
loot that drops. For now it recognises the corpses of these monsters only:

- Fire Elemental, Fire Elementalis, Mutant Fire Elemental, Fury Mutant Fire
  (Elemental)
- Roco, Chibi Roco, Fury Roco
- Gunso, Chibi Gunso, Fury Gunso
- Mutant Wolf

Other corpses are left alone. More monsters can be added in
[`hunter_knife.py`](hunter_knife.py) once their corpse is identified.

### Diagnostics

The **Diagnostyka** (Diagnostics) tab shows whether each part of the session is
working: the panel, the game session, the observer DLL and its bridge, and how
many packets have passed. It also lists the passages the atlas has learned,
nearest first, and lets you record a run or save the last movement block for a
bug report.

![Diagnostics tab with learned passages and session status](docs/screenshots/diagnostics.png)

### Alerts

The bell collects game events: a private message, a message in local chat, a
player on screen, or a sudden Ki drop. You can turn each kind on or off, give it
a sound, and choose whether it fires always or only while the cavebot is running.

## How it works

![The launcher, ready to start](docs/screenshots/launcher.jpg)

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

The launcher works entirely on your computer. It does not download anything
from the internet: it starts the bot from the folder you cloned, and the
**Ostatnie zmiany** (recent changes) list comes from the release notes in that
folder. All learned data (atlas, profiles, reports) stays on your computer in
`local_data/`, and nothing is uploaded anywhere.

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
- [Git for Windows](https://git-scm.com/download/win)
- [Python 3.12](https://www.python.org/downloads/windows/) (3.11+ works)
- [Node.js 22.12+](https://nodejs.org/)
- [MinGW-w64, 32-bit](https://winlibs.com/) installed in `C:\mingw32` (to build the observer DLL)
- .NET Framework 4 (built into Windows; used to build the launcher)

### Steps

Clone the repository with Git into any folder (for example
`git clone <repository URL> C:\DBVictoryBot`). A ZIP download does not work:
the launcher reads the installed version from Git. Then run these in
**Windows PowerShell** in that folder:

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
[`client_profiles.py`](client_profiles.py).

| Client build | `dbvStart.exe` SHA-256 | Status |
|---|---|---|
| 2026-11-26 | `af43383076c0abc7192b407210efc54da6b6cbaf4ff8a2aa18f22a7f7d7486e4` | Current, confirmed in game |

Older builds still have profiles in the code but are no longer tested. To check
your own client without starting the bot:

```powershell
.\venv\Scripts\python.exe check_client_build.py 'C:\DBVClient_v5\dbvStart.exe'
```

If the server updates the client, the bot reports an unknown build and does not
attach. Adding a new build is a developer task described in the
[client update guide](docs/CLIENT_UPDATE_GUIDE.md) and the
[offsets guide](docs/CLIENT_UPDATE_OFFSETS_GUIDE.md).

## Using it with Codex or Claude Code (optional)

The engine is a standard stdio MCP server (`run_mcp.py`), so an AI agent can
read the game state and control the bot with tools such as `get_status`,
`start_bot`, `walk`, `attack`, `toggle_action` and
`session_activity`. Connecting to the game is always an explicit `start_bot`
call.

- Codex: run `.\scripts\setup-codex.ps1`, then `.\scripts\start-codex.ps1`.
  See [Codex on Windows](docs/CODEX.md).
- Claude Code: open Claude Code in the bot folder. The included `.mcp.json`
  starts `run_mcp.py` with the folder's own `venv`.

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

See `BotContext` in [`mcp_server.py`](mcp_server.py) for everything the `bot` object offers (stats, position,
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

This project is a fork of the original DBVictory proxy bot by
**patrykjanik**, which provided the first proxy, packet parser, DLL and
cavebot. It has since been almost completely rewritten: the launcher and
updater, client profiles, native observer, atlas, combat, healing, Hunter Knife
and most of the panel are new.
