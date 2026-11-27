"use strict";
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("desktop", {
  state: () => ipcRenderer.invoke("launcher:state"),
  act: (action) => ipcRenderer.invoke("launcher:act", action),
  mcp: (operation, target) => ipcRenderer.invoke("launcher:mcp", operation, target),
  onState: (fn) => ipcRenderer.on("launcher:changed", (_event, snapshot) => fn(snapshot)),
});
