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
  assert.equal(response.headers.get("set-cookie"), null);
  const body = await response.text();
  assert.match(body, /<div id="root"><\/div>/);
  assert.match(body, /src="\/vendor\/babel\.min\.js"/);
  assert.match(body, /src="\/vendor\/htm-preact\.js"/);
  assert.match(body, /src="\/frame_shell\.mjs"/);
  assert.doesNotMatch(body, /private-app|private-ticket|do-not-reflect/);
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
