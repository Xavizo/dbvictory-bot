# Releases for maintainers

Players install the bot from a zip on the repository's GitHub Releases page,
and their launcher updates from the same page. This file covers publishing a
release and how the package and updater work.

## Publish a release (browser only)

1. Merge the pull requests you want into `master`.
2. Open **Actions → Release → Run workflow**, keep branch `master` and
   **Publish** ticked, and click **Run workflow**.
3. The run (about 15 minutes) tests, builds and publishes
   `DBVictory-Bot-vX.Y.Z.zip`, `release.json`, `release-manifest.json` and,
   when a release was published before, the patch
   `DBVictory-Bot-vX.Y.Z-patch-vA.B.C.zip` (only files changed since vA.B.C). The version comes from the
   commit history (`feat:` raises the minor number, other commits the patch),
   exactly as the launcher shows it.
4. Players see **Dostępna aktualizacja** at their next launcher start.

A release cannot be published twice for the same version; merge a new commit
first. To withdraw a bad release, delete it on the Releases page; launchers
stop offering it at their next start (players who installed it keep it until a
newer release is published). Unticking **Publish** builds the zip as a
workflow artifact only, for testing. Pull requests that change packaging build
it the same way.

Add a `launcher/desktop/release-notes.json` entry for the new version before
running the workflow: it is shown in the launcher and used as the release text.

The repository must be **public** so players can download without a GitHub
account. A fork publishes to its own Releases page: the workflow writes the
fork's `owner/name` into `release.json`, and the fork's packages update from it.

## Test builds and `develop`

Day-to-day work lands on `develop`; `master` only receives what was tested.

1. Open pull requests against `develop` and merge them there.
2. Every push to `develop` builds and publishes a **test build**: a GitHub
   pre-release `vX.Y.Z-test.<commit>` titled `Test build … (develop)`. It
   replaces develop's previous test build. A newer push cancels a build still
   running.
3. To try it, tick **⚙ → Wersje testowe** in the launcher. The launcher then
   offers whichever was published last, the latest release or the newest test
   build. Players without the switch never see test builds: GitHub never marks
   a pre-release "latest", and master's versions and patches ignore them.
4. When `develop` is good, open a pull request from `develop` to `master`,
   merge it (a normal merge, not squash, so the version history stays the same),
   and publish a release as above.
5. Untick **Wersje testowe** to go back to regular releases; a test build is
   then replaced by the latest release even if its version number is lower.

A single pull request can be tried before it reaches `develop`: run
**Actions → Release → Run workflow** on its branch (the branch must contain
this workflow). Test builds of a pull request's branch are deleted when it
closes; **Actions → Delete test builds** removes others by hand.

## What the package contains

Built by [`scripts/build-release.ps1`](../scripts/build-release.ps1) from the
committed `HEAD`:

| Path | Content |
|---|---|
| program files | `git archive HEAD` (without `.github`, `.claude`, `.mcp.json`) |
| `venv\Scripts\` | relocatable CPython 3.12 with `requirements.txt` installed (laid out where a venv's interpreter would be, so every existing path works). It also runs the launcher's and the panel's windows: pywebview on Windows' own WebView2 |
| `dashboard\.next` | the panel, prebuilt; the engine serves it on `127.0.0.1:4747` with [`dbv/mcp_server/panel_server.py`](../dbv/mcp_server/panel_server.py) |
| `dll\*.dll` | observer DLLs for every reviewed client build (32-bit MinGW) |
| `DBVictory.exe` | the launcher players start; `DBVictory Launcher.exe` is the same program under its old name, for shortcuts and for older launchers, which restart under that name after an update |
| `venv\Scripts\DBVictory.exe`, `venv\Scripts\DBVictory Engine.exe` | `pythonw.exe` and `python.exe` with the bot's icon ([`tools/brand_exe.py`](../tools/brand_exe.py)), so Task Manager and the taskbar show the bot: the launcher and panel windows run on the first, the engine on the second |
| `release.json` | version, commit, repository |
| `release-manifest.json` | SHA-256 and size of every file above |

Local build (Windows, a clean commit):

```powershell
.\scripts\build-release.ps1 -MinGwBin C:\mingw32\bin
```

The build needs Node.js on `PATH` to run `npm ci` and `next build`; the package
does not ship it. Every panel page is prerendered, so players need neither
Node.js nor the Next.js server. Before sealing,
[`tools/release_trim.py`](../tools/release_trim.py) removes what only the build
needs: `dashboard\node_modules` (Next.js, React, the compilers…) and the
panel's build cache, pip, Tk/IDLE and the C headers from Python, and `tests\`.
It also drops Python parts nothing imports (about 21 MB): `cryptography`
(only PyJWT's optional algorithms and the MCP client's OAuth helpers use it),
pywin32's COM packages, samples and tests, pycryptodome's self-tests and
CPython's test modules, `venv`, `lib2to3` and `pydoc_data`.
The build then imports the bot's and the windows' modules with the trimmed
Python and serves the panel with `panel_server` before it zips anything.

Optional `-PythonDir` reuses an existing relocatable Python instead of
`uv python install`.

## How a package updates

[`github_release.py`](../github_release.py), called through
`launcher_files.py`, is used when the folder has `release.json` and no `.git`.
Git installations keep the local release queue in
[`dbv_release.py`](../dbv_release.py).

1. At start the launcher reads `releases/latest` from the GitHub API and the
   small `release.json` asset. Offline or on any error it starts the installed
   version.
2. **Zainstaluj aktualizację** downloads the package to `.dbvictory\updates\github`,
   checks its SHA-256 and size, unpacks only files listed in its manifest and
   checks each file's hash. When the installed version is the patch's base
   (`release.json` → `patch.from`), it downloads the patch instead: the full
   file list plus only the changed files. If the patch does not cover the
   installation (a program file is missing, the download is bad) it falls
   back to the full zip. Releases up to v2.0.17 ignore patches.
3. It swaps only files whose hash changed and removes program files the new
   release no longer has. Running files (the launcher's Python, or Electron in older releases)
   are renamed aside, which Windows allows, and deleted at the next start.
   Folders emptied under `dashboard\node_modules`, `runtime\node` and
   `venv\Scripts` are removed once per installed version.
   A journal (`transaction.json`) rolls an interrupted swap back.
4. Never touched: `bot_settings.json`, `.env`, `.mcp.json`, `.dbvictory\`,
   `local_data\`, `dashboard\public\local-spells\`. Shipped `recordings\` are
   added when missing and never overwritten.
5. The launcher restarts itself on the new version.

**Odłóż wydanie** stores the skipped commit in
`.dbvictory\updates\github\deferred.json`; that release is not offered again, a
newer one is.

## Game client updates

The launcher hashes the client's `dbvStart.exe` before Start. An unknown build
gets a plain message (`CLIENT_UPDATED_MESSAGE` in `dbv/client_bridge/client_profiles.py`): the
bot will not attach, the player can play without it, and a release for the new
client will be offered. Supporting the new build is the developer task in the
[client update guide](CLIENT_UPDATE_GUIDE.md), followed by a release.
