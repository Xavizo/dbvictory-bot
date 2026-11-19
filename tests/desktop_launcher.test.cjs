"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs/promises");
const test = require("node:test");
const vm = require("node:vm");
const {EventEmitter} = require("node:events");
const {pathToFileURL} = require("node:url");
const {history, windowPid} = require("../launcher/desktop/host");

test("a clean installation gets readable notes up to its installed version", async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "dbv-notes-"));
  t.after(() => fs.rm(root, {recursive:true, force:true}));
  assert.deepEqual((await history(root, "v1.2.0 (example)")).map(r => r.version), ["v1.2.0", "v1.1.0"]);
  const directory = path.join(root, "launcher", "desktop");
  await fs.mkdir(directory, {recursive:true});
  const file = path.join(directory, "release-notes.json");
  const notes = ["v1.10.0", "v1.9.0", "v1.8.9"].map(version => ({version, changes:[{title:"Zmiana",detail:"Opis"}]}));
  await fs.writeFile(file, JSON.stringify(notes));
  assert.deepEqual(await history(root, "v1.9.0 (abcdef0)"), notes.slice(1));
  await fs.writeFile(file, "invalid");
  await assert.rejects(history(root, "v1.9.0"), SyntaxError);
});

test("client detection requires a window in the selected installation", () => {
  const folder = path.resolve("DBVictory");
  const proc = {Id:4, Path:path.join(folder, "dbvStart.exe"), HasWindow:true};
  const other = {...proc, Id:7, Path:path.join(folder, "other", "dbvStart_gl.exe")};
  assert.equal(windowPid([other, {...proc, HasWindow:false}], folder), null);
  assert.equal(windowPid([other, proc], folder.toUpperCase()), 4);
  assert.throws(() => windowPid([proc, {...proc, Id:8}], folder), {code:"MULTIPLE_CLIENTS"});
});

test("history preview shows seven titles across releases and keeps all details", async () => {
  const desktop = path.resolve(__dirname,"../launcher/desktop");
  class Element {
    children = [];
    style = {setProperty(){}};
    append(...items){this.children.push(...items);}
    replaceChildren(){this.children=[];}
    get childElementCount(){return this.children.length;}
    setAttribute(){}
    addEventListener(){}
  }
  const nodes = new Map();
  const document = {createElement:()=>new Element(),querySelector(selector){
    if(!nodes.has(selector))nodes.set(selector,new Element());
    return nodes.get(selector);
  }};
  const env = vm.createContext({document,window:{desktop:{onState(){},state:()=>({then(){}})}}});
  for(const file of ["effects.js","render.js"])
    vm.runInContext(await fs.readFile(path.join(desktop,file),"utf8"),env);
  const releases = JSON.parse(await fs.readFile(path.join(desktop,"release-notes.json"),"utf8"));
  env.drawHistory(releases);
  const list=nodes.get("#changes");
  assert.deepEqual(list.children.map(li=>li.children[1].textContent),
    releases.flatMap(r=>r.changes).slice(0,7).map(c=>c.title));
  assert.deepEqual(list.children.map(li=>li.children[0].childElementCount),[1,2,3,4,5,6,7]);
  assert.equal(nodes.get("#releases").childElementCount,releases.length);
  env.drawHistory([]);
  assert.equal(list.childElementCount,0);
});

// Exercise the same Electron IPC handlers as the Start button, with no real
// client, processes, user settings or timers.
async function launcherWithClient(initialPid) {
  const desktop = path.resolve(__dirname, "../launcher/desktop");
  const folder = path.resolve("DBVictory");
  const handlers = new Map();
  const proc = new EventEmitter();
  const stub = {pid:initialPid, opens:0, writes:[], ended:false};
  proc.stdin = {write:data=>stub.writes.push(data), end:()=>{stub.ended=true;}};
  const host = {
    clients:async()=>stub.pid ? [{Id:stub.pid, Path:path.join(folder,"dbvStart.exe"), HasWindow:true}] : [],
    clientExe:async()=>path.join(folder,"dbvStart.exe"),
    openWindowPid:async()=>stub.pid,
    openClient:async()=>{stub.opens++; stub.pid=20;},
    checkRelease:async()=>({state:"current"}), history:async()=>[],
    interactive:(_root,_id,_rev,onLine)=>{stub.onLine=onLine; return proc;}
  };
  let ready;
  const app = {setName(){},setPath(){},getPath:()=>folder,requestSingleInstanceLock:()=>true,
    on(){},whenReady:()=>({then:callback=>{ready=callback();}})};
  const webContents = {send(_channel,state){stub.view=state;}, mainFrame:{}, on(){},
    setWindowOpenHandler(){},session:{setPermissionRequestHandler(){}}};
  class TestWindow extends EventEmitter {
    constructor(){super(); this.webContents=webContents; stub.window=this;}
    isDestroyed(){return false;}
    loadFile(file){webContents.mainFrame.url=pathToFileURL(file).href;}
    show(){}
  }
  const substitutes = {
    electron:{app,BrowserWindow:TestWindow,ipcMain:{handle:(name,callback)=>handlers.set(name,callback)}},
    "./host":host, "node:fs/promises":{readFile:async()=>JSON.stringify({folder}),mkdir:async()=>{}}
  };
  const context = {require:name=>substitutes[name] ?? require(name), __dirname:desktop, process, performance,
    setInterval:()=>0, setTimeout:()=>0, clearInterval(){}, clearTimeout(){}};
  vm.runInNewContext(await fs.readFile(path.join(desktop,"main.js"),"utf8"),context);
  await ready;
  stub.onLine("Naciśnij Enter, gdy postać jest wylogowana");
  stub.start=()=>handlers.get("launcher:act")({sender:webContents,senderFrame:webContents.mainFrame},"start");
  stub.host=host;
  return stub;
}

test("opening the game waits at Start until the user confirms the character list", async () => {
  const client=await launcherWithClient(null);
  await client.start();
  assert.equal(client.opens,1);
  assert.equal(client.view.phase,"ready");
  assert.match(client.view.text,/liście postaci/);
  assert.deepEqual(client.writes,[]);
  await Promise.all([client.start(),client.start()]);
  assert.equal(client.view.phase,"starting");
  assert.deepEqual(client.writes,["\n"]);
});

test("Start accepts an already-open client in one click", async () => {
  const client=await launcherWithClient(17);
  await client.start();
  assert.equal(client.opens,0);
  assert.equal(client.view.phase,"starting");
  assert.deepEqual(client.writes,["\n"]);
});

test("a replacement client must be confirmed again on the character list", async () => {
  const client=await launcherWithClient(null);
  await client.start();
  client.pid=42;
  await client.start();
  assert.equal(client.view.phase,"ready");
  assert.deepEqual(client.writes,[]);
  await client.start();
  assert.equal(client.opens,1);
  assert.deepEqual(client.writes,["\n"]);
});

test("closing the launcher while the game is opening cannot advance startup", async () => {
  const client=await launcherWithClient(null);
  client.host.openClient=async()=>{
    client.window.emit("close",{preventDefault(){}});
    client.pid=20;
  };
  await client.start();
  assert.equal(client.ended,true);
  assert.deepEqual(client.writes,[]);
});
