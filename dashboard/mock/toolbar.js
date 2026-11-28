// The preview's own bar (see mock/README.md): switch page, test situation, language and
// desktop/phone sizing. Not part of the bot; only the mock build includes it.
(() => {
  "use strict";
  const page = document.documentElement.dataset.mockPage; // "launcher" | "panel" | "editor", set by mock/build.mjs
  const store = {
    memory: (window.__dbvMockMemory ??= {}), // shared by the mock scripts, used when the viewer's browser keeps no storage
    get(key, fallback) { try { const v = localStorage.getItem("dbv.mock." + key); return v === null ? fallback : JSON.parse(v); } catch { return key in this.memory ? this.memory[key] : fallback; } },
    set(key, value) { this.memory[key] = value; try { localStorage.setItem("dbv.mock." + key, JSON.stringify(value)); } catch { /* storage off */ } },
  };
  const DESKTOP = { launcher: 1024, panel: 1000, editor: 1200 }[page] ?? 1000; // the desktop windows' content width
  const SCENARIOS = page === "launcher"
    ? [["ready", "Ready"], ["update", "Update available"], ["no-client", "No client folder"], ["problem", "Session error"]]
    : [["hunting", "Hunting"], ["idle", "Idle in game"], ["low-hp", "Low HP"], ["logged-out", "Not logged in"], ["offline", "Bot offline"]];
  const scenarioKey = page === "launcher" ? "launcherScenario" : "scenario";
  const sizeKey = "desktopSize." + page; // the launcher defaults to its PC size, the panel to the phone's width

  // "PC size" draws the page at the window's real width and scales it to the screen.
  function fit() {
    const desktop = store.get(sizeKey, page === "launcher");
    const style = getComputedStyle(document.body);
    const room = document.documentElement.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    const zoom = desktop && room < DESKTOP ? room / DESKTOP : 1;
    const bar = document.getElementById("dbv-mock-bar");
    const barHeight = bar ? bar.getBoundingClientRect().height : 0;
    document.documentElement.style.setProperty("--dbv-mock-bar", barHeight + "px");
    for (const el of document.body.children) {
      if (el.id === "dbv-mock-bar" || el.tagName === "SCRIPT") continue;
      el.style.zoom = zoom === 1 ? "" : String(zoom);
      el.style.width = zoom === 1 ? "" : DESKTOP + "px";
      // The panel fills the window; keep its bottom above this bar at any scale.
      if (page !== "launcher") el.style.height = zoom === 1 ? "" : (innerHeight - barHeight) / zoom + "px";
    }
  }

  function toast(message) {
    const el = document.getElementById("dbv-mock-toast");
    if (!el) return;
    el.textContent = message; el.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.hidden = true; }, 2200);
  }
  window.__dbvMockToast = toast;

  function build() {
    const bar = document.createElement("div");
    bar.id = "dbv-mock-bar";
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "Mock preview controls");
    const language = store.get("language", "pl");
    bar.innerHTML = `
      <button type="button" class="dbv-mock-tag" aria-expanded="true" title="Invented data; nothing reaches the game. Tap to fold this bar.">Mock</button>
      <nav><a href="index.html" ${page === "launcher" ? 'aria-current="page"' : ""}>Launcher</a><a href="panel.html" ${page !== "launcher" ? 'aria-current="page"' : ""}>Panel</a></nav>
      <label><span class="dbv-mock-hide">Situation</span><select id="dbv-mock-scenario">${SCENARIOS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
      ${page === "launcher" ? "" : `<div class="dbv-mock-lang" role="group" aria-label="Language"><button type="button" data-l="pl" aria-pressed="${language === "pl"}">PL</button><button type="button" data-l="en" aria-pressed="${language === "en"}">EN</button></div>`}
      <label class="dbv-mock-check"><input type="checkbox" id="dbv-mock-desktop"> PC size</label>
      <button type="button" id="dbv-mock-reset" title="Forget changes made in the mock" aria-label="Reset the mock">↺</button>
      <div id="dbv-mock-toast" role="status" hidden></div>`;
    document.body.append(bar);
    const tag = bar.querySelector(".dbv-mock-tag");
    const fold = folded => { bar.classList.toggle("folded", folded); fit(); tag.setAttribute("aria-expanded", String(!folded)); store.set("folded", folded); };
    tag.onclick = () => fold(!bar.classList.contains("folded"));
    fold(store.get("folded", false));
    const select = bar.querySelector("#dbv-mock-scenario");
    select.value = store.get(scenarioKey, SCENARIOS[0][0]);
    select.onchange = () => { store.set(scenarioKey, select.value); window.dispatchEvent(new Event("dbv-mock-scenario")); toast(select.selectedOptions[0].text); };
    for (const b of bar.querySelectorAll("[data-l]")) b.onclick = () => {
      store.set("language", b.dataset.l);
      for (const o of bar.querySelectorAll("[data-l]")) o.setAttribute("aria-pressed", String(o === b));
    };
    const desktop = bar.querySelector("#dbv-mock-desktop");
    desktop.checked = store.get(sizeKey, page === "launcher");
    desktop.onchange = () => { store.set(sizeKey, desktop.checked); fit(); };
    bar.querySelector("#dbv-mock-reset").onclick = () => {
      try { for (const k of Object.keys(localStorage)) if (k.startsWith("dbv.mock.") && k !== "dbv.mock.language") localStorage.removeItem(k); } catch { /* storage off */ }
      location.reload();
    };
    fit();
    new ResizeObserver(fit).observe(bar);
  }
  addEventListener("resize", fit);
  // After the page (and, in the panel, React) has started, so the bar never disturbs hydration.
  // If React redraws the whole document (after a hydration mismatch) the bar is added again.
  const keep = () => { if (!document.getElementById("dbv-mock-bar")) build(); };
  const start = () => { setTimeout(keep, 0); setInterval(keep, 1000); };
  if (document.readyState === "complete") start(); else addEventListener("load", start);
})();
