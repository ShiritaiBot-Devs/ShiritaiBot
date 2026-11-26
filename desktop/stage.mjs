import { execFileSync } from "node:child_process";
import { copyFileSync, cpSync, mkdirSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = dirname(fileURLToPath(import.meta.url));
const root = dirname(desktop);
const output = join(desktop, "build");
const runtime = join(output, "runtime");
const core = join(runtime, "core");
const windows = process.platform === "win32";
const uvName = windows ? "uv.exe" : "uv";

function run(program, args, options = {}) {
  return execFileSync(program, args, { cwd: root, stdio: "inherit", ...options });
}

const version = /^__version__ = "([^"]+)"/m.exec(
  readFileSync(join(root, "src/shiritaibot/__init__.py"), "utf8"),
)[1];
if (version !== JSON.parse(readFileSync(join(desktop, "package.json"), "utf8")).version) {
  throw new Error("Sync desktop/package.json with the ShiritaiBot version before packaging.");
}

rmSync(output, { recursive: true, force: true });
mkdirSync(core, { recursive: true });
const tracked = run("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "src", "pyproject.toml", "uv.lock",
  ".python-version", "LICENSE", "README.md"], { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
for (const name of tracked.split("\0").filter(Boolean)) {
  const target = join(core, name);
  mkdirSync(dirname(target), { recursive: true });
  copyFileSync(join(root, name), target);
}
cpSync(join(root, "web/dist"), join(core, "web/dist"), { recursive: true });
copyFileSync(join(desktop, "backend.py"), join(runtime, "backend.py"));

const paths = run(windows ? "where.exe" : "/usr/bin/which", [uvName],
  { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
copyFileSync(paths.trim().split(/\r?\n/)[0], join(runtime, uvName));

const pythonInstall = join(runtime, "python");
run(join(runtime, uvName), ["python", "install", "3.13.12", "--install-dir", pythonInstall, "--no-bin"]);
const distribution = readdirSync(pythonInstall, { withFileTypes: true })
  .find((item) => item.isDirectory() && item.name.startsWith("cpython-"));
if (!distribution) throw new Error("uv did not install a CPython distribution.");
const python = join(pythonInstall, distribution.name, windows ? "python.exe" : "bin/python3.13");
const constraints = join(output, "constraints.txt");
run(join(runtime, uvName), ["export", "--locked", "--no-dev", "--no-emit-project",
  "--no-hashes", "--output-file", constraints], { stdio: ["ignore", "ignore", "inherit"] });
run(join(runtime, uvName), ["pip", "install", "--python", python, "--target",
  join(runtime, "bootstrap"), "--constraints", relative(root, constraints), "--link-mode", "copy",
  "packaging", "psutil", "pydantic", "tomli-w"]);
run(python, [join(runtime, "backend.py"), "check"], {
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
});
run(join(runtime, uvName), ["run", "--no-project", "--python", python, "--with", "pillow==12.1.1",
  join(desktop, "icons.py"), join(root, "web/public/icon.png"), join(output, "icons")]);
