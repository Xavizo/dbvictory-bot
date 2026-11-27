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

## What the package contains

Built by [`scripts/build-release.ps1`](../scripts/build-release.ps1) from the
committed `HEAD`:

| Path | Content |
|---|---|
| program files | `git archive HEAD` (without `.github`, `.claude`, `.mcp.json`) |
| `venv\Scripts\` | relocatable CPython 3.12 with `requirements.txt` installed (laid out where a venv's interpreter would be, so every existing path works) |
| `dashboard\.next` | the panel, prebuilt; the engine serves it on `127.0.0.1:4747` with [`dbv/mcp_server/panel_server.py`](../dbv/mcp_server/panel_server.py) |
| `dashboard\node_modules\electron` | Electron, the launcher's and the panel's window (the only panel package shipped) |
| `dll\*.dll` | observer DLLs for every reviewed client build (32-bit MinGW) |
| `DBVictory Launcher.exe` | the launcher |
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
needs: every panel package except Electron (Next.js, React, the compilers…),
pip, Tk/IDLE and the C headers from Python, and `tests\`. The build then
imports the bot's modules with the trimmed Python and serves the panel with
`panel_server` before it zips anything. Electron's own files stay whole: the
running launcher holds them open while it installs an update.

Optional `-PythonDir` reuses an existing relocatable Python instead of
`uv python install`.

## How a package updates

[`github_release.py`](../github_release.py), called through
`launcher_files.py`, is used when the folder has `release.json` and no `.git`.
Git installations keep the local release queue described in
[CODEX.md](CODEX.md).

1. At start the launcher reads `releases/latest` from the GitHub API and the
   small `release.json` asset. Offline or on any error it starts the installed
   version.
2. **Zainstaluj aktualizację** downloads the package to `.codex\updates\github`,
   checks its SHA-256 and size, unpacks only files listed in its manifest and
   checks each file's hash. When the installed version is the patch's base
   (`release.json` → `patch.from`), it downloads the patch instead: the full
   file list plus only the changed files. If the patch does not cover the
   installation (a program file is missing, the download is bad) it falls
   back to the full zip. Releases up to v2.0.17 ignore patches.
3. It swaps only files whose hash changed and removes program files the new
   release no longer has. Running files (the launcher's Electron and Python)
   are renamed aside, which Windows allows, and deleted at the next start.
   Folders emptied under `dashboard\node_modules`, `runtime\node` and
   `venv\Scripts` are removed once per installed version.
   A journal (`transaction.json`) rolls an interrupted swap back.
4. Never touched: `bot_settings.json`, `.env`, `.mcp.json`, `.codex\`,
   `local_data\`, `dashboard\public\local-spells\`. Shipped `recordings\` are
   added when missing and never overwritten.
5. The launcher restarts itself on the new version.

**Odłóż wydanie** stores the skipped commit in
`.codex\updates\github\deferred.json`; that release is not offered again, a
newer one is.

## Game client updates

The launcher hashes the client's `dbvStart.exe` before Start. An unknown build
gets a plain message (`CLIENT_UPDATED_MESSAGE` in `dbv/client_bridge/client_profiles.py`): the
bot will not attach, the player can play without it, and a release for the new
client will be offered. Supporting the new build is the developer task in the
[client update guide](CLIENT_UPDATE_GUIDE.md), followed by a release.
