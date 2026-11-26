const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, Notification, screen, shell, Tray } = require("electron");
const { spawn } = require("node:child_process");
const { mkdirSync } = require("node:fs");
const { mkdir, readFile, readdir } = require("node:fs/promises");
const { createConnection } = require("node:net");
const { delimiter, dirname, join } = require("node:path");
const { pathToFileURL } = require("node:url");
const { createInterface } = require("node:readline");
const { setTimeout: delay } = require("node:timers/promises");
const { closeBehaviors, dataFolder, loadPreferences, writePreferences } = require("./preferences.cjs");

const mac = process.platform === "darwin";
const windows = process.platform === "win32";
const trafficLightPosition = { x: 18, y: 14 };
const dev = !app.isPackaged && app.commandLine.hasSwitch("desktop-dev");
const root = dirname(__dirname);
const runtime = join(app.isPackaged ? process.resourcesPath : join(__dirname, "build"), "runtime");
const bootstrap = join(dev ? __dirname : runtime, "backend.py");
const startup = join(__dirname, "startup.html");
const icon = dev ? join(root, "web", "public", "icon.png") : app.isPackaged
  ? join(process.resourcesPath, "icon.png")
  : join(__dirname, "build", "icons", "icon.png");
const startupUrl = pathToFileURL(startup);
startupUrl.searchParams.set("icon", pathToFileURL(icon).href);
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
let devWeb;
let restartBackend = () => {};
let exitCode = 0;
let startupStage = "checking";
let startupIndexHost;
const tr = (zh, en) => chinese() ? zh : en;

function showStartupStage(stage, indexHost) {
  startupStage = stage;
  startupIndexHost = indexHost;
  if (window && !window.isDestroyed() && window.webContents.getURL() === startupUrl.href) {
    window.webContents.send("desktop:startup-progress", stage, indexHost);
  }
}

if (dev) {
  const profile = join(root, ".local", "desktop-dev");
  mkdirSync(profile, { recursive: true });
  app.setPath("userData", profile);
  const endpoint = process.env.SHIRITAI_DESKTOP_DEV_SOCKET;
  if (endpoint) {
    const launcher = createConnection(endpoint);
    launcher.once("close", () => app.quit());
    launcher.once("error", (error) => console.error(error.message));
  }
}

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
  return url === startupUrl.href || Boolean(origin && new URL(url).origin === origin);
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
      ? { titleBarStyle: "hidden", trafficLightPosition }
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
  if (mac && Number.parseInt(process.getSystemVersion(), 10) >= 26) {
    const diameter = 14;
    const spacing = 23;
    let cursorPoll;
    let trafficLights = "native";
    const updateTrafficLights = () => {
      const inactive = window.isVisible() && !window.isMinimized()
        && !window.isFocused() && !window.isFullScreen();
      let appearance = "native";
      if (inactive) {
        const cursor = screen.getCursorScreenPoint();
        const bounds = window.getBounds();
        const x = cursor.x - bounds.x - trafficLightPosition.x;
        const y = cursor.y - bounds.y - trafficLightPosition.y;
        appearance = x >= 0 && x < spacing * 2 + diameter && y >= 0 && y < diameter
          ? "hover" : "idle";
      }
      if (trafficLights !== appearance) {
        if (trafficLights === "native" || appearance === "native") {
          window.setWindowButtonVisibility(appearance === "native");
          if (appearance === "native" && !window.isFullScreen()) {
            // AppKit can reset the custom position when the buttons become visible.
            window.setWindowButtonPosition(trafficLightPosition);
          }
        }
        trafficLights = appearance;
        window.webContents.send("desktop:inactive-traffic-lights", trafficLights);
      }
      if (inactive && !cursorPoll) {
        // A background window may not deliver mouse events to its web contents.
        cursorPoll = setInterval(updateTrafficLights, 60);
      } else if (!inactive && cursorPoll) {
        clearInterval(cursorPoll);
        cursorPoll = undefined;
      }
    };
    for (const event of ["focus", "blur", "show", "hide", "minimize", "restore", "enter-full-screen", "leave-full-screen"]) {
      window.on(event, updateTrafficLights);
    }
    window.once("closed", () => clearInterval(cursorPoll));
    window.webContents.on("dom-ready", () => {
      void window.webContents.insertCSS(`
        html[data-inactive-traffic-lights]::before {
          content: "";
          position: fixed;
          top: ${trafficLightPosition.y}px;
          left: ${trafficLightPosition.x}px;
          width: ${diameter}px;
          height: ${diameter}px;
          border-radius: 50%;
          color: #cfd3da;
          background: currentColor;
          box-shadow: ${spacing}px 0, ${spacing * 2}px 0;
          pointer-events: none;
          z-index: 101;
        }
        html[data-inactive-traffic-lights="hover"]::before {
          background: #ed6864;
          box-shadow: ${spacing}px 0 #f2ca48, ${spacing * 2}px 0 #65c566;
        }
      `).catch(console.error);
      updateTrafficLights();
      window.webContents.send("desktop:inactive-traffic-lights", trafficLights);
    });
  }
  window.webContents.on("did-finish-load", () => showStartupStage(startupStage, startupIndexHost));
  void window.loadURL(startupUrl.href);
}

function runPython(python, args, home, onReport, { sourceBootstrap = false, index = "" } = {}) {
  const script = ["-u", bootstrap, ...args];
  const uv = dev ? (process.env.UV || "uv") : join(runtime, windows ? "uv.exe" : "uv");
  const processHandle = spawn(sourceBootstrap ? uv : python, sourceBootstrap
    ? ["run", "--locked", "--project", root, "python", ...script]
    : script, {
    cwd: home,
    windowsHide: true,
    env: {
      ...process.env,
      PATH: [python && dirname(python), !dev && runtime, process.env.PATH].filter(Boolean).join(delimiter),
      UV: uv,
      SHIRITAI_DESKTOP_PARENT: "1",
      SHIRITAI_DESKTOP_INDEX: index,
      ...(sourceBootstrap && { UV_PROJECT_ENVIRONMENT: join(app.getPath("userData"), "bootstrap") }),
      ...(!dev && { UV_CACHE_DIR: join(home, "runtime", "cache"), UV_PYTHON_DOWNLOADS: "never" }),
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
    else if (dev) console.log(line);
  });
  processHandle.stderr.setEncoding("utf8").on("data", (chunk) => {
    diagnostics = (diagnostics + chunk).slice(-6000);
    if (dev) process.stderr.write(chunk);
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
  let bootstrapPython;
  if (!dev) {
    const distributions = await readdir(join(runtime, "python"), { withFileTypes: true });
    const distribution = distributions.find((item) => item.isDirectory() && item.name.startsWith("cpython-"));
    if (!distribution) throw new Error("The bundled Python runtime is missing.");
    bootstrapPython = join(runtime, "python", distribution.name, windows ? "python.exe" : "bin/python3.13");
  }
  let openingWindow;
  async function openApp(port) {
    if (dev) {
      const { startDevWeb } = await import(pathToFileURL(join(root, "web", "desktop-dev.mjs")).href);
      devWeb = await startDevWeb(port, () => restartBackend());
    }
    if (quitting) {
      await devWeb?.close();
      return;
    }
    origin = devWeb?.origin ?? `http://127.0.0.1:${port}`;
    if (dev) console.log(`ShiritaiBot dev UI: ${origin}`);
    const token = await readFile(join(home, "web.token"), "utf8");
    showStartupStage("ready");
    await delay(280);
    if (!quitting) await window.loadURL(`${origin}/token#${new URLSearchParams({ token: token.trim() })}`);
  }
  while (!quitting) {
    try {
      let python;
      let index;
      await runPython(bootstrapPython, ["prepare", home], home, (message) => {
        if (message.startupStage) {
          if (!openingWindow) showStartupStage(message.startupStage, message.indexHost);
        } else {
          python = message.python;
          index = message.index;
        }
      }, { sourceBootstrap: dev });
      if (quitting) return;
      if (!python) throw new Error("Python did not report a runtime environment.");
      if (!openingWindow) showStartupStage("starting");
      const code = await runPython(python, ["serve", home, String(port)], home, (message) => {
        if (message.taskFinished) {
          notifyTask(message.taskFinished);
          return;
        }
        port = message.port;
        if (dev) {
          const running = child;
          restartBackend = () => {
            if (!quitting && child === running) running.stdin.write("r");
          };
        }
        openingWindow ??= openApp(port).catch(showError);
      }, { index });
      if (code !== 75 && !quitting) throw new Error("The local server stopped unexpectedly.");
    } catch (error) {
      if (!devWeb || quitting) throw error;
      console.error(error.message + "\nSave a backend file to retry.");
      await new Promise((resolve) => { restartBackend = resolve; });
      restartBackend = () => {};
    }
  }
}

function showError(error) {
  if (quitting) return;
  if (dev) {
    console.error(error);
    exitCode = 1;
    app.quit();
    return;
  }
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
    preferences = await loadPreferences(userData, { useInstallerFolder: !dev });
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
        case "relaunch":
          if (dev) exitCode = 75;
          else app.relaunch();
          app.quit();
          break;
      }
    });
    createWindow();
    return runBackend();
  }).catch(showError);
  app.on("activate", () => { if (window) showWindow(); });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (stopped || (!dev && !child)) {
      quitting = true;
      return;
    }
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    restartBackend();
    const active = child;
    const shutdown = active ? new Promise((resolve) => {
      const timeout = setTimeout(() => active.kill(), 13000);
      active.once("close", () => {
        clearTimeout(timeout);
        resolve();
      });
      active.stdin.end();
    }) : Promise.resolve();
    void Promise.allSettled([shutdown, devWeb?.close()]).then(() => {
      stopped = true;
      if (dev) app.exit(exitCode);
      else app.quit();
    });
  });
}
