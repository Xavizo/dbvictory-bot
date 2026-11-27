"use strict";
const {app,BrowserWindow,ipcMain,dialog} = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const {randomUUID} = require("node:crypto");
const {pathToFileURL} = require("node:url");
const {setTimeout:delay} = require("node:timers/promises");
const host = require("./host");

// The EXE selects the installation as cwd for both the bootstrap and installed UI.
const root = process.cwd();
const page = pathToFileURL(path.join(__dirname,"index.html")).href;
const readOnly = process.argv.includes("--check");
app.setName("DBVictory Launcher");
app.setPath("userData",path.join(app.getPath("appData"),"DBVictory Launcher"));
const settings = path.join(app.getPath("userData"),"client.json");
let win, child, oldPending, marker, clientPid, poll, deadline;
let locked = false, checkingPanel = false, quitting = false, sessionError = "", awaitingStart = false;
let awaitingConnection = false, recheckOnClose = false;
const waitingForList = "Zaloguj się na konto i pozostaw otwartą listę postaci. Gdy będziesz gotowy, kliknij „Połącz z grą”. Nie wchodź jeszcze na postać.";
let view = {phase:"checking", text:"Sprawdzanie lokalnej kolejki wydań…",version:"",changes:[],
  folder:"",installed:false,clientOpen:false,canDefer:false,readOnly};

function set(phase, text, extra={}) {
  view = {...view,phase,text,canDefer:false,canInstall:false,clientOutdated:false,failedStep:null,...extra};
  if (win && !win.isDestroyed()) win.webContents.send("launcher:changed",view);
}
function reveal() {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show(); win.focus();
}
function stopMonitor() {
  clearInterval(poll); clearTimeout(deadline); checkingPanel=false;
  if (marker) {fs.rm(marker,{force:true}).catch(()=>{});marker=null;}
}
async function clientInfo(folder) {
  const exe = await host.clientExe(folder);
  let windowPid = null;
  if (exe) {
    const list = await host.clients().catch(()=>[]);
    const match = list.find(p=>p.HasWindow && p.Path && path.dirname(p.Path).toLowerCase()===path.resolve(folder).toLowerCase());
    windowPid = match?.Id || null;
  }
  return {folder,installed:!!exe,clientOpen:!!windowPid,windowPid};
}
function showStart(info={}) {
  const text = (info.clientOpen ?? view.clientOpen)
    ? "Kliknij Start, a następnie na liście postaci wybierz „Połącz z grą”."
    : "Start otworzy klienta. Na liście postaci wybierz „Połącz z grą”.";
  set("ready",text,info);
}
async function check(defer, install) {
  set("checking","Sprawdzanie lokalnej kolejki wydań…");
  let report = await host.checkRelease(root,["preflight","--check"]);
  const github = report.source==="github";
  if (!readOnly && defer && report.state==="available" && defer===report.pending && host.validSha(defer)) {
    set("applying","Odkładanie wybranego wydania…");
    report=await host.checkRelease(root,["defer",defer]);
  } else if (!readOnly && install && github && report.state==="available" && install===report.pending && host.validSha(install)) {
    set("applying","Pobieranie i instalowanie aktualizacji z GitHuba… To może potrwać kilka minut.",{version:report.installed_label});
    report=await host.checkRelease(root,["install",install]);
    if (report.state==="updated" && report.restart) {
      set("applying","Zainstalowano aktualizację. Ponowne uruchamianie launchera…");
      await delay(900);
      // The new launcher must load the new runtime; start it through the EXE after this process exits.
      const exe=path.join(root,"DBVictory Launcher.exe");
      app.relaunch(require("node:fs").existsSync(exe)?{execPath:exe,args:[]}:undefined);
      quitting=true;app.exit(0);return;
    }
  } else if (!readOnly && github && report.state==="recovery") {
    set("applying",report.message,{version:report.installed_label});
    report=await host.checkRelease(root,["preflight"]);
  } else if (!readOnly && !github && ["available","recovery"].includes(report.state)) {
    set("applying",report.message,{version:report.installed_label});
    report=await host.checkRelease(root,["preflight"]);
  }
  oldPending=report.pending;
  let changes=[], notesError=false;
  try { changes=await host.history(root,report.installed_label); } catch { notesError=true; }
  const extra={revision:report.installed,version:report.installed_label||"",changes,notesError,canDefer:!!(report.can_defer&&oldPending),
    canInstall:report.source==="github"&&report.state==="available"&&!!oldPending&&!readOnly,
    pendingLabel:report.pending_label||""};
  if (report.state==="updated"||report.state==="current") {
    if (readOnly) set("ready","Sprawdzenie zakończone.",extra);
    else {
      set("preparing","Przygotowywanie launchera…",extra);
      await preparePrompt();
    }
  } else {
    const queued=report.state==="busy" && report.pending_label ? " W kolejce: "+report.pending_label+"." : "";
    const state=["busy","available","recovery"].includes(report.state)?report.state:"problem";
    set(state,report.message+queued,extra);
  }
}

function startDeadline(proc) {
  clearTimeout(deadline);
  deadline=setTimeout(()=>{
    if (child===proc && view.phase==="connecting") {
      set("waiting","Silnik nie potwierdził połączenia z klientem.");
      return;
    }
    if (child===proc && ["starting","panel"].includes(view.phase))
      set("waiting",checkingPanel?"Główne okno nie potwierdziło gotowości.":"Silnik nie potwierdził gotowości.",{failedStep:checkingPanel?"panel":"engine"});
  },75000);
}
function watchSession(proc) {
  startDeadline(proc);
  let probing = false;
  poll=setInterval(async()=>{
    if (probing || child!==proc) return;
    probing=true;
    try {
      if (clientPid) {
        try {process.kill(clientPid,0);}
        catch (err) {
          if (err.code==="ESRCH") {
            // A client update can replace its process. Follow only this installation.
            const list=await host.clients();
            if (child!==proc) return;
            const replacement=list.find(p=>p.Path && path.dirname(p.Path).toLowerCase()===view.folder.toLowerCase());
            if (replacement) clientPid=replacement.Id;
            else {clientPid=null;proc.stdin.end();return;}
          }
        }
      }
      if (checkingPanel && marker) {
        try {await fs.access(marker);} catch {return;}
        if (child!==proc) return;
        checkingPanel=false;clearTimeout(deadline);
        set("running","Panel gotowy. Stan zalogowania i odczyty postaci zobaczysz w głównym oknie.");win.hide();
      }
    } catch { /* A temporarily unavailable process listing does not end the session. */ }
    finally {probing=false;}
  },600);
}
async function preparePrompt() {
  if (child || readOnly) return;
  const id=randomUUID().replaceAll("-","");
  const events=path.join(root,".codex","launcher-events");
  await fs.mkdir(events,{recursive:true});
  marker=path.join(events,id+".ready");
  sessionError="";
  const proc=host.interactive(root,id,view.revision,line=>{
    if (child!==proc) return;
    if (line.includes("Naciśnij Enter, gdy postać jest wylogowana")) {
      awaitingStart=true;clearTimeout(deadline);
      showStart();
    } else if (line==="OCZEKIWANIE NA POŁĄCZENIE" && awaitingConnection) {
      clearTimeout(deadline);
      set("prepared", waitingForList);
    } else if (line.startsWith("GOTOWE")) {
      checkingPanel=true;set("panel","Silnik gotowy. Oczekiwanie na główne okno…");
    } else if (line.startsWith("BŁĄD:")||line.startsWith("Błąd launchera:")) {
      sessionError=line.slice(0,440);
    }
  });
  child=proc;
  proc.once("error",()=>{sessionError="Nie udało się otworzyć procesu sesji.";});
  proc.once("close",(code)=>{
    if (child!==proc) return;
    const wasRunning=["running","prepared"].includes(view.phase);
    const failedStep=checkingPanel?"panel":clientPid?"engine":null;
    stopMonitor();child=null;clientPid=null;awaitingStart=false;awaitingConnection=false;
    if (quitting || wasRunning && code===0) {app.quit();return;}
    if (recheckOnClose) {recheckOnClose=false;action("retry");return;}
    reveal();
    if (code===75) set("problem","Pojawiło się nowsze wydanie. Sprawdź aktualizację ponownie.");
    else if (code || sessionError) set("problem",sessionError||"Proces sesji zakończył się błędem.",{failedStep});
    else set("closed","Sesja została zamknięta.");
  });
  deadline=setTimeout(()=>{
    if(child===proc && !awaitingStart){sessionError="Proces przygotowania nie zgłosił gotowości.";proc.stdin.end();set("problem",sessionError);}
  },30000);
}

async function findOrLaunchClient(proc) {
  const folder = view.folder;
  const current = () => !quitting && proc === child && awaitingStart;
  set("detecting","Wykrywanie klienta DBVictory…");
  const existing = await host.openWindowPid(folder);
  if (!current()) return null;
  if (existing) return existing;

  set("detecting","Uruchamianie klienta DBVictory…",{clientOpen:false,windowPid:null});
  await host.openClient(folder);
  if (!current()) return null;

  set("detecting","Oczekiwanie na okno klienta DBVictory…");
  const until = performance.now() + 60000;
  while (current()) {
    const pid = await host.openWindowPid(folder);
    if (!current()) return null;
    if (pid) return pid;
    const remaining = until - performance.now();
    if (remaining <= 0) throw new Error("Okno DBVictory nie pojawiło się w ciągu minuty. Po aktualizacji klienta możesz ponowić próbę.");
    await delay(Math.min(750, remaining));
  }
  return null;
}

async function action(name) {
  if (locked) return;
  locked=true;
  try {
    if (name==="retry" && !child) await check();
    else if (name==="defer" && !child && !readOnly && view.canDefer) await check(oldPending);
    else if (name==="install" && !child && !readOnly && view.canInstall) await check(undefined, oldPending);
    else if (name==="recheck" && child && awaitingStart && ["ready","clientError"].includes(view.phase) && !readOnly) {
      // Close the idle session prompt; its close handler checks for a new release.
      recheckOnClose=true;set("closing","Sprawdzanie aktualizacji…");child.stdin.end();
    }
    else if (name==="start" && awaitingStart && ["ready","clientError"].includes(view.phase) && !readOnly) {
      const proc=child;
      try {
        // A game client update needs a matching bot release: say so before touching the client.
        const support=await host.clientSupport(root,view.folder);
        if (quitting || proc!==child || !awaitingStart) return;
        if (!support.supported) {set("clientError",support.message,{failedStep:"client",clientOutdated:!support.missing});return;}
        const pid=await findOrLaunchClient(proc);
        if (!pid || quitting || proc!==child || !awaitingStart) return;
        clientPid=pid;
        awaitingConnection=true;
      } catch(error) {
        if (!quitting && proc===child) set("clientError",error.message,{failedStep:"client"});
        return;
      }
      awaitingStart=false;
      set("starting","Uruchamianie silnika…",{clientOpen:true,windowPid:clientPid});
      watchSession(proc);
      proc.stdin.write("prepare-login\n");
    } else if (name==="connect" && awaitingConnection && view.phase==="prepared" && !readOnly) {
      const proc=child;
      const confirmedPid=clientPid;
      try {
        // The click confirms the character list. Check only the selected process,
        // never capture the window or infer login state from its contents.
        const pid=await host.openWindowPid(view.folder);
        if (quitting || proc!==child || !awaitingConnection || view.phase!=="prepared") return;
        if (!pid) throw new Error("Nie znaleziono otwartego okna klienta. Otwórz klienta z wybranego folderu i ponownie kliknij „Połącz z grą” na liście postaci.");
        clientPid=pid;
        if (pid!==confirmedPid) {
          set("prepared","Klient został ponownie uruchomiony. Na jego liście postaci kliknij jeszcze raz „Połącz z grą”.",{windowPid:pid});
          return;
        }
      } catch(error) {
        if (!quitting && proc===child) set("prepared",error.message);
        return;
      }
      awaitingConnection=false;
      set("connecting","Łączenie z klientem DBVictory…");
      startDeadline(proc);
      proc.stdin.write("connect\n");
    } else if (name==="cancel" && child && view.phase==="waiting") {
      set("closing","Zamykanie uruchamianej sesji…");child.stdin.end();
    } else if (name==="folder" && awaitingStart && ["ready","clientError"].includes(view.phase) && !readOnly) {
      const result=await dialog.showOpenDialog(win,{title:"Wybierz katalog klienta DBVictory",properties:["openDirectory"]});
      if (result.canceled) return;
      const info=await clientInfo(result.filePaths[0]);
      if (!info.installed) {set("ready","W tym katalogu brakuje dbvStart.exe lub dbvStart_gl.exe.");return;}
      await fs.mkdir(path.dirname(settings),{recursive:true});
      await fs.writeFile(settings+".tmp",JSON.stringify({folder:info.folder}));await fs.rename(settings+".tmp",settings);
      showStart(info);
    }
  } catch (e) {
    if (awaitingStart) set("clientError",e.message,{failedStep:"client"});
    else set("problem",e.message);
  }
  finally {locked=false;}
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance",reveal);
  app.whenReady().then(async()=>{
    win=new BrowserWindow({width:1060,height:670,minWidth:610,minHeight:555,show:false,
      title:"DBVictory Launcher",backgroundColor:"#0b1222",autoHideMenuBar:true,
      titleBarStyle:"hidden",titleBarOverlay:{color:"#101a2b",symbolColor:"#d7e8fb",height:38},
      webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,preload:path.join(__dirname,"bridge.js")}});
    win.webContents.setWindowOpenHandler(()=>({action:"deny"}));
    for (const event of ["will-navigate","will-redirect"]) win.webContents.on(event,e=>e.preventDefault());
    win.webContents.session.setPermissionRequestHandler((_a,_b,cb)=>cb(false));
    const trusted=e=>e.sender===win.webContents && e.senderFrame===win.webContents.mainFrame && e.senderFrame.url===page;
    ipcMain.handle("launcher:state",e=>{if(!trusted(e))throw Error("Nieznane okno.");return view;});
    ipcMain.handle("launcher:act",(e,name)=>{if(!trusted(e))throw Error("Nieznane okno.");return action(name);});
    ipcMain.handle("launcher:mcp",(e,operation,target)=>{
      if(!trusted(e))throw Error("Nieznane okno.");
      if(readOnly&&operation!=="status")throw Error("Tryb tylko do odczytu.");
      return host.mcpClients(root,operation,target);
    });
    win.on("close",e=>{
      if (quitting) return;
      if (locked && view.phase!=="detecting" || ["checking","applying"].includes(view.phase)) {e.preventDefault();return;}
      if (child && ["running","prepared","connecting","starting","panel","waiting"].includes(view.phase)) {e.preventDefault();win.hide();return;}
      if(child){e.preventDefault();quitting=true;child.stdin.end();}
    });
    await win.loadFile(path.join(__dirname,"index.html"));win.show();
    try {
      let folder;
      try{folder=JSON.parse(await fs.readFile(settings,"utf8")).folder;}catch{}
      if (!folder) {
        const list=await host.clients().catch(()=>[]);
        folder=list[0]?.Path ? path.dirname(list[0].Path) : await host.findClientFolder();
      }
      view={...view,...await clientInfo(folder)};
      await action("retry");
    } catch(e){set("problem",e.message);}
  });
  app.on("window-all-closed",()=>app.quit());
}
