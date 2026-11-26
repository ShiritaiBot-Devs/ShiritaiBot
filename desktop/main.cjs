const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, Notification, shell, Tray } = require("electron");
const { spawn } = require("node:child_process");
const { mkdir, readFile, readdir } = require("node:fs/promises");
const { delimiter, dirname, join } = require("node:path");
const { pathToFileURL } = require("node:url");
const { createInterface } = require("node:readline");
const { closeBehaviors, dataFolder, loadPreferences, writePreferences } = require("./preferences.cjs");

const mac = process.platform === "darwin";
const windows = process.platform === "win32";
const runtime = join(app.isPackaged ? process.resourcesPath : join(__dirname, "build"), "runtime");
const bootstrap = join(runtime, "backend.py");
const startup = join(__dirname, "startup.html");
const startupUrl = pathToFileURL(startup).href;
const icon = app.isPackaged
  ? join(process.resourcesPath, "icon.png")
  : join(__dirname, "build", "icons", "icon.png");
const releases = "https://github.com/ShiritaiBot-Devs/ShiritaiBot/releases/latest";
const chinese = () => app.getLocale().startsWith("zh");
let window;
let child;
let origin;
let port = 0;
let quitting = false;
let stopped = false;
let preferences;
let activeDirectory;
let tray;
let asking = false;
let saving = Promise.resolve();
const tr = (zh, en) => chinese() ? zh : en;

function savePreferences(patch) {
  const update = saving.then(async () => {
    const next = { ...preferences, ...patch };
    await writePreferences(app.getPath("userData"), next);
    preferences = next;
  });
  saving = update.catch(console.error);
  return update;
}

function showWindow() {
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

function hideInTray() {
  if (!tray) {
    tray = new Tray(nativeImage.createFromPath(icon).resize({ width: 20, height: 20 }));
    tray.setToolTip("ShiritaiBot");
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: tr("打开 ShiritaiBot", "Open ShiritaiBot"), click: showWindow },
      { label: tr("退出", "Quit"), click: () => app.quit() },
    ]));
    tray.on("click", showWindow);
  }
  window.hide();
}

async function closeWindow() {
  if (asking) return;
  let behavior = preferences.closeBehavior;
  if (behavior === "ask") {
    asking = true;
    try {
      const result = await dialog.showMessageBox(window, {
        title: "ShiritaiBot",
        message: tr("关闭窗口时要执行什么操作？", "What should happen when you close the window?"),
        buttons: [tr("最小化到托盘", "Minimize to tray"), tr("退出", "Quit"), tr("取消", "Cancel")],
        defaultId: 0,
        cancelId: 2,
        checkboxLabel: tr("将此操作设为默认", "Remember my choice"),
      });
      if (result.response === 2) return;
      behavior = result.response === 0 ? "tray" : "quit";
      if (result.checkboxChecked) await savePreferences({ closeBehavior: behavior });
    } finally {
      asking = false;
    }
  }
  if (behavior === "tray") hideInTray();
  else app.quit();
}

function notifyTask(status) {
  if (!preferences.notifications || !Notification.isSupported()) return;
  const notification = new Notification({
    title: "ShiritaiBot",
    body: status === "completed" ? tr("会话任务已完成", "Chat task completed") : tr("会话任务失败", "Chat task failed"),
    icon,
  });
  notification.on("click", showWindow);
  notification.show();
}

function external(url) {
  if (["https:", "http:", "mailto:"].includes(new URL(url).protocol)) {
    void shell.openExternal(url).catch(console.error);
  }
}

function trusted(url) {
  return url === startupUrl || Boolean(origin && new URL(url).origin === origin);
}

function createWindow() {
  window = new BrowserWindow({
    width: 1340,
    height: 880,
    minWidth: 920,
    minHeight: 640,
    title: "ShiritaiBot",
    backgroundColor: "#f7faff",
    icon,
    roundedCorners: true,
    ...(mac
      ? { titleBarStyle: "hidden", trafficLightPosition: { x: 18, y: 14 } }
      : { frame: false }),
    webPreferences: {
      preload: join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      void closeWindow().catch((error) => dialog.showErrorBox("ShiritaiBot", error.message));
    }
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (!trusted(url)) {
      event.preventDefault();
      external(url);
    }
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    external(url);
    return { action: "deny" };
  });
  window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  const sendState = () => window.webContents.send("desktop:maximized", window.isMaximized());
  window.on("maximize", sendState);
  window.on("unmaximize", sendState);
  void window.loadFile(startup);
}

function runPython(python, args, home, onReport) {
  const processHandle = spawn(python, ["-u", bootstrap, ...args], {
    cwd: home,
    windowsHide: true,
    env: {
      ...process.env,
      PATH: [dirname(python), runtime, process.env.PATH].filter(Boolean).join(delimiter),
      UV: join(runtime, windows ? "uv.exe" : "uv"),
      UV_CACHE_DIR: join(home, "runtime", "cache"),
      UV_PYTHON_DOWNLOADS: "never",
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONIOENCODING: "utf-8",
    },
  });
  child = processHandle;
  let diagnostics = "";
  const output = createInterface({ input: processHandle.stdout });
  output.on("line", (line) => {
    const prefix = "SHIRITAI_DESKTOP ";
    if (line.startsWith(prefix)) onReport(JSON.parse(line.slice(prefix.length)));
  });
  processHandle.stderr.setEncoding("utf8").on("data", (chunk) => {
    diagnostics = (diagnostics + chunk).slice(-6000);
  });
  processHandle.stdin.on("error", () => {});
  return new Promise((resolve, reject) => {
    processHandle.once("error", reject);
    processHandle.once("close", (code) => {
      if (child === processHandle) child = undefined;
      if (quitting || code === 0 || code === 75) resolve(code);
      else reject(new Error(diagnostics.trim() || `Python exited with code ${code}.`));
    });
  });
}

async function runBackend() {
  const home = activeDirectory;
  const distributions = await readdir(join(runtime, "python"), { withFileTypes: true });
  const distribution = distributions.find((item) => item.isDirectory() && item.name.startsWith("cpython-"));
  if (!distribution) throw new Error("The bundled Python runtime is missing.");
  const bootstrapPython = join(runtime, "python", distribution.name, windows ? "python.exe" : "bin/python3.13");
  while (!quitting) {
    let python;
    await runPython(bootstrapPython, ["prepare", home], home, (message) => { python = message.python; });
    if (quitting) return;
    if (!python) throw new Error("Python did not report a runtime environment.");
    const exitCode = await runPython(python, ["serve", home, String(port)], home, (message) => {
      if (message.taskFinished) {
        notifyTask(message.taskFinished);
        return;
      }
      port = message.port;
      if (!origin) {
        origin = `http://127.0.0.1:${port}`;
        void readFile(join(home, "web.token"), "utf8").then((token) => {
          if (!quitting) return window.loadURL(`${origin}/token#${new URLSearchParams({ token: token.trim() })}`);
        }).catch(showError);
      }
    });
    if (exitCode !== 75 && !quitting) throw new Error("The local server stopped unexpectedly.");
  }
}

function showError(error) {
  if (quitting) return;
  dialog.showErrorBox(chinese() ? "ShiritaiBot 启动失败" : "ShiritaiBot could not start", error.message);
  app.quit();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (window) {
      showWindow();
    }
  });
  app.whenReady().then(async () => {
    if (windows) app.setAppUserModelId("dev.shiritaibot.desktop");
    const userData = app.getPath("userData");
    await mkdir(userData, { recursive: true });
    preferences = await loadPreferences(userData);
    activeDirectory = await dataFolder(preferences.dataDirectory, process.execPath);
    function validSender(event) {
      return event.sender === window?.webContents && event.senderFrame === event.sender.mainFrame
        && trusted(event.senderFrame.url);
    }
    const settings = () => ({ ...preferences, activeDirectory });
    ipcMain.handle("desktop:preferences", (event) => {
      if (validSender(event)) return settings();
    });
    ipcMain.handle("desktop:save-preferences", async (event, key, value) => {
      if (!validSender(event)) return;
      if ((key === "closeBehavior" && closeBehaviors.has(value)) ||
        (key === "notifications" && typeof value === "boolean")) {
        await savePreferences({ [key]: value });
      }
      return settings();
    });
    ipcMain.handle("desktop:folder", async (event, purpose, current) => {
      if (!validSender(event) || !["data", "workspace"].includes(purpose)) return;
      const result = await dialog.showOpenDialog(window, {
        title: purpose === "data" ? tr("选择数据目录", "Choose the data directory") : tr("选择工作目录", "Choose a workspace"),
        defaultPath: typeof current === "string" ? current : undefined,
        properties: ["openDirectory", "createDirectory"],
      });
      if (result.canceled) return null;
      if (purpose === "workspace") return result.filePaths[0];
      const directory = await dataFolder(result.filePaths[0], process.execPath);
      await savePreferences({ dataDirectory: directory });
      return settings();
    });
    if (!mac) Menu.setApplicationMenu(null);
    ipcMain.on("desktop:action", (event, action) => {
      if (!validSender(event)) return;
      switch (action) {
        case "minimize": window.minimize(); break;
        case "maximize": window.isMaximized() ? window.unmaximize() : window.maximize(); break;
        case "close": window.close(); break;
        case "releases": external(releases); break;
        case "notification-settings":
          if (windows) void shell.openExternal("ms-settings:notifications").catch(console.error);
          break;
        case "relaunch": app.relaunch(); app.quit(); break;
      }
    });
    createWindow();
    return runBackend();
  }).catch(showError);
  app.on("activate", () => { if (window) showWindow(); });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (stopped || !child) {
      quitting = true;
      return;
    }
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    const active = child;
    const timeout = setTimeout(() => active.kill(), 13000);
    active.once("close", () => {
      clearTimeout(timeout);
      stopped = true;
      app.quit();
    });
    active.stdin.end();
  });
}
