const { contextBridge, ipcRenderer } = require("electron");

ipcRenderer.on("desktop:inactive-traffic-lights", (_event, appearance) => {
  const root = document.documentElement;
  if (appearance === "native") root?.removeAttribute("data-inactive-traffic-lights");
  else root?.setAttribute("data-inactive-traffic-lights", appearance);
});

contextBridge.exposeInMainWorld("shiritaiDesktop", {
  platform: process.platform,
  action: (name) => ipcRenderer.send("desktop:action", name),
  preferences: () => ipcRenderer.invoke("desktop:preferences"),
  savePreference: (key, value) => ipcRenderer.invoke("desktop:save-preferences", key, value),
  selectFolder: (current) => ipcRenderer.invoke("desktop:folder", "workspace", current),
  selectDataFolder: (current) => ipcRenderer.invoke("desktop:folder", "data", current),
  onStartupProgress: (listener) => {
    ipcRenderer.on("desktop:startup-progress", (_event, stage, indexHost) => listener(stage, indexHost));
  },
  onMaximized: (listener) => {
    const receive = (_event, maximized) => listener(maximized);
    ipcRenderer.on("desktop:maximized", receive);
    return () => ipcRenderer.removeListener("desktop:maximized", receive);
  },
});
