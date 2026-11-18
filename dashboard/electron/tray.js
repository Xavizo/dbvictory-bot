const path = require("node:path");

// Hiding keeps renderer state, including unsaved sketches, in the same window.
// This process owns only the panel, never the game connection or automation.
function installTray({ app, Tray, Menu, nativeImage }, win, getEditor) {
  const icon = nativeImage.createFromPath(path.join(__dirname, "tray.png"));
  if (icon.isEmpty()) throw new Error("Missing dashboard tray icon");
  const tray = new Tray(icon);
  let quitting = false;
  let editorWasVisible = false;
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
    { label: "Zamknięcie panelu nie zatrzymuje bota", enabled: false },
    { label: "Zamknij panel", click: () => app.quit() },
  ]));
  tray.on("click", show);
  tray.on("double-click", show);
  return tray;
}

module.exports = { installTray };
