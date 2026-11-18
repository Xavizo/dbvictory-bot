const fs = require("node:fs");
const path = require("node:path");

// Hiding keeps renderer state, including unsaved sketches, in the same window.
function installTray({ app, Tray, Menu, nativeImage, dialog }, win, getEditor, exitFile) {
  const icon = nativeImage.createFromPath(path.join(__dirname, "tray.png"));
  if (icon.isEmpty()) throw new Error("Missing dashboard tray icon");
  const tray = new Tray(icon);
  let quitting = false;
  let editorWasVisible = false;
  const exitBot = () => {
    if (!exitFile || quitting) return;
    try {
      fs.writeFileSync(exitFile, "", { flag: "wx" });
    } catch (error) {
      if (error.code !== "EEXIST") {
        dialog.showErrorBox("Zakończ bota", "Nie udało się przekazać żądania zamknięcia do silnika.");
        return;
      }
    }
    app.quit();
  };
  const show = () => {
    if (win.isDestroyed()) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    const editor = getEditor();
    if (editorWasVisible && editor && !editor.isDestroyed()) editor.show();
    editorWasVisible = false;
  };
  win.on("close", event => {
    if (quitting) return;
    event.preventDefault();
    const editor = getEditor();
    editorWasVisible = !!editor && !editor.isDestroyed() && editor.isVisible();
    if (editorWasVisible) editor.hide();
    win.hide();
  });
  app.on("before-quit", () => { quitting = true; });
  app.on("will-quit", () => { tray.destroy(); });
  tray.setToolTip("DBVictory Bot — kliknij, aby pokazać panel");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Pokaż panel", click: show },
    { type: "separator" },
    { label: "Zamknij panel", click: () => app.quit() },
    { label: "Zakończ bota", enabled: Boolean(exitFile), click: exitBot },
  ]));
  tray.on("click", show);
  tray.on("double-click", show);
  return tray;
}

module.exports = { installTray };
