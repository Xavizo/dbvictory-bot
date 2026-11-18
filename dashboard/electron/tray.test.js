const test = require("node:test");
const assert = require("node:assert/strict");
const { installTray } = require("./tray");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function setup(exitFile) {
  const events = {}, winEvents = {}, trayEvents = {};
  const win = { destroyed:false, hidden:false, minimized:false, focusCount:0,
    on: (name, fn) => { winEvents[name] = fn; }, isDestroyed(){return this.destroyed;},
    isMinimized(){return this.minimized;}, restore(){this.minimized=false;},
    hide(){this.hidden=true;}, show(){this.hidden=false;}, focus(){this.focusCount++;} };
  const editor = { hidden:false, destroyed:false, isDestroyed(){return this.destroyed;},
    isVisible(){return !this.hidden;}, hide(){this.hidden=true;}, show(){this.hidden=false;} };
  let quitCount = 0, menu, tray;
  const app = { on:(name,fn)=>{events[name]=fn;}, quit:()=>{quitCount++;events['before-quit']();} };
  const errors = [];
  installTray({app, dialog:{showErrorBox:(...args)=>errors.push(args)}, Menu:{buildFromTemplate:value=>(menu=value)},
    Tray:class { constructor(){tray=this;this.destroyed=false;} setToolTip(){} setContextMenu(){}
      on(name,fn){trayEvents[name]=fn;} destroy(){this.destroyed=true;} },
    nativeImage:{createFromPath:()=>({isEmpty:()=>false})}},win,()=>editor,exitFile);
  return {win,editor,events,winEvents,trayEvents,menu,tray,errors,quitCount:()=>quitCount};
}

test("X hides panel and editor without destroying their state; tray restores both",()=>{
  const s=setup();let prevented=false;
  s.winEvents.close({preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(s.win.hidden,true);assert.equal(s.editor.hidden,true);
  assert.equal(s.win.destroyed,false);assert.equal(s.quitCount(),0);
  s.win.minimized=true;s.trayEvents.click();
  assert.equal(s.win.hidden,false);assert.equal(s.editor.hidden,false);assert.equal(s.win.minimized,false);
  assert.equal(s.win.focusCount,1);
});

test("only explicit tray exit permits closing and releases the tray",()=>{
  const s=setup();s.menu.find(row=>row.label==='Zamknij panel').click();
  assert.equal(s.quitCount(),1);let prevented=false;
  s.winEvents.close({preventDefault(){prevented=true;}});
  assert.equal(prevented,false);s.events['will-quit']();assert.equal(s.tray.destroyed,true);
});

test("restoring panel does not revive an editor that was already closed",()=>{
  const s=setup();s.editor.destroyed=true;s.editor.hidden=true;
  s.winEvents.close({preventDefault(){}});s.menu[0].click();
  assert.equal(s.editor.hidden,true);assert.equal(s.win.hidden,false);
});

test("hidden panel can exit its owner, while panel-only quit leaves the engine running", t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dbv-tray-test-"));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const file = path.join(dir, "quit");
  const panel = setup(file);
  panel.menu.find(row => row.label === "Zamknij panel").click();
  assert.equal(fs.existsSync(file), false);
  const s = setup(file);
  s.winEvents.close({preventDefault() {}});
  const item = s.menu.find(row => row.label === "Zakończ bota");
  assert.equal(item.enabled, true);
  item.click();
  item.click();
  assert.equal(fs.existsSync(file), true);
  assert.equal(s.quitCount(), 1);
  assert.deepEqual(s.errors, []);
});

test("failed exit keeps the panel available and an unbound panel cannot end a session", t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dbv-tray-test-"));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const s = setup(path.join(dir, "missing", "quit"));
  const exit = s.menu.find(row => row.label === "Zakończ bota");
  exit.click();
  assert.equal(s.quitCount(), 0);
  assert.equal(s.errors.length, 1);
  fs.mkdirSync(path.join(dir, "missing"));
  exit.click();
  assert.equal(s.quitCount(), 1);
  assert.equal(setup().menu.find(row => row.label === "Zakończ bota").enabled, false);
});
