"use strict";
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("desktop", {
  state: () => ipcRenderer.invoke("launcher:state"),
  act: (action) => ipcRenderer.invoke("launcher:act", action),
  onState: (fn) => ipcRenderer.on("launcher:changed", (_event, snapshot) => fn(snapshot)),
});
