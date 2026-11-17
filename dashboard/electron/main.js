const electron = require("electron");
const { app, BrowserWindow, ipcMain, clipboard } = electron;
const { installTray } = require("./tray");
const { startAlertSounds } = require("./alert-sounds");
const { createNotificationAudio } = require("./notification-audio");
const path = require("node:path");

const NEXT_URL = "http://127.0.0.1:4747";
const sessionToken = process.env.DBV_DASHBOARD_TOKEN;
delete process.env.DBV_DASHBOARD_TOKEN;

function isDashboardUrl(url) {
  try { return new URL(url).origin === NEXT_URL; }
  catch { return false; }
}

const LOADING_HTML = `data:text/html,
<html>
<body style="margin:0;background:#111827;color:#9ca3af;display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui">
  <div style="text-align:center">
    <h2 style="color:#e5e7eb;margin-bottom:8px">DBVictory Bot</h2>
    <p id="msg">Starting dashboard...</p>
  </div>
  <script>
    let dots = 0;
    setInterval(() => {
      dots = (dots + 1) % 4;
      document.getElementById("msg").textContent = "Starting dashboard" + ".".repeat(dots);
    }, 400);
  </script>
</body>
</html>`;

const trustedContents = new Set();
let editorWindow = null;
function trustWindow(win) {
  const contents = win.webContents;
  trustedContents.add(contents);
  win.on("closed", () => trustedContents.delete(contents));
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  for (const eventName of ["will-navigate", "will-redirect"]) {
    win.webContents.on(eventName, (event, url) => {
      if (!isDashboardUrl(url)) event.preventDefault();
    });
  }
  win.webContents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  win.webContents.session.setPermissionCheckHandler(() => false);
}
function trustedSender(event) {
  return trustedContents.has(event.sender) && event.senderFrame === event.sender.mainFrame &&
    isDashboardUrl(event.senderFrame.url) && !!sessionToken;
}
function createWindow() {
  const win = new BrowserWindow({
    width: 1000,
    height: 740,
    minWidth: 720,
    minHeight: 540,
    title: "DBVictory Bot",
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  trustWindow(win);
  installTray(electron, win, () => editorWindow);
  win.on("closed", () => { if (editorWindow && !editorWindow.isDestroyed()) editorWindow.close(); });
  ipcMain.handle("dbv:open-route-editor", (event, draftId) => {
    if (!trustedSender(event) || event.sender !== win.webContents) throw new Error("Only the main dashboard may open its editor.");
    if (typeof draftId !== "string" || (draftId !== "" && !/^[a-f0-9]{32}$/.test(draftId))) throw new Error("Invalid route ID.");
    if (editorWindow && !editorWindow.isDestroyed()) { editorWindow.restore(); editorWindow.focus(); return false; }
    editorWindow = new BrowserWindow({width:1200,height:850,minWidth:820,minHeight:600,title:"DBVictory — Edytor trasy",autoHideMenuBar:true,
      webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,preload:path.join(__dirname,"preload.js")}});
    const editor=editorWindow; trustWindow(editor);
    editor.on("closed",()=>{if(editorWindow===editor)editorWindow=null;});
    editor.loadURL(`${NEXT_URL}/route-editor?draft=${encodeURIComponent(draftId)}`);
    return true;
  });
  ipcMain.handle("dbv:dashboard-token", (event) => {
    if (!trustedSender(event)) {
      throw new Error("Open the dashboard through the bot's start_bot tool.");
    }
    return sessionToken;
  });
  // Write-only, bounded text operation for the explicit map Copy button.
  ipcMain.handle("dbv:copy-map-cell", (event, text) => {
    if (!trustedSender(event)) {
      throw new Error("Only the trusted dashboard can copy map data.");
    }
    if (typeof text !== "string" || text.length > 16384 || text.includes("\0") ||
        !/^Pole \(\d{1,5}, \d{1,5}, \d{1,2}\) — /.test(text)) {
      throw new Error("Invalid map text.");
    }
    clipboard.writeText(text);
    return true;
  });
  ipcMain.handle("dbv:copy-map-plan", (event, text) => {
    if (!trustedSender(event)) {
      throw new Error("Only the trusted dashboard can copy a route preview.");
    }
    if (typeof text !== "string" || text.length > 4 * 1024 * 1024 || text.includes("\0")) {
      throw new Error("Invalid route preview.");
    }
    const plan = JSON.parse(text);
    if (!plan || plan.preview_only !== true || typeof plan.scope !== "string" ||
        !Array.isArray(plan.path) || plan.path.length > 40000 ||
        !Array.isArray(plan.points) || plan.points.length > 128) {
      throw new Error("Invalid route preview.");
    }
    clipboard.writeText(text);
    return true;
  });

  // Show loading screen immediately
  win.loadURL(LOADING_HTML);

  // Poll until Next.js is ready, then load
  const tryLoad = () => {
    if (win.isDestroyed()) return;
    fetch(NEXT_URL)
      .then((res) => {
        if (win.isDestroyed()) return;
        if (res.ok) win.loadURL(NEXT_URL);
        else setTimeout(tryLoad, 500);
      })
      .catch(() => setTimeout(tryLoad, 500));
  };
  tryLoad();
}

app.whenReady().then(() => {
  createWindow();
  const audio = createNotificationAudio({fallback:()=>electron.shell.beep()});
  const sounds = startAlertSounds({token:sessionToken, fetchImpl:fetch, beep:kind=>audio.play(kind), setTimer:setTimeout});
  ipcMain.handle("dbv:test-alert-sound", event => {
    if (!trustedSender(event)) throw new Error("Untrusted sender");
    return sounds.testSound();
  });
  app.on("before-quit", () => {sounds.stop();audio.stop();});
});

app.on("window-all-closed", () => {
  app.quit();
});
