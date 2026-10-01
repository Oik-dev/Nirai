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
  holoSurface: (request) => ipcRenderer.invoke("nirai:holo-surface", request),
  holoHide: () => ipcRenderer.invoke("nirai:holo-hide"),
  onHoloChatClose: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = () => listener();
    ipcRenderer.on("nirai:holo-chat-close", handler);
    return () => ipcRenderer.removeListener("nirai:holo-chat-close", handler);
  },
  readAvatar: (residentId) => ipcRenderer.invoke("nirai:avatar-read", residentId),
  reportAvatar: (residentId, token, report) => ipcRenderer.invoke("nirai:avatar-report", residentId, token, report),
  selectAvatar: (residentId, clear = false) => ipcRenderer.invoke("nirai:avatar-select", residentId, clear),
  selectPersona: (residentId, clear = false) => ipcRenderer.invoke("nirai:persona-select", residentId, clear),
  refreshConversationProvider: (providerId) => ipcRenderer.invoke("nirai:conversation-provider-refresh", providerId),
  onSnapshotChanged: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = (_event, snapshot) => listener(snapshot);
    ipcRenderer.on("nirai:snapshot-changed", handler);
    return () => ipcRenderer.removeListener("nirai:snapshot-changed", handler);
  },
  onHoloPresentationChanged: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = (_event, presentation) => listener(presentation);
    ipcRenderer.on("nirai:holo-presentation", handler);
    return () => ipcRenderer.removeListener("nirai:holo-presentation", handler);
  },
  onHubDisconnected: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = () => listener();
    ipcRenderer.on("nirai:hub-disconnected", handler);
    return () => ipcRenderer.removeListener("nirai:hub-disconnected", handler);
  },
});
