import { createHash } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  brotliCompressSync,
  constants as zlibConstants,
  gzipSync,
} from "node:zlib";


const CURRENT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_PORT = 8001;
const DEFAULT_HOST = "0.0.0.0";
const IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable";
const REVALIDATED_CACHE_CONTROL = "public, max-age=0, must-revalidate";
const UNCACHEABLE_CACHE_CONTROL = "no-store";
const BROTLI_OPTIONS = Object.freeze({
  params: {
    [zlibConstants.BROTLI_PARAM_QUALITY]: 6,
  },
});

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
  "content-security-policy": CONTENT_SECURITY_POLICY,
  "permissions-policy": PERMISSIONS_POLICY,
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
});

const STATIC_FILES = Object.freeze({
  "/": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.html"),
    type: "text/html; charset=utf-8",
    references: {
      "/frame_shell.css": "/frame_shell.css",
      "/vendor/babel.min.js": "/vendor/babel.min.js",
      "/vendor/htm-preact.js": "/vendor/htm-preact.js",
      "/frame_shell.mjs": "/frame_shell.mjs",
    },
  },
  "/index.html": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.html"),
    type: "text/html; charset=utf-8",
    references: {
      "/frame_shell.css": "/frame_shell.css",
      "/vendor/babel.min.js": "/vendor/babel.min.js",
      "/vendor/htm-preact.js": "/vendor/htm-preact.js",
      "/frame_shell.mjs": "/frame_shell.mjs",
    },
  },
  "/frame.html": {
    file: path.join(CURRENT_DIRECTORY, "frame_shell.html"),
    type: "text/html; charset=utf-8",
    references: {
      "/frame_shell.css": "/frame_shell.css",
      "/vendor/babel.min.js": "/vendor/babel.min.js",
      "/vendor/htm-preact.js": "/vendor/htm-preact.js",
      "/frame_shell.mjs": "/frame_shell.mjs",
    },
  },
  "/frame_shell.css": {
    cacheable: true,
    file: path.join(CURRENT_DIRECTORY, "frame_shell.css"),
    type: "text/css; charset=utf-8",
  },
  "/frame_shell.mjs": {
    cacheable: true,
    file: path.join(CURRENT_DIRECTORY, "frame_shell.mjs"),
    references: {
      "./controller_facade.mjs": "/controller_facade.mjs",
      "./presentation_context.mjs": "/presentation_context.mjs",
    },
    type: "text/javascript; charset=utf-8",
  },
  "/controller_facade.mjs": {
    cacheable: true,
    file: path.join(CURRENT_DIRECTORY, "controller_facade.mjs"),
    references: {
      "./presentation_context.mjs": "/presentation_context.mjs",
    },
    type: "text/javascript; charset=utf-8",
  },
  "/presentation_context.mjs": {
    cacheable: true,
    file: path.join(CURRENT_DIRECTORY, "presentation_context.mjs"),
    type: "text/javascript; charset=utf-8",
  },
  "/vendor/babel.min.js": {
    cacheable: true,
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
    cacheable: true,
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
  const loaded = new Map();
  const loading = new Set();

  const load = (urlPath) => {
    const existing = loaded.get(urlPath);
    if (existing) return existing;
    if (loading.has(urlPath)) {
      throw new Error(`Circular frame asset reference: ${urlPath}`);
    }
    const definition = STATIC_FILES[urlPath];
    if (!definition) {
      throw new Error(`Unknown frame asset reference: ${urlPath}`);
    }

    loading.add(urlPath);
    let body = fs.readFileSync(definition.file);
    if (definition.references) {
      let source = body.toString("utf8");
      for (const [reference, targetPath] of Object.entries(
        definition.references,
      )) {
        const target = load(targetPath);
        const versionedReference = `${reference}?v=${target.digest}`;
        source = source
          .replaceAll(`"${reference}"`, `"${versionedReference}"`)
          .replaceAll(`'${reference}'`, `'${versionedReference}'`);
      }
      body = Buffer.from(source, "utf8");
    }

    const digest = createHash("sha256").update(body).digest("hex");
    const representations = {
      identity: {
        body,
        encoding: "identity",
        etag: `"${digest}"`,
      },
    };
    if (definition.cacheable === true) {
      const brotliBody = brotliCompressSync(body, BROTLI_OPTIONS);
      const gzipBody = gzipSync(body, { level: 9 });
      representations.br = {
        body: brotliBody,
        encoding: "br",
        etag: `"${createHash("sha256").update(brotliBody).digest("hex")}"`,
      };
      representations.gzip = {
        body: gzipBody,
        encoding: "gzip",
        etag: `"${createHash("sha256").update(gzipBody).digest("hex")}"`,
      };
    }
    const asset = {
      cacheable: definition.cacheable === true,
      digest,
      representations,
      type: definition.type,
    };
    loaded.set(urlPath, asset);
    loading.delete(urlPath);
    return asset;
  };

  for (const urlPath of Object.keys(STATIC_FILES)) {
    load(urlPath);
  }
  return loaded;
}


function send(response, method, status, body, headers = {}) {
  const payload = Buffer.isBuffer(body) ? body : Buffer.from(body, "utf8");
  response.writeHead(status, {
    ...SECURITY_HEADERS,
    "cache-control": UNCACHEABLE_CACHE_CONTROL,
    ...headers,
    "content-length": payload.byteLength,
  });
  response.end(method === "HEAD" ? undefined : payload);
}


function sendNotModified(response, headers) {
  response.writeHead(304, {
    ...SECURITY_HEADERS,
    ...headers,
  });
  response.end();
}


function matchesEntityTag(header, etag) {
  if (typeof header !== "string") return false;
  return header.split(",").some((candidate) => {
    const normalized = candidate.trim().replace(/^W\//, "");
    return normalized === "*" || normalized === etag;
  });
}


function acceptedEncoding(header) {
  if (typeof header !== "string" || header.trim() === "") return "identity";
  const qualities = new Map();
  for (const item of header.split(",")) {
    const [rawName, ...parameters] = item.trim().split(";");
    const name = rawName.trim().toLowerCase();
    if (!name) continue;
    let quality = 1;
    for (const parameter of parameters) {
      const match = parameter.trim().match(/^q=([0-9.]+)$/i);
      if (!match) continue;
      const parsed = Number.parseFloat(match[1]);
      quality = Number.isFinite(parsed) && parsed >= 0 && parsed <= 1
        ? parsed
        : 0;
    }
    qualities.set(name, Math.max(qualities.get(name) ?? 0, quality));
  }

  const wildcardQuality = qualities.get("*");
  const qualityFor = (encoding) => {
    if (qualities.has(encoding)) return qualities.get(encoding);
    if (wildcardQuality !== undefined) return wildcardQuality;
    return encoding === "identity" ? 1 : 0;
  };
  return ["br", "gzip", "identity"]
    .map((encoding, preference) => ({
      encoding,
      preference,
      quality: qualityFor(encoding),
    }))
    .filter(({ quality }) => quality > 0)
    .sort((left, right) => (
      right.quality - left.quality
      || left.preference - right.preference
    ))[0]?.encoding ?? null;
}


function cacheHeaders(asset, requestUrl, representation) {
  if (!asset.cacheable) return {};
  const queryEntries = [...requestUrl.searchParams.entries()];
  const hasMatchingVersion = (
    queryEntries.length === 1
    && queryEntries[0][0] === "v"
    && queryEntries[0][1] === asset.digest
  );
  return {
    "cache-control": hasMatchingVersion
      ? IMMUTABLE_CACHE_CONTROL
      : REVALIDATED_CACHE_CONTROL,
    etag: representation.etag,
    vary: "Accept-Encoding",
  };
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

    let requestUrl;
    try {
      requestUrl = new URL(
        request.url ?? "/",
        "http://widget-frame.invalid",
      );
    } catch {
      send(response, method, 400, "Bad Request\n", {
        "content-type": "text/plain; charset=utf-8",
      });
      return;
    }
    const { pathname } = requestUrl;

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
    const encoding = asset.cacheable
      ? acceptedEncoding(request.headers["accept-encoding"])
      : "identity";
    if (!encoding) {
      send(response, method, 406, "Not Acceptable\n", {
        "content-type": "text/plain; charset=utf-8",
        vary: "Accept-Encoding",
      });
      return;
    }
    const representation = asset.representations[encoding];
    const responseHeaders = {
      "content-type": asset.type,
      ...cacheHeaders(asset, requestUrl, representation),
    };
    if (representation.encoding !== "identity") {
      responseHeaders["content-encoding"] = representation.encoding;
    }
    if (
      asset.cacheable
      && matchesEntityTag(
        request.headers["if-none-match"],
        representation.etag,
      )
    ) {
      sendNotModified(response, responseHeaders);
      return;
    }
    send(response, method, 200, representation.body, {
      ...responseHeaders,
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
