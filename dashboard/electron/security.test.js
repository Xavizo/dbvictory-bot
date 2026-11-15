const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadMain() {
  const handlers = {};
  const navigation = {};
  let preferences;
  let windowHandler;
  const contents = {
    mainFrame: { url: "http://127.0.0.1:4747/" },
    on: (name, fn) => { navigation[name] = fn; },
    setWindowOpenHandler: (fn) => { windowHandler = fn; },
    session: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} },
  };
  class BrowserWindow {
    constructor(options) { preferences = options.webPreferences; this.webContents = contents; }
    loadURL() {}
    isDestroyed() { return false; }
  }
  const electron = {
    BrowserWindow,
    app: { whenReady: () => ({ then: (fn) => fn() }), on() {} },
    ipcMain: { handle: (name, fn) => { handlers[name] = fn; } },
  };
  const process = { env: { DBV_DASHBOARD_TOKEN: "test-token-not-a-real-secret" } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "main.js"), "utf8"), {
    require: (name) => name === "electron" ? electron : require(name),
    __dirname, process, URL,
    fetch: async () => ({ ok: true }), setTimeout() {},
  });
  return { token: handlers["dbv:dashboard-token"], contents, preferences, navigation, windowHandler, process };
}

test("only the trusted main frame receives the dashboard credential", () => {
  const app = loadMain();
  const event = { sender: app.contents, senderFrame: app.contents.mainFrame };
  assert.equal(app.token(event), "test-token-not-a-real-secret");
  assert.equal(app.process.env.DBV_DASHBOARD_TOKEN, undefined);
  assert.throws(() => app.token({ ...event, sender: {} }));
  assert.throws(() => app.token({ ...event, senderFrame: { url: app.contents.mainFrame.url } }));
  app.contents.mainFrame.url = "https://evil.example/";
  assert.throws(() => app.token(event));
});

test("renderer stays sandboxed and cannot navigate to another origin", () => {
  const app = loadMain();
  assert.equal(app.preferences.nodeIntegration, false);
  assert.equal(app.preferences.contextIsolation, true);
  assert.equal(app.preferences.sandbox, true);
  assert.equal(app.preferences.webSecurity, true);
  assert.equal(app.windowHandler().action, "deny");
  for (const name of ["will-navigate", "will-redirect"]) {
    let prevented = false;
    const event = { preventDefault() { prevented = true; } };
    app.navigation[name](event, "http://127.0.0.1:4747/");
    assert.equal(prevented, false);
    app.navigation[name](event, "http://127.0.0.1:4747.evil.example/");
    assert.equal(prevented, true);
  }
});
