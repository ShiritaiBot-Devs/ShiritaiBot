const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("shiritaiDesktop", {
  platform: process.platform,
  action: (name) => ipcRenderer.send("desktop:action", name),
  preferences: () => ipcRenderer.invoke("desktop:preferences"),
  savePreference: (key, value) => ipcRenderer.invoke("desktop:save-preferences", key, value),
  selectFolder: (current) => ipcRenderer.invoke("desktop:folder", "workspace", current),
  selectDataFolder: (current) => ipcRenderer.invoke("desktop:folder", "data", current),
  onMaximized: (listener) => {
    const receive = (_event, maximized) => listener(maximized);
    ipcRenderer.on("desktop:maximized", receive);
    return () => ipcRenderer.removeListener("desktop:maximized", receive);
  },
});
