"use strict";
// launcher/desktop_app.py exposes state, act, mcp, channel, texts and language as window.pywebview.api and
// pushes every state change through window.__dbvDesktopChanged.
(() => {
  const ready = new Promise(resolve => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.act) resolve(window.pywebview.api);
    else window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), {once:true});
  });
  const listeners = [];
  window.__dbvDesktopChanged = snapshot => { for (const fn of listeners) fn(snapshot); };
  window.desktop = Object.freeze({
    state: () => ready.then(api => api.state()),
    act: action => ready.then(api => api.act(action)),
    mcp: (operation, target) => ready.then(api => api.mcp(operation, target ?? null)),
    channel: channel => ready.then(api => api.channel(channel)),
    texts: () => ready.then(api => api.texts()),
    language: language => ready.then(api => api.language(language)),
    onState: fn => { listeners.push(fn); },
  });
})();
