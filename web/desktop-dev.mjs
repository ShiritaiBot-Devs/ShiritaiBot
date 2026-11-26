import { resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const root = fileURLToPath(new URL(".", import.meta.url));

export async function startDevWeb(backendPort, restartBackend) {
  const server = await createServer({
    root,
    server: {
      host: "127.0.0.1",
      open: false,
      proxy: {
        "/api": { target: `http://127.0.0.1:${backendPort}`, ws: true },
      },
    },
  });

  let restartTimer;
  const source = resolve(root, "../src/shiritaibot");
  const backend = resolve(root, "../desktop/backend.py");
  server.watcher.add([source, backend]);
  server.watcher.on("all", (event, path) => {
    if (!["add", "change", "unlink"].includes(event) || !/\.(py|json|sql)$/.test(path)) return;
    const absolute = resolve(root, path);
    if (absolute !== backend && !absolute.startsWith(source + sep)) return;
    clearTimeout(restartTimer);
    restartTimer = setTimeout(restartBackend, 200);
  });

  await server.listen();
  const [origin] = server.resolvedUrls.local;
  return {
    origin: new URL(origin).origin,
    close: async () => {
      clearTimeout(restartTimer);
      await server.close();
    },
  };
}
