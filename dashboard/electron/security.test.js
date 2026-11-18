const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadMain(engineLabel) {
  const handlers = {};
  const navigation = {};
  let preferences;
  let windowHandler;
  const copied = []; const windows=[];
  const contents = {
    mainFrame: { url: "http://127.0.0.1:4747/" },
    on: (name, fn) => { navigation[name] = fn; },
    setWindowOpenHandler: (fn) => { windowHandler = fn; },
    session: { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} },
  };
  class BrowserWindow {
    constructor(options) { this.title = options.title; preferences = options.webPreferences; this.webContents = windows.length ? {...contents,mainFrame:{url:"http://127.0.0.1:4747/route-editor"}} : contents; this.events={}; windows.push(this); }
    on(name,fn) { (this.events[name]??=[]).push(fn); }
    restore() {}
    focus() {}
    close() {}
    loadURL(url) { this.loaded=url; }
    isDestroyed() { return false; }
  }
  const electron = {
    BrowserWindow,
    app: { whenReady: () => ({ then: (fn) => fn() }), on() {} },
    Tray: class { setToolTip() {} setContextMenu() {} on() {} },
    Menu: { buildFromTemplate: value => value },
    nativeImage: { createFromPath: () => ({ isEmpty: () => false }) },
    ipcMain: { handle: (name, fn) => { handlers[name] = fn; } },
    shell: {beep() {}},
    clipboard: { writeText: (text) => copied.push(text) },
  };
  const process = { env: { DBV_DASHBOARD_TOKEN: "test-token-not-a-real-secret" } };
  if (engineLabel) process.env.DBV_ENGINE_LABEL = engineLabel;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "main.js"), "utf8"), {
    require: (name) => name === "electron" ? electron : name === "./notification-audio" ? {createNotificationAudio:({fallback})=>({play:fallback,stop(){}})} : require(name),
    __dirname, process, URL,
    fetch: async () => ({ ok: true }), setTimeout() {},
  });
  return { testSound:handlers["dbv:test-alert-sound"],editor:handlers["dbv:open-route-editor"],windows,token: handlers["dbv:dashboard-token"], copy:handlers["dbv:copy-map-cell"], copyPlan:handlers["dbv:copy-map-plan"], copied, contents, preferences, navigation, windowHandler, process };
}

test("the window keeps the engine revision and its start time on page refresh", () => {
  const label = "Silnik abc2345 · start 2026-11-18 09:31:47+01:00";
  const win = loadMain(label).windows[0];
  assert.equal(win.title, "DBVictory Bot — " + label);
  let prevented = false;
  for (const listener of win.events["page-title-updated"])
    listener({preventDefault() { prevented = true; }}, "DBVictory Bot");
  assert.equal(prevented, true);
  assert.equal(loadMain().windows[0].title, "DBVictory Bot — wersja silnika niedostępna");
});

test("copy accepts bounded tile text only from the trusted main frame", () => {
  const app=loadMain();
  const event={sender:app.contents,senderFrame:app.contents.mainFrame};
  const text="Pole (986, 609, 7) — Blokada\nID 6264 · warstwa 1";
  assert.equal(app.copy(event,text),true);
  assert.deepEqual(app.copied,[text]);
  for(const bad of [null,{},"private text",text+"x".repeat(16384),text+"\0"])
    assert.throws(()=>app.copy(event,bad));
  assert.throws(()=>app.copy({...event,sender:{}},text));
  assert.throws(()=>app.copy({...event,senderFrame:{url:app.contents.mainFrame.url}},text));
  app.contents.mainFrame.url="https://evil.example/";
  assert.throws(()=>app.copy(event,text));
  assert.deepEqual(app.copied,[text]);
});

test("preload exposes a write-only fixed IPC operation for map copying", () => {
  const exposed={}, calls=[];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,"preload.js"),"utf8"),{
    require:()=>({contextBridge:{exposeInMainWorld:(name,value)=>{exposed[name]=value;}},
      ipcRenderer:{invoke:(...args)=>{calls.push(args);return Promise.resolve(true);}}}),
  });
  assert.deepEqual(Object.keys(exposed.dbvMap),["copyCell","copyPlan"]);
  exposed.dbvMap.copyCell("tile");
  assert.deepEqual(calls,[["dbv:copy-map-cell","tile"]]);
});

test("route copying rejects other senders, executable plans and oversized data", () => {
  const app=loadMain(), event={sender:app.contents,senderFrame:app.contents.mainFrame};
  const plan={preview_only:true,scope:"test",path:[[10,10,7]],points:[[10,10,7]]};
  const text=JSON.stringify(plan);
  assert.equal(app.copyPlan(event,text),true);
  for(const bad of [null,"null","broken",JSON.stringify({...plan,preview_only:false}),"x".repeat(4*1024*1024+1)])
    assert.throws(()=>app.copyPlan(event,bad));
  assert.throws(()=>app.copyPlan({...event,sender:{}},text));
  assert.throws(()=>app.copyPlan({...event,senderFrame:{url:app.contents.mainFrame.url}},text));
  app.contents.mainFrame.url="https://evil.example/";
  assert.throws(()=>app.copyPlan(event,text));
  assert.deepEqual(app.copied,[text]);
});

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

test("editor opens only a fixed local route, trusts its own main frame, and revokes on close",()=>{
  const app=loadMain(),event={sender:app.contents,senderFrame:app.contents.mainFrame};
  for(const bad of [null,{},'../secrets','https://evil.example','a'.repeat(33)])assert.throws(()=>app.editor(event,bad));
  assert.throws(()=>app.editor({...event,sender:{}},''));
  assert.equal(app.editor(event,'a'.repeat(32)),true);
  const editor=app.windows[1],second={sender:editor.webContents,senderFrame:editor.webContents.mainFrame};
  assert.equal(editor.loaded,'http://127.0.0.1:4747/route-editor?draft='+'a'.repeat(32));
  assert.equal(app.token(second),'test-token-not-a-real-secret');
  assert.throws(()=>app.token({...second,senderFrame:{url:second.senderFrame.url}}));
  assert.throws(()=>app.editor(second,''));
  assert.equal(app.editor(event,''),false);assert.equal(app.windows.length,2);
  second.senderFrame.url='https://evil.example/';assert.throws(()=>app.token(second));
  second.senderFrame.url='http://127.0.0.1:4747/route-editor';
  Object.defineProperty(editor,'webContents',{get(){throw new Error('Object has been destroyed');}});
  editor.events.closed.forEach(fn=>fn());assert.throws(()=>app.token(second));
  assert.equal(app.editor(event,''),true);assert.equal(app.windows.length,3);
});

test('alert sound IPC rejects untrusted frames and only exposes a bounded test beep',()=>{
  const app=loadMain();const event={sender:app.contents,senderFrame:app.contents.mainFrame};
  assert.equal(app.testSound(event),true);assert.equal(app.testSound(event),false);
  assert.throws(()=>app.testSound({...event,sender:{}}));
  assert.throws(()=>app.testSound({...event,senderFrame:{url:event.senderFrame.url}}));
  app.contents.mainFrame.url='https://evil.example/';assert.throws(()=>app.testSound(event));
});
