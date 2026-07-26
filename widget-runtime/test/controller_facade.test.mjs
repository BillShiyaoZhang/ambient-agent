import assert from "node:assert/strict";
import { test } from "node:test";

import {
  createStorageApi,
  mountController,
  readTextFieldValue,
} from "../controller_facade.mjs";


function mountTestController() {
  let ambient;
  const runtime = {
    Component: class {},
    Fragment: Symbol("Fragment"),
    createContext: () => ({}),
    h: (type, props, ...children) => ({
      type,
      props: { ...(props ?? {}), children },
    }),
    html: () => null,
    render(value) {
      if (value?.props?.ambient) ambient = value.props.ambient;
    },
    useCallback: (callback) => callback,
    useContext: () => undefined,
    useEffect: () => undefined,
    useMemo: (factory) => factory(),
    useReducer: () => [undefined, () => undefined],
    useRef: (current) => ({ current }),
    useState: (initial) => [initial, () => undefined],
  };
  const root = {
    ownerDocument: {
      documentElement: {
        dataset: {},
        style: {},
      },
    },
  };
  const controller = mountController({
    babel: {
      transform() {
        return {
          code: "exports.default = function Controller() { return null; };",
        };
      },
    },
    runtime,
    root,
    controllerSource: "export default function Controller() { return null; }",
    capabilityIds: [],
    presentationContext: {
      theme: { preference: "system", effective: "dark" },
      locale: "en-US",
      reduced_motion: false,
    },
    transport: {
      rpc: async () => undefined,
      storageRequest: async () => undefined,
      hostEvent: () => true,
    },
  });
  return { ambient, controller };
}


test("ambient.storage exposes the complete local storage contract", async () => {
  const calls = [];
  const storage = createStorageApi(async (operation, payload) => {
    calls.push({ operation, ...payload });
    if (operation === "get") return null;
    if (operation === "list") return ["a", "b"];
    return { status: "ok" };
  });

  assert.equal(await storage.get("missing"), null);
  assert.deepEqual(await storage.set("a", { count: 1 }), { status: "ok" });
  assert.deepEqual(await storage.delete("a"), { status: "ok" });
  assert.deepEqual(await storage.clear(), { status: "ok" });
  assert.deepEqual(await storage.list(), ["a", "b"]);
  assert.deepEqual(calls, [
    { operation: "get", key: "missing" },
    { operation: "set", key: "a", value: { count: 1 } },
    { operation: "delete", key: "a" },
    { operation: "clear" },
    { operation: "list" },
  ]);
  assert.equal(Object.isFrozen(storage), true);
});


test("TextField callbacks receive the current string value", () => {
  assert.equal(readTextFieldValue("Shanghai"), "Shanghai");
  assert.equal(
    readTextFieldValue({ currentTarget: { value: "北京" } }),
    "北京",
  );
  assert.equal(readTextFieldValue({ currentTarget: { value: 0 } }), "0");
  assert.equal(readTextFieldValue({}), "");
});


test("ambient.lifecycle keeps one replaceable pre-suspend handler", async () => {
  const { ambient, controller } = mountTestController();
  const calls = [];

  assert.equal(Object.isFrozen(ambient.lifecycle), true);
  assert.throws(
    () => ambient.lifecycle.onBeforeSuspend("not a function"),
    /requires a handler/,
  );

  const unsubscribeOld = ambient.lifecycle.onBeforeSuspend(() => {
    calls.push("old");
  });
  const unsubscribeCurrent = ambient.lifecycle.onBeforeSuspend(async () => {
    await Promise.resolve();
    calls.push("current");
  });

  unsubscribeOld();
  await controller.beforeSuspend();
  assert.deepEqual(calls, ["current"]);

  unsubscribeCurrent();
  unsubscribeCurrent();
  await controller.beforeSuspend();
  assert.deepEqual(calls, ["current"]);

  controller.dispose();
});


test("beforeSuspend propagates handler failure and does nothing after dispose", async () => {
  const { ambient, controller } = mountTestController();
  let calls = 0;
  ambient.lifecycle.onBeforeSuspend(async () => {
    calls += 1;
    throw new Error("synthetic flush failure");
  });

  await assert.rejects(
    controller.beforeSuspend(),
    /synthetic flush failure/,
  );
  assert.equal(calls, 1);

  controller.dispose();
  await controller.beforeSuspend();
  assert.equal(calls, 1);
});
