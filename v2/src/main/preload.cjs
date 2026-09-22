const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("niraiDashboard", {
  snapshot: () => ipcRenderer.invoke("nirai:snapshot"),
  command: async (envelope) => {
    let response;
    try {
      response = await ipcRenderer.invoke("nirai:command", envelope);
    } catch (error) {
      throw new Error(`transport: ${error?.message ?? error}`);
    }
    if (!response?.ok) throw Object.assign(new Error(`${response?.code ?? "invalid"}: ${response?.error ?? "Hub command failed"}`), {
      code: response?.code, current: response?.current,
    });
    return response.result;
  },
  commandReceipt: (commandId) => ipcRenderer.invoke("nirai:command-receipt", commandId),
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
