import assert from "node:assert/strict";
import { test } from "node:test";

import {
  createStorageApi,
  readTextFieldValue,
} from "../controller_facade.mjs";


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
