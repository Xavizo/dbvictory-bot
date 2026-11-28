# Phone preview (mock)

A clickable copy of the launcher and the panel that runs in any browser, phone included,
with invented game data. It lets a change to the UI be looked at before anyone tests it with
the real client. It never contacts a game, a bot or any other host.

The pages are the real ones: `launcher/desktop/` (index.html, render.js, style.css, i18n) and
the panel built from `dashboard/app` and `dashboard/lib`. Only the parts that talk to the bot
are swapped:

| Real                                                  | In the preview                    |
| ----------------------------------------------------- | --------------------------------- |
| `launcher/desktop_app.py` (window.pywebview.api)      | `mock/launcher/backend.js`        |
| `public/desktop-bridge.js`, HTTP :8089, WebSocket :8090 | `mock/panel/backend.js`         |
| the game world                                        | `mock/panel/world.js`             |

A bar at the bottom (`mock/toolbar.js`) switches between the launcher and the panel, picks a
situation (hunting with the cavebot, idle, low HP, logged out, bot offline; for the launcher:
ready, update available, no client folder, session error), switches PL/EN for the panel (the
launcher has its own switch), and toggles "PC size", which draws the page at the desktop
window's width and scales it to the screen. Changes made in the preview (routes, profiles,
settings) stay in that browser's storage until "↺" resets them.

## Build

From `dashboard/`, after `npm ci`:

```
node mock/build.mjs
```

It runs `next build` with `DBV_MOCK=1` (a static export with relative paths, see
`next.config.mjs`, written to `.next-mock/`) and assembles `mock/dist/`:
`index.html` (launcher), `panel.html`, `route-editor.html`, `next/` (Next's `_next/`, renamed because
artifacts reserve names that start with `_`), `mock/`. Serve that folder
with any static server, for example `python -m http.server -d mock/dist`. `--skip-next`
reuses the last `.next-mock/` when only files in `mock/` or `launcher/desktop/` changed.
The build fails loudly if a page no longer has the tag it replaces.

`mock/dist/` is not committed, and `tools/release_trim.py` leaves `dashboard/mock/` out of
release packages.

## Keeping it in step

- UI changes need nothing here: the preview builds from the same code.
- A new field in `/api/state` or a new endpoint: add fake values in `mock/panel/backend.js`
  (the panel shows `[mock] no fake answer for ...` in the console for a missing endpoint).
- A new launcher action or phase: add it to `mock/launcher/backend.js`.

## Publishing

The preview is published as a private claude.ai artifact from the files in `mock/dist/`
(all of them, `index.html` first) to https://claude.ai/artifact/VMLQHgxj1kPcnYhyZ1SeHq.
Republish it after each UI change with mode "replace", so chunk files from the previous build
(their names carry a hash) are removed.
