const { contextBridge, ipcRenderer } = require("electron");

// Expose one narrow operation, never the IPC module or Node APIs.
contextBridge.exposeInMainWorld("dbvAuth", {
  getToken: () => ipcRenderer.invoke("dbv:dashboard-token"),
});
