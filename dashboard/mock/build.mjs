// Builds the phone preview: the real launcher page and the real panel, with the bot
// replaced by invented data. Run from dashboard/: `npm run mock` (after `npm ci`).
// Output: dashboard/mock/dist/ (index.html = launcher, panel.html = panel). See mock/README.md.
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const mock = dirname(fileURLToPath(import.meta.url));
const dashboard = join(mock, "..");
const root = join(dashboard, "..");
const launcher = join(root, "launcher", "desktop");
const out = join(dashboard, ".next-mock");
const dist = join(mock, "dist");

if (!process.argv.includes("--skip-next")) {
  const next = spawnSync(process.execPath, [join(dashboard, "node_modules", "next", "dist", "bin", "next"), "build"],
    { cwd: dashboard, stdio: "inherit", env: { ...process.env, DBV_MOCK: "1", NEXT_TELEMETRY_DISABLED: "1" } });
  if (next.status !== 0) process.exit(next.status ?? 1);
}

function replaceOnce(text, find, replacement, where) {
  if (!text.includes(find)) throw new Error(`mock build: "${find}" not found in ${where}; update mock/build.mjs`);
  return text.replace(find, replacement);
}

rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, "mock"), { recursive: true });
// claude.ai artifacts reserve names starting with "_": the assets move from _next/ to next/,
// without Next's pages-router leftovers (pages/_app, _buildManifest, ...) that these pages never load.
cpSync(join(out, "_next"), join(dist, "next"), { recursive: true,
  filter: source => !relative(join(out, "_next"), source).split(sep).some(part => part.startsWith("_")) });
for (const name of readdirSync(join(dist, "next", "static", "chunks")).filter(n => /^webpack-.*\.js$/.test(n))) {
  const file = join(dist, "next", "static", "chunks", name);
  writeFileSync(file, replaceOnce(readFileSync(file, "utf8"), '"./_next/"', '"./next/"', name));
}
for (const file of ["world.js", "backend.js"]) cpSync(join(mock, "panel", file), join(dist, "mock", "panel-" + file));
cpSync(join(mock, "launcher", "backend.js"), join(dist, "mock", "launcher-backend.js"));
for (const file of ["toolbar.js", "toolbar.css"]) cpSync(join(mock, file), join(dist, "mock", file));

// Panel pages: the mock bot takes the place of public/desktop-bridge.js.
const panelScripts = '<link rel="stylesheet" href="./mock/toolbar.css"/><script src="./mock/panel-world.js"></script>' +
  '<script src="./mock/panel-backend.js"></script><script src="./mock/toolbar.js"></script>';
for (const [source, target, page] of [["index.html", "panel.html", "panel"], ["route-editor.html", "route-editor.html", "editor"]]) {
  let html = readFileSync(join(out, source), "utf8");
  html = replaceOnce(html, '<script src="/desktop-bridge.js"></script>', panelScripts, source);
  html = replaceOnce(html, "<html ", `<html data-mock-page="${page}" `, source);
  html = html.replaceAll("./_next/", "./next/");
  if (/(?:src|href)="\/(?!\/)|_next\//.test(html)) throw new Error(`mock build: ${source} still has a root-relative path`);
  writeFileSync(join(dist, target), html);
}

// Launcher: its own page and scripts; the mock plays launcher/desktop_app.py.
for (const file of ["style.css", "effects.js", "render.js", "bridge.js", "hero.webp"]) cpSync(join(launcher, file), join(dist, file));
const words = Object.fromEntries(["pl", "en"].map(l => [l, JSON.parse(readFileSync(join(launcher, "i18n", l + ".json"), "utf8"))]));
const releases = JSON.parse(readFileSync(join(launcher, "release-notes.json"), "utf8"));
writeFileSync(join(dist, "mock", "launcher-texts.js"),
  "window.__dbvLauncherTexts=" + JSON.stringify({ words, releases, version: (releases[0]?.version ?? "v?") + " · mock" }) + ";\n");
let page = readFileSync(join(launcher, "index.html"), "utf8");
page = replaceOnce(page, '<script src="bridge.js" defer></script>',
  '<link rel="stylesheet" href="mock/toolbar.css"><script src="mock/launcher-texts.js" defer></script>' +
  '<script src="mock/launcher-backend.js" defer></script><script src="bridge.js" defer></script><script src="mock/toolbar.js" defer></script>', "launcher index.html");
page = replaceOnce(page, "<html ", '<html data-mock-page="launcher" ', "launcher index.html");
writeFileSync(join(dist, "index.html"), page);

if (!existsSync(join(dist, "panel.html"))) throw new Error("mock build: panel.html missing");
console.log("Mock preview written to " + dist);
