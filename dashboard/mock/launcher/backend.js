// The phone preview's stand-in for launcher/desktop_app.py (see mock/README.md). It plays
// window.pywebview.api for launcher/desktop/bridge.js with invented states; nothing here
// starts a client, installs an update or edits an AI program's settings.
(() => {
  "use strict";
  const TEXTS = window.__dbvLauncherTexts; // written by mock/build.mjs from launcher/desktop/i18n
  const store = {
    memory: (window.__dbvMockMemory ??= {}), // shared by the mock scripts, used when the viewer's browser keeps no storage
    get(key, fallback) { try { const v = localStorage.getItem("dbv.mock." + key); return v === null ? fallback : JSON.parse(v); } catch { return key in this.memory ? this.memory[key] : fallback; } },
    set(key, value) { this.memory[key] = value; try { localStorage.setItem("dbv.mock." + key, JSON.stringify(value)); } catch { /* storage off */ } },
  };
  let language = store.get("language", "pl");
  const text = (key, params) => {
    const s = TEXTS.words[language][key] ?? TEXTS.words.pl[key] ?? key;
    return params ? s.replace(/\{(\w+)\}/g, (m, n) => n in params ? params[n] : m) : s;
  };
  const base = { version: TEXTS.version, changes: TEXTS.releases.slice(0, 12), folder: "C:\\Games\\DBVictory", installed: true,
    clientOpen: false, canDefer: false, canInstall: false, readOnly: false, channel: store.get("channel", "stable"), notesError: false };
  let view, said, timers = [];
  const listeners = () => window.__dbvDesktopChanged?.(snapshot());
  const snapshot = () => ({ ...view, text: said ? text(...said) : "", language });
  function set(phase, key, extra = {}) { said = key ? [].concat(key) : null; view = { ...view, ...extra, phase }; listeners(); }
  const later = (ms, fn) => timers.push(setTimeout(fn, ms));
  const clear = () => { timers.forEach(clearTimeout); timers = []; };

  function scenario() {
    clear();
    view = { ...base, phase: "ready", progress: undefined, failedStep: undefined };
    const s = store.get("launcherScenario", "ready");
    if (s === "update") set("available", ["state.newerRelease"], { canInstall: true, canDefer: true, pendingLabel: "v9.9.9 (mock)" });
    else if (s === "no-client") set("ready", ["state.folderMissingClient"], { installed: false, folder: "" });
    else if (s === "problem") set("problem", ["state.sessionFailed"], { canDefer: false });
    else set("ready", ["state.startClosed"]);
  }

  function install() {
    let percent = 0;
    const label = view.pendingLabel || "v9.9.9";
    const tick = () => {
      percent = Math.min(100, percent + 9);
      const step = percent < 40 ? 1 : percent < 80 ? 2 : 3;
      set("applying", ["state.updatingTo", { label }], { progress: { step, percent, label: text("state.updatingTo", { label }), rate: "4.2 MB/s" } });
      if (percent < 100) later(350, tick);
      else later(600, () => { store.set("launcherScenario", "ready"); scenario(); });
    };
    tick();
  }

  function act(name) {
    const phase = view.phase;
    if (name === "start" && ["ready", "clientError"].includes(phase) && view.installed) {
      set("detecting", ["state.launchingClient"], { clientOpen: false });
      later(1200, () => set("prepared", ["state.waitingForList"], { clientOpen: true }));
    } else if (name === "connect" && phase === "prepared") {
      set("connecting", ["state.connecting"]);
      later(1000, () => set("starting", ["state.startingEngine"]));
      later(2200, () => set("panel", ["state.engineReady"]));
      later(3400, () => set("running", ["state.running"]));
      later(4800, () => { location.href = "panel.html"; });
    } else if (name === "install") install();
    else if (name === "defer") { store.set("launcherScenario", "ready"); scenario(); }
    else if (name === "folder") set(phase, said, { installed: true, folder: "C:\\Games\\DBVictory" });
    else if (["retry", "recheck"].includes(name)) { set("checking", ["state.checkingUpdates"]); later(900, () => { store.set("launcherScenario", "ready"); scenario(); }); }
    else if (name === "cancel") scenario();
    return snapshot();
  }

  const ai = store.get("ai", { codex: false, "claude-desktop": true, "claude-code": false });
  const labels = { codex: "Codex", "claude-desktop": "Claude Desktop", "claude-code": "Claude Code" };
  const aiStatus = message => ({ status: Object.fromEntries(Object.entries(labels).map(([k, label]) => [k, { label, connected: ai[k], available: k !== "claude-code" || ai[k] }])), message });

  const wait = ms => new Promise(r => setTimeout(r, ms));
  window.pywebview = { api: {
    state: async () => snapshot(),
    act: async name => act(name),
    texts: async () => ({ texts: TEXTS.words[language], language }),
    language: async next => { language = next; store.set("language", next); listeners(); return { texts: TEXTS.words[language], language }; },
    channel: async channel => { await wait(500); store.set("channel", channel); view = { ...view, channel }; listeners(); return snapshot(); },
    mcp: async (operation, target) => {
      if (operation === "status") return aiStatus(null);
      await wait(500);
      ai[target] = operation === "connect"; store.set("ai", ai);
      return aiStatus(text("ai.done." + operation, { label: labels[target], hint: text("ai.hint." + target) }));
    },
  } };
  window.addEventListener("dbv-mock-scenario", scenario);
  scenario();
})();
