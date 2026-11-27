// The panel window (dbv/mcp_server/panel_window.py) exposes narrow operations as
// window.pywebview.api. Map them onto the names the panel uses. Never exposes a
// general IPC or clipboard reads; files only as route packs the player saves
// or opens through a native dialog. In a plain browser nothing
// answers, so every call fails after a short wait, as browser access must.
(() => {
  "use strict";
  if (window.dbvAuth) return;
  const ready = new Promise((resolve, reject) => {
    const done = () => window.pywebview && window.pywebview.api ? resolve(window.pywebview.api) : null;
    if (done() === null) {
      window.addEventListener("pywebviewready", done, { once: true });
      setTimeout(() => reject(new Error("Open the dashboard through start_bot; browser access is disabled.")), 10000);
    }
  });
  const call = (name, ...args) => ready.then(api => {
    if (typeof api[name] !== "function") throw new Error("Panel operation unavailable.");
    return api[name](...args);
  });
  const fixed = value => Object.freeze(value);
  Object.defineProperties(window, {
    dbvAuth: { value: fixed({ getToken: () => call("get_token") }) },
    dbvMap: { value: fixed({ copyCell: text => call("copy_cell", text), copyPlan: text => call("copy_plan", text) }) },
    dbvEditor: { value: fixed({ open: draftId => call("open_editor", draftId) }) },
    dbvRoutes: { value: fixed({ save: (fileName, text) => call("save_route", fileName, text), open: () => call("open_route") }) },
    dbvAlerts: { value: fixed({ testSound: () => call("test_sound"), panelPause: value => call("panel_pause", value) }) },
  });
})();
