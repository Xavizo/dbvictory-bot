const { app, BrowserWindow, ipcMain } = require("electron");
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

function createWindow() {
  const win = new BrowserWindow({
    width: 820,
    height: 640,
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

  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  for (const eventName of ["will-navigate", "will-redirect"]) {
    win.webContents.on(eventName, (event, url) => {
      if (!isDashboardUrl(url)) event.preventDefault();
    });
  }
  win.webContents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  win.webContents.session.setPermissionCheckHandler(() => false);
  ipcMain.handle("dbv:dashboard-token", (event) => {
    if (event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame ||
        !isDashboardUrl(event.senderFrame.url) || !sessionToken) {
      throw new Error("Open the dashboard through the bot's start_bot tool.");
    }
    return sessionToken;
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

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  app.quit();
});
