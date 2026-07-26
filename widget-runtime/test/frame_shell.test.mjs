import assert from "node:assert/strict";
import { MessageChannel } from "node:worker_threads";
import { test } from "node:test";

import {
  FRAME_PROTOCOL_VERSION,
  installFrameShell,
} from "../frame_shell.mjs";


class FakeWindow {
  constructor() {
    this.parent = {};
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type, event) {
    for (const listener of [...(this.listeners.get(type) ?? [])]) {
      listener(event);
    }
  }

  listenerCount(type) {
    return this.listeners.get(type)?.size ?? 0;
  }
}


function nextMessage(port) {
  return new Promise((resolve) => port.once("message", resolve));
}


function portOffer(target, port, overrides = {}) {
  return {
    source: target.parent,
    data: {
      type: "ambient-widget-port",
      protocol_version: FRAME_PROTOCOL_VERSION,
      nonce: "nonce-123",
      ...overrides,
    },
    ports: [port],
  };
}


function initMessage(overrides = {}) {
  return {
    type: "init",
    protocol_version: FRAME_PROTOCOL_VERSION,
    nonce: "nonce-123",
    controller_source: "export default function Controller() { return null; }",
    capability_ids: ["graph.query"],
    presentation_context: {
      theme: { preference: "system", effective: "dark" },
      locale: "en-US",
      reduced_motion: false,
    },
    ...overrides,
  };
}


test("accepts one parent port, echoes the nonce, then removes global message access", async () => {
  const target = new FakeWindow();
  const initialized = [];
  const shell = installFrameShell(target, {
    initializeController: async (message) => {
      initialized.push(message);
      return { dispose() {} };
    },
  });
  const ignored = new MessageChannel();
  target.dispatch("message", {
    ...portOffer(target, ignored.port1),
    source: {},
  });
  assert.equal(target.listenerCount("message"), 1);

  const channel = new MessageChannel();
  const offered = nextMessage(channel.port2);
  target.dispatch("message", portOffer(target, channel.port1));
  assert.deepEqual(await offered, {
    type: "port_ready",
    nonce: "nonce-123",
  });
  assert.equal(target.listenerCount("message"), 0);

  const ready = nextMessage(channel.port2);
  channel.port2.postMessage(initMessage());
  assert.deepEqual(await ready, { type: "ready" });
  assert.equal(initialized.length, 1);
  assert.equal(initialized[0].controller_source, initMessage().controller_source);

  const secondChannel = new MessageChannel();
  target.dispatch("message", portOffer(target, secondChannel.port1));
  assert.equal(target.listenerCount("message"), 0);

  shell.dispose();
  ignored.port1.close();
  ignored.port2.close();
  channel.port2.close();
  secondChannel.port1.close();
  secondChannel.port2.close();
});


test("routes storage requests and responses exclusively over the transferred port", async () => {
  const target = new FakeWindow();
  let transport;
  const shell = installFrameShell(target, {
    initializeController: async (_message, nextTransport) => {
      transport = nextTransport;
      return { dispose() {} };
    },
  });
  const channel = new MessageChannel();
  const offered = nextMessage(channel.port2);
  target.dispatch("message", portOffer(target, channel.port1));
  await offered;
  const ready = nextMessage(channel.port2);
  channel.port2.postMessage(initMessage());
  await ready;

  const requestMessage = nextMessage(channel.port2);
  const resultPromise = transport.storageRequest("get", { key: "draft" });
  const request = await requestMessage;
  assert.deepEqual(request, {
    type: "storage_request",
    request_id: "storage-1",
    operation: "get",
    key: "draft",
  });
  channel.port2.postMessage({
    type: "storage_response",
    request_id: request.request_id,
    result: { text: "saved locally" },
  });
  assert.deepEqual(await resultPromise, { text: "saved locally" });

  const errorMessage = nextMessage(channel.port2);
  const rejected = transport.storageRequest("set", {
    key: "draft",
    value: { text: "next" },
  });
  const errorRequest = await errorMessage;
  assert.deepEqual(errorRequest, {
    type: "storage_request",
    request_id: "storage-2",
    operation: "set",
    key: "draft",
    value: { text: "next" },
  });
  channel.port2.postMessage({
    type: "storage_response",
    request_id: errorRequest.request_id,
    error: { code: "quota_exceeded", message: "Widget storage quota exceeded" },
  });
  await assert.rejects(rejected, (error) => {
    assert.equal(error.code, "quota_exceeded");
    assert.equal(error.message, "Widget storage quota exceeded");
    return true;
  });

  shell.dispose();
  channel.port2.close();
});


test("uses the same port for capability RPC and bounded host events", async () => {
  const target = new FakeWindow();
  let transport;
  const shell = installFrameShell(target, {
    initializeController: async (_message, nextTransport) => {
      transport = nextTransport;
      return { dispose() {} };
    },
  });
  const channel = new MessageChannel();
  const offered = nextMessage(channel.port2);
  target.dispatch("message", portOffer(target, channel.port1));
  await offered;
  const ready = nextMessage(channel.port2);
  channel.port2.postMessage(initMessage());
  await ready;

  const rpcMessage = nextMessage(channel.port2);
  const rpcResult = transport.rpc("graph.mutate", { actions: [] });
  const rpcRequest = await rpcMessage;
  assert.deepEqual(rpcRequest, {
    type: "rpc_request",
    request_id: "rpc-1",
    method: "graph.mutate",
    params: { actions: [] },
  });
  channel.port2.postMessage({
    type: "rpc_response",
    request_id: rpcRequest.request_id,
    result: { status: "ok" },
  });
  assert.deepEqual(await rpcResult, { status: "ok" });

  const hostEventMessage = nextMessage(channel.port2);
  transport.hostEvent({
    event: "send_message",
    text: "x".repeat(20_000),
  });
  const hostEvent = await hostEventMessage;
  assert.equal(hostEvent.type, "host_event");
  assert.equal(hostEvent.event, "send_message");
  assert.equal(hostEvent.text.length, 16_384);

  shell.dispose();
  channel.port2.close();
});


test("invalidates the endpoint and rejects pending requests when the host session closes", async () => {
  const target = new FakeWindow();
  let transport;
  let controllerDisposed = false;
  const shell = installFrameShell(target, {
    initializeController: async (_message, nextTransport) => {
      transport = nextTransport;
      return {
        dispose() {
          controllerDisposed = true;
        },
      };
    },
  });
  const channel = new MessageChannel();
  const offered = nextMessage(channel.port2);
  target.dispatch("message", portOffer(target, channel.port1));
  await offered;
  const ready = nextMessage(channel.port2);
  channel.port2.postMessage(initMessage());
  await ready;

  const requestMessage = nextMessage(channel.port2);
  const pending = transport.rpc("graph.subscribe", { query: { type: "Note" } });
  await requestMessage;
  channel.port2.postMessage({
    type: "session_invalidated",
    reason: "Backend disconnected",
  });

  await assert.rejects(pending, /Backend disconnected/);
  assert.equal(controllerDisposed, true);
  shell.dispose();
  channel.port2.close();
});


test("fails closed when init does not echo the offered nonce", async () => {
  const target = new FakeWindow();
  let initialized = false;
  const shell = installFrameShell(target, {
    initializeController: async () => {
      initialized = true;
      return { dispose() {} };
    },
  });
  const channel = new MessageChannel();
  const offered = nextMessage(channel.port2);
  target.dispatch("message", portOffer(target, channel.port1));
  await offered;
  const errorMessage = nextMessage(channel.port2);
  channel.port2.postMessage(initMessage({ nonce: "wrong-nonce" }));
  const message = await errorMessage;

  assert.equal(message.type, "runtime_error");
  assert.equal(message.error.code, "runtime_protocol_invalid");
  assert.equal(initialized, false);
  shell.dispose();
  channel.port2.close();
});


test("reports controller initialization errors without exposing a stack", async () => {
  const target = new FakeWindow();
  const shell = installFrameShell(target, {
    initializeController: async () => {
      throw new Error("synthetic controller failure");
    },
  });
  const channel = new MessageChannel();
  const offered = nextMessage(channel.port2);
  target.dispatch("message", portOffer(target, channel.port1));
  await offered;
  const errorMessage = nextMessage(channel.port2);
  channel.port2.postMessage(initMessage());
  const message = await errorMessage;

  assert.deepEqual(message, {
    type: "runtime_error",
    error: {
      code: "controller_load_failed",
      message: "synthetic controller failure",
      classification: "code_only",
    },
  });
  assert.equal("stack" in message.error, false);
  shell.dispose();
  channel.port2.close();
});
