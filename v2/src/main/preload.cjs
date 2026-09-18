const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("niraiDashboard", {
  snapshot: () => ipcRenderer.invoke("nirai:snapshot"),
  command: (envelope) => ipcRenderer.invoke("nirai:command", envelope),
  onSnapshotChanged: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = (_event, snapshot) => listener(snapshot);
    ipcRenderer.on("nirai:snapshot-changed", handler);
    return () => ipcRenderer.removeListener("nirai:snapshot-changed", handler);
  },
  onHubDisconnected: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = () => listener();
    ipcRenderer.on("nirai:hub-disconnected", handler);
    return () => ipcRenderer.removeListener("nirai:hub-disconnected", handler);
  },
});
