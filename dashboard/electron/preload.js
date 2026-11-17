const { contextBridge, ipcRenderer } = require("electron");

// Expose narrow operations, never the IPC module, clipboard reads or Node APIs.
contextBridge.exposeInMainWorld("dbvAuth", {
  getToken: () => ipcRenderer.invoke("dbv:dashboard-token"),
});
contextBridge.exposeInMainWorld("dbvMap", {
  copyCell: (text) => ipcRenderer.invoke("dbv:copy-map-cell", text),
  copyPlan: (text) => ipcRenderer.invoke("dbv:copy-map-plan", text),
});

contextBridge.exposeInMainWorld("dbvEditor", { open: (draftId) => ipcRenderer.invoke("dbv:open-route-editor", draftId) });

contextBridge.exposeInMainWorld("dbvAlerts", {testSound: () => ipcRenderer.invoke("dbv:test-alert-sound")});
