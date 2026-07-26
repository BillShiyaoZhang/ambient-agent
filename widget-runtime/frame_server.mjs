import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";


const CURRENT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_PORT = 8001;
const DEFAULT_HOST = "0.0.0.0";

export const CONTENT_SECURITY_POLICY = [
  "sandbox allow-scripts",
  "default-src 'none'",
  "script-src 'self' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "connect-src 'none'",
  "worker-src 'none'",
  "frame-src 'none'",
  "child-src 'none'",
  "object-src 'none'",
  "media-src 'none'",
  "font-src 'none'",
  "img-src 'none'",
  "manifest-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
].join("; ");

export const PERMISSIONS_POLICY = [
  "accelerometer=()",
  "autoplay=()",
  "bluetooth=()",
  "browsing-topics=()",
  "camera=()",
  "clipboard-read=()",
  "clipboard-write=()",
  "display-capture=()",
  "encrypted-media=()",
  "fullscreen=()",
  "gamepad=()",
  "geolocation=()",
  "gyroscope=()",
  "hid=()",
  "identity-credentials-get=()",
  "idle-detection=()",
  "local-fonts=()",
  "magnetometer=()",
  "microphone=()",
  "midi=()",
  "otp-credentials=()",
  "payment=()",
  "picture-in-picture=()",
  "publickey-credentials-create=()",
  "publickey-credentials-get=()",
  "screen-wake-lock=()",
  "serial=()",
  "storage-access=()",
  "usb=()",
  "web-share=()",
  "window-management=()",
  "xr-spatial-tracking=()",
].join(", ");

const SECURITY_HEADERS = Object.freeze({
  "access-control-allow-origin": "*",
  "cache-control": "no-store",
  "content-security-policy": CONTENT_SECURITY_POLICY,
  "permissions-policy": PERMISSIONS_POLICY,
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
});

const STATIC_FILES = Object.freeze({
  "/": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.html"),
    type: "text/html; charset=utf-8",
  },
  "/index.html": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.html"),
    type: "text/html; charset=utf-8",
  },
  "/frame.html": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.html"),
    type: "text/html; charset=utf-8",
  },
  "/frame_shell.css": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.css"),
    type: "text/css; charset=utf-8",
  },
  "/frame_shell.mjs": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.mjs"),
    type: "text/javascript; charset=utf-8",
  },
  "/controller_facade.mjs": {
    file: path.join(CURRENT_DIRECTORY, "controller_facade.mjs"),
    type: "text/javascript; charset=utf-8",
  },
  "/presentation_context.mjs": {
    file: path.join(CURRENT_DIRECTORY, "presentation_context.mjs"),
    type: "text/javascript; charset=utf-8",
  },
  "/vendor/babel.min.js": {
    file: path.join(
      CURRENT_DIRECTORY,
      "node_modules",
      "@babel",
      "standalone",
      "babel.min.js",
    ),
    type: "text/javascript; charset=utf-8",
  },
  "/vendor/htm-preact.js": {
    file: path.join(
      CURRENT_DIRECTORY,
      "node_modules",
      "htm",
      "preact",
      "standalone.umd.js",
    ),
    type: "text/javascript; charset=utf-8",
  },
});


function loadStaticFiles() {
  return new Map(
    Object.entries(STATIC_FILES).map(([urlPath, asset]) => [
      urlPath,
      {
        body: fs.readFileSync(asset.file),
        type: asset.type,
      },
    ]),
  );
}


function send(response, method, status, body, headers = {}) {
  const payload = Buffer.isBuffer(body) ? body : Buffer.from(body, "utf8");
  response.writeHead(status, {
    ...SECURITY_HEADERS,
    ...headers,
    "content-length": payload.byteLength,
  });
  response.end(method === "HEAD" ? undefined : payload);
}


export function createFrameServer() {
  const assets = loadStaticFiles();
  return http.createServer((request, response) => {
    const method = request.method ?? "GET";
    if (method !== "GET" && method !== "HEAD") {
      send(response, method, 405, "Method Not Allowed\n", {
        allow: "GET, HEAD",
        "content-type": "text/plain; charset=utf-8",
      });
      return;
    }

    let pathname;
    try {
      pathname = new URL(request.url ?? "/", "http://widget-frame.invalid")
        .pathname;
    } catch {
      send(response, method, 400, "Bad Request\n", {
        "content-type": "text/plain; charset=utf-8",
      });
      return;
    }

    if (pathname === "/health") {
      send(response, method, 200, "ok\n", {
        "content-type": "text/plain; charset=utf-8",
      });
      return;
    }

    const asset = assets.get(pathname);
    if (!asset) {
      send(response, method, 404, "Not Found\n", {
        "content-type": "text/plain; charset=utf-8",
      });
      return;
    }
    send(response, method, 200, asset.body, {
      "content-type": asset.type,
    });
  });
}


function configuredPort() {
  const port = Number.parseInt(process.env.WIDGET_FRAME_PORT ?? "", 10);
  return Number.isInteger(port) && port > 0 && port <= 65_535
    ? port
    : DEFAULT_PORT;
}


function isMainModule() {
  if (!process.argv[1]) return false;
  return path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
}


if (isMainModule()) {
  const server = createFrameServer();
  const host = process.env.WIDGET_FRAME_HOST || DEFAULT_HOST;
  const port = configuredPort();
  server.listen(port, host, () => {
    process.stdout.write(`Widget Frame listening on http://${host}:${port}\n`);
  });

  const shutdown = () => {
    server.close(() => process.exit(0));
  };
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}
