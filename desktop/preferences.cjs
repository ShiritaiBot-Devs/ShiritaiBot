const { execFile } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const { mkdir, readFile, realpath, rename, writeFile } = require("node:fs/promises");
const { dirname, isAbsolute, join, relative, sep } = require("node:path");
const { promisify } = require("node:util");

const exec = promisify(execFile);
const closeBehaviors = new Set(["ask", "quit", "tray"]);

async function installerFolder() {
  if (process.platform !== "win32") return "";
  try {
    const { stdout } = await exec("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
      String.raw`[Console]::OutputEncoding = [Text.Encoding]::UTF8; (Get-ItemProperty -LiteralPath 'HKCU:\Software\ShiritaiBot\Desktop' -Name DataDirectory -ErrorAction SilentlyContinue).DataDirectory`], { windowsHide: true });
    return stdout.trim();
  } catch {
    return "";
  }
}

async function loadPreferences(userData, { useInstallerFolder = true } = {}) {
  let saved = {};
  try {
    saved = JSON.parse(await readFile(join(userData, "desktop-preferences.json"), "utf8"));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const installed = useInstallerFolder && !saved.dataDirectory ? await installerFolder() : "";
  return {
    closeBehavior: closeBehaviors.has(saved.closeBehavior) ? saved.closeBehavior : "ask",
    notifications: saved.notifications === true,
    dataDirectory: saved.dataDirectory || installed || join(userData, "data"),
  };
}

async function writePreferences(userData, value) {
  const target = join(userData, "desktop-preferences.json");
  const temp = join(userData, `preferences-${randomUUID()}.tmp`);
  await writeFile(temp, JSON.stringify(value), { mode: 0o600 });
  await rename(temp, target);
}

async function dataFolder(path, executable) {
  if (typeof path !== "string" || !isAbsolute(path)) throw new Error("Invalid data directory.");
  await mkdir(path, { recursive: true });
  const resolved = await realpath(path);
  const program = await realpath(dirname(executable));
  const inside = relative(program, resolved);
  if (!inside || (inside !== ".." && !inside.startsWith(".." + sep) && !isAbsolute(inside)))
    throw new Error("Choose a data directory outside the application installation folder.");
  return resolved;
}

module.exports = { closeBehaviors, dataFolder, loadPreferences, writePreferences };
