import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import {
  CONTENT_SECURITY_POLICY,
  createFrameServer,
} from "../frame_server.mjs";


let baseUrl;
let server;


before(async () => {
  server = createFrameServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  baseUrl = `http://127.0.0.1:${address.port}`;
});


after(async () => {
  await new Promise((resolve) => server.close(resolve));
});


test("serves a fixed frame shell without reflecting app data", async () => {
  const response = await fetch(
    `${baseUrl}/frame.html?app_id=private-app&ticket=private-ticket`,
    { headers: { cookie: "ambient_secret=do-not-reflect" } },
  );

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "text/html; charset=utf-8");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("etag"), null);
  assert.equal(response.headers.get("set-cookie"), null);
  const body = await response.text();
  assert.match(body, /<div id="root"><\/div>/);
  assert.match(body, /src="\/vendor\/babel\.min\.js\?v=[a-f0-9]{64}"/);
  assert.match(body, /src="\/vendor\/htm-preact\.js\?v=[a-f0-9]{64}"/);
  assert.match(body, /src="\/frame_shell\.mjs\?v=[a-f0-9]{64}"/);
  assert.match(body, /href="\/frame_shell\.css\?v=[a-f0-9]{64}"/);
  assert.doesNotMatch(body, /private-app|private-ticket|do-not-reflect/);
});


test("uses immutable caching only for content-versioned allowlisted assets", async () => {
  const frameResponse = await fetch(`${baseUrl}/frame.html`);
  const frameBody = await frameResponse.text();
  const babelPath = frameBody.match(
    /src="(\/vendor\/babel\.min\.js\?v=[a-f0-9]{64})"/,
  )?.[1];
  assert.ok(babelPath);

  const [versionedResponse, compatibilityResponse, mismatchedResponse] =
    await Promise.all([
      fetch(`${baseUrl}${babelPath}`),
      fetch(`${baseUrl}/vendor/babel.min.js`),
      fetch(`${baseUrl}/vendor/babel.min.js?v=stale-release`),
    ]);

  assert.equal(versionedResponse.status, 200);
  assert.equal(
    versionedResponse.headers.get("cache-control"),
    "public, max-age=31536000, immutable",
  );
  assert.match(versionedResponse.headers.get("etag"), /^"[a-f0-9]{64}"$/);
  assert.ok((await versionedResponse.arrayBuffer()).byteLength > 1_000_000);

  for (const response of [compatibilityResponse, mismatchedResponse]) {
    assert.equal(response.status, 200);
    assert.equal(
      response.headers.get("cache-control"),
      "public, max-age=0, must-revalidate",
    );
    assert.equal(
      response.headers.get("etag"),
      versionedResponse.headers.get("etag"),
    );
  }
});


test("precompresses fixed assets and negotiates representation-specific ETags", async () => {
  const url = `${baseUrl}/vendor/babel.min.js`;
  const [identity, brotli, gzip, head] = await Promise.all([
    fetch(url, { headers: { "accept-encoding": "identity" } }),
    fetch(url, { headers: { "accept-encoding": "br" } }),
    fetch(url, { headers: { "accept-encoding": "gzip" } }),
    fetch(url, {
      method: "HEAD",
      headers: { "accept-encoding": "br" },
    }),
  ]);

  assert.equal(identity.headers.get("content-encoding"), null);
  assert.equal(brotli.headers.get("content-encoding"), "br");
  assert.equal(gzip.headers.get("content-encoding"), "gzip");
  for (const response of [identity, brotli, gzip, head]) {
    assert.equal(response.headers.get("vary"), "Accept-Encoding");
    assert.match(response.headers.get("etag"), /^"[a-f0-9]{64}"$/);
    assert.equal(
      response.headers.get("cache-control"),
      "public, max-age=0, must-revalidate",
    );
  }

  const identityBytes = Number(identity.headers.get("content-length"));
  const brotliBytes = Number(brotli.headers.get("content-length"));
  const gzipBytes = Number(gzip.headers.get("content-length"));
  assert.ok(identityBytes > 2_000_000, identityBytes);
  assert.ok(brotliBytes < 500_000, brotliBytes);
  assert.ok(gzipBytes < 600_000, gzipBytes);
  assert.ok(brotliBytes < gzipBytes);
  assert.equal(
    Number(head.headers.get("content-length")),
    brotliBytes,
  );
  assert.equal((await head.arrayBuffer()).byteLength, 0);

  const [identityBody, brotliBody, gzipBody] = await Promise.all([
    identity.arrayBuffer(),
    brotli.arrayBuffer(),
    gzip.arrayBuffer(),
  ]);
  assert.deepEqual(brotliBody, identityBody);
  assert.deepEqual(gzipBody, identityBody);
  assert.notEqual(brotli.headers.get("etag"), identity.headers.get("etag"));
  assert.notEqual(gzip.headers.get("etag"), identity.headers.get("etag"));
  assert.notEqual(brotli.headers.get("etag"), gzip.headers.get("etag"));
});


test("returns a bodyless 304 with cache and security headers for matching ETags", async () => {
  const initial = await fetch(`${baseUrl}/vendor/htm-preact.js`, {
    headers: { "accept-encoding": "br" },
  });
  const etag = initial.headers.get("etag");
  assert.match(etag, /^"[a-f0-9]{64}"$/);
  assert.equal(initial.headers.get("content-encoding"), "br");
  await initial.arrayBuffer();

  const response = await fetch(`${baseUrl}/vendor/htm-preact.js`, {
    headers: {
      "accept-encoding": "br",
      "if-none-match": `"unrelated", W/${etag}`,
    },
  });

  assert.equal(response.status, 304);
  assert.equal((await response.arrayBuffer()).byteLength, 0);
  assert.equal(response.headers.get("etag"), etag);
  assert.equal(response.headers.get("content-encoding"), "br");
  assert.equal(response.headers.get("vary"), "Accept-Encoding");
  assert.equal(
    response.headers.get("cache-control"),
    "public, max-age=0, must-revalidate",
  );
  assert.equal(
    response.headers.get("content-security-policy"),
    CONTENT_SECURITY_POLICY,
  );
  assert.match(response.headers.get("permissions-policy"), /camera=\(\)/);
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("access-control-allow-origin"), "*");
  assert.equal(response.headers.get("set-cookie"), null);
});


test("sets restrictive browser security headers on every response", async () => {
  const response = await fetch(`${baseUrl}/missing`);

  assert.equal(response.status, 404);
  assert.equal(
    response.headers.get("content-security-policy"),
    CONTENT_SECURITY_POLICY,
  );
  for (const directive of [
    "sandbox allow-scripts",
    "default-src 'none'",
    "style-src 'self' 'unsafe-inline'",
    "connect-src 'none'",
    "worker-src 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "media-src 'none'",
    "font-src 'none'",
    "form-action 'none'",
    "base-uri 'none'",
  ]) {
    assert.ok(CONTENT_SECURITY_POLICY.includes(directive), directive);
  }
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("access-control-allow-origin"), "*");
  assert.equal(response.headers.get("access-control-allow-credentials"), null);
  assert.match(response.headers.get("permissions-policy"), /camera=\(\)/);
  assert.match(response.headers.get("permissions-policy"), /microphone=\(\)/);
  assert.match(response.headers.get("permissions-policy"), /geolocation=\(\)/);
  assert.equal(response.headers.get("set-cookie"), null);
});


test("serves only allowlisted static assets and supports health checks", async () => {
  const [moduleResponse, vendorResponse, healthResponse, methodResponse] =
    await Promise.all([
      fetch(`${baseUrl}/frame_shell.mjs`),
      fetch(`${baseUrl}/vendor/htm-preact.js`),
      fetch(`${baseUrl}/health`),
      fetch(`${baseUrl}/`, { method: "POST" }),
    ]);

  assert.equal(moduleResponse.status, 200);
  assert.equal(
    moduleResponse.headers.get("content-type"),
    "text/javascript; charset=utf-8",
  );
  assert.match(await moduleResponse.text(), /installFrameShell/);
  assert.equal(vendorResponse.status, 200);
  assert.equal(
    vendorResponse.headers.get("content-type"),
    "text/javascript; charset=utf-8",
  );
  assert.equal(healthResponse.status, 200);
  assert.equal(await healthResponse.text(), "ok\n");
  assert.equal(methodResponse.status, 405);
  assert.equal(methodResponse.headers.get("allow"), "GET, HEAD");
});
