const test = require("node:test");
const assert = require("node:assert/strict");
const { installTray } = require("./tray");

function setup() {
  const events = {}, winEvents = {}, trayEvents = {};
  const win = { destroyed:false, hidden:false, minimized:false, focusCount:0,
    on: (name, fn) => { winEvents[name] = fn; }, isDestroyed(){return this.destroyed;},
    isMinimized(){return this.minimized;}, restore(){this.minimized=false;},
    hide(){this.hidden=true;}, show(){this.hidden=false;}, focus(){this.focusCount++;} };
  const editor = { hidden:false, destroyed:false, isDestroyed(){return this.destroyed;},
    isVisible(){return !this.hidden;}, hide(){this.hidden=true;}, show(){this.hidden=false;} };
  let quitCount = 0, menu, tray;
  const app = { on:(name,fn)=>{events[name]=fn;}, quit:()=>{quitCount++;events['before-quit']();} };
  installTray({app, Menu:{buildFromTemplate:value=>(menu=value)},
    Tray:class { constructor(){tray=this;this.destroyed=false;} setToolTip(){} setContextMenu(){}
      on(name,fn){trayEvents[name]=fn;} destroy(){this.destroyed=true;} },
    nativeImage:{createFromPath:()=>({isEmpty:()=>false})}},win,()=>editor);
  return {win,editor,events,winEvents,trayEvents,menu,tray,quitCount:()=>quitCount};
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
