import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  BrowserLifecycle,
  DEFAULT_BROWSER_IDLE_TIMEOUT_MS,
  DEFAULT_MAX_CONTEXTS,
  createControllerTransformer,
} from "../runtime.mjs";


const CURRENT_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const RUNTIME_PATH = path.resolve(CURRENT_DIRECTORY, "..", "runtime.mjs");
const CHROMIUM_CANDIDATES = [
  process.env.CHROMIUM_EXECUTABLE_PATH,
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const CHROMIUM_PATH = CHROMIUM_CANDIDATES.find((candidate) =>
  fs.existsSync(candidate),
);


class FakeBrowser extends EventEmitter {
  constructor() {
    super();
    this.closeCalls = 0;
    this.connected = true;
  }

  isConnected() {
    return this.connected;
  }

  async close() {
    this.closeCalls += 1;
    if (!this.connected) return;
    this.connected = false;
    this.emit("disconnected");
  }

  crash() {
    if (!this.connected) return;
    this.connected = false;
    this.emit("disconnected");
  }
}


class DeferredCloseBrowser extends FakeBrowser {
  constructor() {
    super();
    this.resolveClose = null;
  }

  async close() {
    this.closeCalls += 1;
    if (!this.connected) return;
    await new Promise((resolve) => {
      this.resolveClose = resolve;
    });
    this.connected = false;
    this.emit("disconnected");
  }

  finishClose() {
    this.resolveClose?.();
  }
}


class FailingCloseBrowser extends FakeBrowser {
  constructor({ disconnectBeforeFailure = false } = {}) {
    super();
    this.disconnectBeforeFailure = disconnectBeforeFailure;
  }

  async close() {
    this.closeCalls += 1;
    if (this.disconnectBeforeFailure) {
      this.connected = false;
      this.emit("disconnected");
    }
    throw new Error("close failed");
  }
}


class FakeTimers {
  constructor() {
    this.entries = [];
  }

  set = (callback, delay) => {
    const entry = { callback, delay, cleared: false };
    this.entries.push(entry);
    return entry;
  };

  clear = (entry) => {
    if (entry) entry.cleared = true;
  };

  pending() {
    return this.entries.filter((entry) => !entry.cleared);
  }

  async runNext() {
    const entry = this.pending()[0];
    assert.ok(entry, "expected a pending timer");
    entry.cleared = true;
    await entry.callback();
  }
}


function waitForChildExit(child) {
  if (child.exitCode !== null) return Promise.resolve(child.exitCode);
  return new Promise((resolve) => child.once("exit", resolve));
}


async function waitForSocket(socketPath, child, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(socketPath)) return;
    if (child.exitCode !== null) {
      throw new Error(`runtime exited before listening (code ${child.exitCode})`);
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("timed out waiting for the runtime socket");
}


function connectUnix(socketPath) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    socket.once("connect", () => resolve(socket));
    socket.once("error", reject);
  });
}


function waitForFirstFrame(socket, sessionId, timeoutMs = 10_000) {
  return new Promise((resolve, reject) => {
    let buffer = Buffer.alloc(0);
    let ready = false;
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`timed out waiting for frame for ${sessionId}`));
    }, timeoutMs);
    const cleanup = () => {
      clearTimeout(timer);
      socket.off("data", onData);
      socket.off("error", onError);
    };
    const onError = (error) => {
      cleanup();
      reject(error);
    };
    const onData = (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      let newline;
      while ((newline = buffer.indexOf(0x0a)) >= 0) {
        const line = buffer.subarray(0, newline);
        buffer = buffer.subarray(newline + 1);
        if (!line.byteLength) continue;
        const message = JSON.parse(line.toString("utf8"));
        if (message.type === "runtime_error") {
          cleanup();
          reject(new Error(JSON.stringify(message.error)));
          return;
        }
        if (message.type === "ready" && message.session_id === sessionId) {
          ready = true;
        }
        if (message.type === "frame" && message.session_id === sessionId) {
          cleanup();
          resolve({ frame: message, ready });
          return;
        }
      }
    };
    socket.on("data", onData);
    socket.once("error", onError);
  });
}


function runtimeStartMessage(sessionId) {
  return {
    type: "start",
    protocol_version: 1,
    session_id: sessionId,
    app_id: `app-${sessionId}`,
    manifest_revision: "2:1.0.0",
    grants_digest: `grants-${sessionId}`,
    artifact_digest: `artifact-${sessionId}`,
    capability_ids: [],
    controller_source:
      `export default function Controller() {`
      + ` return <div>Runtime smoke ${sessionId}</div>;`
      + " }",
    viewport: {
      width: 320,
      height: 240,
      device_scale_factor: 1,
    },
  };
}


async function openRuntimeSession(socketPath, sessionId) {
  const socket = await connectUnix(socketPath);
  const framePromise = waitForFirstFrame(socket, sessionId);
  socket.write(`${JSON.stringify(runtimeStartMessage(sessionId))}\n`);
  const result = await framePromise;
  return { result, socket };
}


async function closeRuntimeSession(socket, sessionId) {
  await new Promise((resolve, reject) => {
    socket.write(
      `${JSON.stringify({ type: "close", session_id: sessionId })}\n`,
      (error) => {
        if (error) {
          reject(error);
          return;
        }
        socket.end(resolve);
      },
    );
  });
}


async function renderRuntimeSession(socketPath, sessionId) {
  const { result, socket } = await openRuntimeSession(socketPath, sessionId);
  await closeRuntimeSession(socket, sessionId);
  return result;
}


test("uses conservative defaults for an idle runtime under the PID budget", () => {
  assert.equal(DEFAULT_MAX_CONTEXTS, 4);
  assert.equal(DEFAULT_BROWSER_IDLE_TIMEOUT_MS, 60_000);
});


test("coalesces concurrent first opens and intentionally closes an idle browser", async () => {
  const browsers = [];
  const timers = new FakeTimers();
  let launchCalls = 0;
  let unexpectedDisconnects = 0;
  const lifecycle = new BrowserLifecycle({
    idleTimeoutMs: 1234,
    launchBrowser: async () => {
      launchCalls += 1;
      await Promise.resolve();
      const browser = new FakeBrowser();
      browsers.push(browser);
      return browser;
    },
    onFatal: () => {
      unexpectedDisconnects += 1;
    },
    setTimer: timers.set,
    clearTimer: timers.clear,
  });

  assert.equal(launchCalls, 0);
  const [first, second] = await Promise.all([
    lifecycle.acquire(),
    lifecycle.acquire(),
  ]);
  assert.equal(launchCalls, 1);
  assert.equal(first.browser, second.browser);

  first.release();
  assert.equal(timers.pending().length, 0);
  second.release();
  assert.equal(timers.pending().length, 1);
  assert.equal(timers.pending()[0].delay, 1234);

  await timers.runNext();
  assert.equal(browsers[0].closeCalls, 1);
  assert.equal(unexpectedDisconnects, 0);

  const third = await lifecycle.acquire();
  assert.equal(launchCalls, 2);
  assert.notEqual(third.browser, browsers[0]);
  third.release();
  await lifecycle.shutdown();
});


test("cancels idle reclamation when a session reopens", async () => {
  const timers = new FakeTimers();
  const browser = new FakeBrowser();
  const lifecycle = new BrowserLifecycle({
    idleTimeoutMs: 100,
    launchBrowser: async () => browser,
    setTimer: timers.set,
    clearTimer: timers.clear,
  });

  const first = await lifecycle.acquire();
  first.release();
  const scheduledClose = timers.pending()[0];
  assert.ok(scheduledClose);

  const second = await lifecycle.acquire();
  assert.equal(scheduledClose.cleared, true);
  assert.equal(browser.closeCalls, 0);
  second.release();
  await lifecycle.shutdown();
});


test("waits for an in-progress idle close before launching a replacement", async () => {
  const timers = new FakeTimers();
  const closingBrowser = new DeferredCloseBrowser();
  const replacementBrowser = new FakeBrowser();
  let launchCalls = 0;
  const lifecycle = new BrowserLifecycle({
    idleTimeoutMs: 100,
    launchBrowser: async () => {
      launchCalls += 1;
      return launchCalls === 1 ? closingBrowser : replacementBrowser;
    },
    setTimer: timers.set,
    clearTimer: timers.clear,
  });

  const first = await lifecycle.acquire();
  first.release();
  const idleClose = timers.runNext();
  assert.equal(closingBrowser.closeCalls, 1);

  let acquireSettled = false;
  const acquiring = lifecycle.acquire().then((lease) => {
    acquireSettled = true;
    return lease;
  });
  let second;
  try {
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(
      launchCalls,
      1,
      "a replacement must not launch while the prior browser is closing",
    );
    assert.equal(
      acquireSettled,
      false,
      "the new lease must wait until the prior browser has closed",
    );

    closingBrowser.finishClose();
    await idleClose;
    second = await acquiring;
    assert.equal(launchCalls, 2);
    assert.equal(second.browser, replacementBrowser);
  } finally {
    closingBrowser.finishClose();
    await idleClose;
    second ??= await acquiring;
    second.release();
    await lifecycle.shutdown();
  }
});


test(
  "enters fatal state when close fails while Chromium remains connected",
  async () => {
    const timers = new FakeTimers();
    const survivingBrowser = new FailingCloseBrowser();
    const replacementBrowser = new FakeBrowser();
    const fatalErrors = [];
    let launchCalls = 0;
    const lifecycle = new BrowserLifecycle({
      idleTimeoutMs: 100,
      launchBrowser: async () => {
        launchCalls += 1;
        return launchCalls === 1 ? survivingBrowser : replacementBrowser;
      },
      onFatal: (error) => fatalErrors.push(error),
      setTimer: timers.set,
      clearTimer: timers.clear,
    });

    const lease = await lifecycle.acquire();
    lease.release();
    await timers.runNext();
    await Promise.resolve();

    assert.equal(survivingBrowser.isConnected(), true);
    assert.equal(fatalErrors.length, 1);
    assert.equal(fatalErrors[0].code, "chromium_close_failed");
    await assert.rejects(lifecycle.acquire(), /restart is required/);
    assert.equal(
      launchCalls,
      1,
      "fatal close failure must not launch a second Chromium",
    );

    await lifecycle.shutdown();
  },
);


test(
  "allows relaunch when close fails after Chromium is confirmed disconnected",
  async () => {
    const timers = new FakeTimers();
    const disconnectedBrowser = new FailingCloseBrowser({
      disconnectBeforeFailure: true,
    });
    const replacementBrowser = new FakeBrowser();
    let launchCalls = 0;
    const lifecycle = new BrowserLifecycle({
      idleTimeoutMs: 100,
      launchBrowser: async () => {
        launchCalls += 1;
        return launchCalls === 1 ? disconnectedBrowser : replacementBrowser;
      },
      setTimer: timers.set,
      clearTimer: timers.clear,
    });

    const first = await lifecycle.acquire();
    first.release();
    await timers.runNext();

    const second = await lifecycle.acquire();
    assert.equal(launchCalls, 2);
    assert.equal(second.browser, replacementBrowser);
    second.release();
    await lifecycle.shutdown();
  },
);


test("reserves a lease before a queued idle callback can close the browser", async () => {
  const timers = new FakeTimers();
  const browser = new FakeBrowser();
  const lifecycle = new BrowserLifecycle({
    idleTimeoutMs: 100,
    launchBrowser: async () => browser,
    setTimer: timers.set,
    clearTimer: timers.clear,
  });

  const first = await lifecycle.acquire();
  first.release();
  const queuedIdleCallback = timers.pending()[0];
  assert.ok(queuedIdleCallback);

  const ensureBrowser = lifecycle.ensureBrowser.bind(lifecycle);
  lifecycle.ensureBrowser = async () => {
    const value = await ensureBrowser();
    queueMicrotask(() => {
      void queuedIdleCallback.callback();
    });
    return value;
  };

  const second = await lifecycle.acquire();
  assert.equal(second.browser, browser);
  assert.equal(browser.isConnected(), true);
  assert.equal(browser.closeCalls, 0);
  second.release();
  await lifecycle.shutdown();
});


test("rolls back a reserved lease when browser launch fails and allows retry", async () => {
  const browser = new FakeBrowser();
  let launchCalls = 0;
  const lifecycle = new BrowserLifecycle({
    launchBrowser: async () => {
      launchCalls += 1;
      if (launchCalls === 1) throw new Error("launch failed");
      return browser;
    },
  });

  await assert.rejects(lifecycle.acquire(), /launch failed/);
  assert.equal(lifecycle.activeLeases, 0);

  const lease = await lifecycle.acquire();
  assert.equal(launchCalls, 2);
  assert.equal(lease.browser, browser);
  lease.release();
  await lifecycle.shutdown();
});


test("rolls back a launch reservation when shutdown wins the race", async () => {
  let finishLaunch;
  const browser = new FakeBrowser();
  const lifecycle = new BrowserLifecycle({
    launchBrowser: () => new Promise((resolve) => {
      finishLaunch = () => resolve(browser);
    }),
  });

  const acquiring = lifecycle.acquire();
  const stopping = lifecycle.shutdown();
  finishLaunch();

  await assert.rejects(acquiring, /shutting down/);
  await stopping;
  assert.equal(lifecycle.activeLeases, 0);
  assert.equal(browser.closeCalls, 1);
});


test("an unplanned Chromium disconnect immediately blocks relaunch", async () => {
  const browser = new FakeBrowser();
  const replacementBrowser = new FakeBrowser();
  const fatalErrors = [];
  let launchCalls = 0;
  const lifecycle = new BrowserLifecycle({
    idleTimeoutMs: 100,
    launchBrowser: async () => {
      launchCalls += 1;
      return launchCalls === 1 ? browser : replacementBrowser;
    },
    onFatal: (error) => fatalErrors.push(error),
  });

  const lease = await lifecycle.acquire();
  browser.crash();
  await assert.rejects(lifecycle.acquire(), /restart is required/);
  await Promise.resolve();
  assert.equal(launchCalls, 1);
  assert.equal(fatalErrors.length, 1);
  assert.equal(fatalErrors[0].code, "chromium_disconnected");
  lease.release();
  await lifecycle.shutdown();
});


test("loads Babel only for the first Controller and shares that load", async () => {
  let loadCalls = 0;
  const transforms = [];
  const transformController = createControllerTransformer({
    loadBabel: async () => {
      loadCalls += 1;
      await Promise.resolve();
      return {
        transform(source, options) {
          transforms.push({ source, options });
          return { code: `compiled:${source}` };
        },
      };
    },
  });

  assert.equal(loadCalls, 0);
  const [first, second] = await Promise.all([
    transformController("first"),
    transformController("second"),
  ]);
  assert.equal(loadCalls, 1);
  assert.equal(first, "compiled:first");
  assert.equal(second, "compiled:second");
  assert.equal(transforms.length, 2);
  assert.equal(transforms[0].options.filename, "controller.js");

  const source = fs.readFileSync(RUNTIME_PATH, "utf8");
  assert.doesNotMatch(source, /from\s+["']@babel\/standalone["']/);
  assert.match(source, /import\(["']@babel\/standalone["']\)/);
});


test("listens on the socket without launching Chromium", async (context) => {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "ambient-widget-runtime-test-"),
  );
  const socketPath = path.join(directory, "runtime.sock");
  const child = spawn(process.execPath, [RUNTIME_PATH], {
    env: {
      ...process.env,
      CHROMIUM_EXECUTABLE_PATH: path.join(directory, "missing-chromium"),
      WIDGET_RUNTIME_SOCKET_PATH: socketPath,
      WIDGET_RUNTIME_IDLE_TIMEOUT_MS: "10",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr.on("data", (chunk) => {
    output += chunk;
  });
  context.after(async () => {
    if (child.exitCode === null) {
      child.kill("SIGTERM");
      await waitForChildExit(child);
    }
    fs.rmSync(directory, { recursive: true, force: true });
  });

  await waitForSocket(socketPath, child);
  const socket = net.createConnection(socketPath);
  await new Promise((resolve, reject) => {
    socket.once("connect", resolve);
    socket.once("error", reject);
  });
  socket.destroy();
  assert.match(output, /Widget Runtime listening/);
  assert.equal(child.exitCode, null);
});


test(
  "renders again after the last session passes the browser idle timeout",
  {
    skip: CHROMIUM_PATH ? false : "Chromium is not installed",
    timeout: 30_000,
  },
  async (context) => {
    const directory = fs.mkdtempSync(
      path.join(os.tmpdir(), "ambient-widget-runtime-smoke-"),
    );
    const socketPath = path.join(directory, "runtime.sock");
    const child = spawn(process.execPath, [RUNTIME_PATH], {
      env: {
        ...process.env,
        CHROMIUM_EXECUTABLE_PATH: CHROMIUM_PATH,
        WIDGET_RUNTIME_SOCKET_PATH: socketPath,
        WIDGET_RUNTIME_IDLE_TIMEOUT_MS: "100",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => {
      output += chunk;
    });
    child.stderr.on("data", (chunk) => {
      output += chunk;
    });
    context.after(async () => {
      if (child.exitCode === null) {
        child.kill("SIGTERM");
        await waitForChildExit(child);
      }
      fs.rmSync(directory, { recursive: true, force: true });
    });

    await waitForSocket(socketPath, child);
    const first = await renderRuntimeSession(socketPath, "first");
    assert.equal(first.ready, true);
    assert.equal(first.frame.format, "jpeg");
    assert.ok(first.frame.data.length > 100);

    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.equal(
      child.exitCode,
      null,
      `runtime exited after intentional idle close:\n${output}`,
    );

    const second = await renderRuntimeSession(socketPath, "second");
    assert.equal(second.ready, true);
    assert.equal(second.frame.format, "jpeg");
    assert.ok(second.frame.data.length > 100);

    await new Promise((resolve) => setTimeout(resolve, 300));
    const active = await Promise.all(
      ["multi-1", "multi-2", "multi-3", "multi-4"].map(async (sessionId) => ({
        sessionId,
        ...await openRuntimeSession(socketPath, sessionId),
      })),
    );
    assert.ok(active.every(({ result }) => result.ready));

    const fifthSocket = await connectUnix(socketPath);
    const fifthFrame = waitForFirstFrame(fifthSocket, "multi-5");
    fifthSocket.write(
      `${JSON.stringify(runtimeStartMessage("multi-5"))}\n`,
    );
    await assert.rejects(fifthFrame, /context limit reached/);
    fifthSocket.end();

    await Promise.all(
      active.map(({ sessionId, socket }) =>
        closeRuntimeSession(socket, sessionId)
      ),
    );
    assert.equal(child.exitCode, null);
  },
);
