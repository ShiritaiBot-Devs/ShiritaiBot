const { spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const { createServer } = require("node:net");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const endpoint = process.platform === "win32"
  ? `\\\\.\\pipe\\shiritaibot-${randomUUID()}`
  : `/tmp/shiritaibot-${randomUUID()}.sock`;
env.SHIRITAI_DESKTOP_DEV_SOCKET = endpoint;
let stopping = false;
let connection;
const server = createServer((socket) => {
  connection = socket;
  socket.on("error", () => {});
  if (stopping) socket.end();
});

function launch() {
  const child = spawn(require("electron"), [".", "--desktop-dev", ...process.argv.slice(2)], {
    cwd: __dirname,
    env,
    stdio: "inherit",
  });
  const stop = () => {
    stopping = true;
    connection?.end();
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  child.on("error", (error) => console.error(error.message));
  child.on("close", (code) => {
    process.off("SIGINT", stop);
    process.off("SIGTERM", stop);
    if (code === 75 && !stopping) launch();
    else {
      server.close();
      process.exitCode = stopping ? 0 : (code ?? 1);
    }
  });
}

server.listen(endpoint, launch);
