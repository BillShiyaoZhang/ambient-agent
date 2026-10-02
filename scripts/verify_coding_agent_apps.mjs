#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const require = createRequire(path.join(ROOT, "widget-runtime", "package.json"));
const { chromium } = require("playwright-core");
const { createFrameServer } = await import(path.join(ROOT, "widget-runtime", "frame_server.mjs"));
const DEFAULT_CASES = path.join(ROOT, "scripts", "coding_agent_eval_cases.json");

function argsFrom(argv) {
  const args = {};
  for (let index = 2; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for ${key}`);
    args[key.slice(2)] = value;
    index += 1;
  }
  return args;
}

const cli = argsFrom(process.argv);
const casePath = path.resolve(cli.cases || DEFAULT_CASES);
const casesDoc = JSON.parse(await fs.readFile(casePath, "utf8"));
const testCase = casesDoc.cases.find((item) => item.id === cli.case);
if (!testCase) throw new Error(`Unknown case '${cli.case}'. Expected one of: ${casesDoc.cases.map((item) => item.id).join(", ")}`);
const appDir = path.resolve(cli["app-dir"] || path.join(ROOT, "docs", "verification", "coding-agent-app-evaluation-artifacts", testCase.id));
const outputPath = path.resolve(cli.result || path.join(appDir, "interaction-result.json"));
const controllerSource = await fs.readFile(path.join(appDir, "controller.js"), "utf8");
const manifest = JSON.parse(await fs.readFile(path.join(appDir, "manifest.json"), "utf8"));
assert.equal(manifest.id, testCase.id, "generated manifest ID must match its evaluation case");

async function artifactDigest(directory) {
  const files = [];
  for (const name of ["controller.js", "manifest.json", "README.md"]) {
    const absolute = path.join(directory, name);
    try { if ((await fs.stat(absolute)).isFile()) files.push(absolute); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  async function addDataFiles(current) {
    let entries;
    try { entries = await fs.readdir(current, { withFileTypes: true }); }
    catch (error) { if (error.code === "ENOENT") return; throw error; }
    for (const entry of entries) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) await addDataFiles(absolute);
      else if (entry.isFile()) files.push(absolute);
    }
  }
  await addDataFiles(path.join(directory, "data"));
  const digest = createHash("sha256");
  for (const file of files.sort((left, right) => path.relative(directory, left).localeCompare(path.relative(directory, right)))) {
    digest.update(path.relative(directory, file).split(path.sep).join("/"));
    digest.update(await fs.readFile(file));
  }
  return digest.digest("hex");
}

const browserCandidates = [
  process.env.CHROMIUM_EXECUTABLE_PATH,
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const executablePath = browserCandidates.find((candidate) => {
  try { return require("node:fs").existsSync(candidate); } catch { return false; }
});
if (!executablePath) throw new Error("Chromium is unavailable; set CHROMIUM_EXECUTABLE_PATH");

const server = createFrameServer();
await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});
const frameUrl = `http://127.0.0.1:${server.address().port}/frame.html`;
const fixture = structuredClone(testCase.fixture);
fixture.files ??= {};
fixture.graph ??= [];
fixture.rpcLog = [];
fixture.storage = {};
fixture.failFileReads = 0;
fixture.nextNetworkResponses = [];
const evidence = { case_id: testCase.id, artifact_dir: appDir, artifact_hash: await artifactDigest(appDir), checks: [], rpc: [], page_errors: [], screenshot: null };
let browser;
let page;
let frame;
let iframeSequence = 0;

function recordCheck(id, detail = "") {
  evidence.checks.push({ id, passed: true, detail });
}

function regexFor(words) {
  return new RegExp(words, "i");
}

async function bodyText() {
  return frame.locator("body").innerText();
}

async function expectBody(words, description, timeout = 5_000) {
  const pattern = regexFor(words);
  const deadline = Date.now() + timeout;
  let current = "";
  while (Date.now() < deadline) {
    try { current = await bodyText(); } catch { /* frame is navigating */ }
    if (pattern.test(current)) {
      recordCheck(description, words);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${description} /${pattern.source}/; body was: ${current}`);
}

async function expectBodyAbsent(words, description, timeout = 5_000) {
  const pattern = regexFor(words);
  const deadline = Date.now() + timeout;
  let current = "";
  while (Date.now() < deadline) {
    try { current = await bodyText(); } catch { /* frame is navigating */ }
    if (!pattern.test(current)) {
      recordCheck(description, words);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${description} to disappear /${pattern.source}/; body was: ${current}`);
}

async function button(words, options = {}) {
  const locator = frame.getByRole("button", { name: regexFor(words) }).first();
  await locator.waitFor({ state: "visible", timeout: options.timeout || 5_000 });
  await locator.click();
  return locator;
}

async function textboxes() {
  return frame.locator('input[type="text"], input:not([type]), textarea').all();
}

async function fillFirstTextboxes(values) {
  const boxes = await textboxes();
  assert.ok(boxes.length >= values.length, `expected ${values.length} text fields, found ${boxes.length}`);
  for (let index = 0; index < values.length; index += 1) await boxes[index].fill(values[index]);
}

async function findRow(text) {
  const needle = frame.getByText(text, { exact: false }).first();
  await needle.waitFor({ state: "visible" });
  // UI primitives render Cards/Lists as nested divs. Choose the nearest ancestor with a control.
  return needle.locator("xpath=ancestor::*[.//button or .//input][1]");
}

async function fillMatchingTextbox(oldValue, newValue) {
  const inputs = frame.locator('input[type="text"], input:not([type]), textarea');
  const count = await inputs.count();
  for (let index = 0; index < count; index += 1) {
    const candidate = inputs.nth(index);
    if (await candidate.inputValue() === oldValue) {
      await candidate.fill(newValue);
      return;
    }
  }
  throw new Error(`No editor textbox contained the expected current value '${oldValue}'`);
}

async function expectTextboxValue(expected, description, timeout = 5_000) {
  const deadline = Date.now() + timeout;
  let values = [];
  while (Date.now() < deadline) {
    const inputs = frame.locator('input[type="text"], input:not([type]), textarea');
    const count = await inputs.count();
    values = await Promise.all(Array.from({ length: count }, (_, index) => inputs.nth(index).inputValue()));
    if (values.includes(expected)) {
      recordCheck(description, `visible textbox value: ${expected}`);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for textbox value '${expected}'; values were: ${JSON.stringify(values)}`);
}

async function expectTextboxAbsent(expected, description, timeout = 5_000) {
  const deadline = Date.now() + timeout;
  let values = [];
  while (Date.now() < deadline) {
    const inputs = frame.locator('input[type="text"], input:not([type]), textarea');
    const count = await inputs.count();
    values = await Promise.all(Array.from({ length: count }, (_, index) => inputs.nth(index).inputValue()));
    if (!values.includes(expected)) {
      recordCheck(description, `textbox value disappeared: ${expected}`);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for textbox value '${expected}' to disappear; values were: ${JSON.stringify(values)}`);
}

async function findRowByTextboxValue(value) {
  const inputs = frame.locator('input[type="text"], input:not([type]), textarea');
  const count = await inputs.count();
  for (let index = 0; index < count; index += 1) {
    const input = inputs.nth(index);
    if (await input.inputValue() === value) return input.locator("xpath=ancestor::*[.//button or .//input][1]");
  }
  throw new Error(`No rendered task textbox contains '${value}'`);
}

async function clickInRow(row, words) {
  const control = row.getByRole("button", { name: regexFor(words) }).first();
  await control.waitFor({ state: "visible" });
  await control.click();
  return control;
}

async function bootWidget() {
  iframeSequence += 1;
  const index = iframeSequence - 1;
  await page.evaluate(({ frameUrl, index, source, capabilityIds, initialFixture }) => {
    const prior = document.querySelector('iframe[title="Generated Ambient Widget"]');
    if (prior) prior.remove();
    const iframe = document.createElement("iframe");
    iframe.setAttribute("title", "Generated Ambient Widget");
    iframe.style.width = "640px";
    iframe.style.height = "480px";
    iframe.src = frameUrl;
    document.body.append(iframe);
    window.__widgetReady = false;
    window.__widgetPort?.close();
    window.__widgetEvents = [];
    const channel = new MessageChannel();
    window.__widgetPort = channel.port1;
    const fixture = window.__evalFixture || initialFixture;
    fixture.graphSubscriptions = [];
    const post = (message) => channel.port1.postMessage(message);
    const rpcError = (request, error, code = "fixture_error") => post({ type: "rpc_response", request_id: request.request_id, error: { code, message: error } });
    const rpcResult = (request, result) => post({ type: "rpc_response", request_id: request.request_id, result });
    const publishGraph = (subscriptionId) => post({ type: "subscription_event", subscription_id: subscriptionId, data: structuredClone(fixture.graph) });
    channel.port1.onmessage = async (event) => {
      const message = event.data;
      if (message.type === "port_ready") {
        post({ type: "init", protocol_version: 1, nonce: "coding-agent-eval", controller_source: source, capability_ids: capabilityIds, presentation_context: { theme: { preference: "light", effective: "light" }, locale: "en-US", reduced_motion: true } });
      } else if (message.type === "ready") {
        window.__widgetReady = true;
      } else if (message.type === "runtime_error") {
        window.__widgetEvents.push({ type: "runtime_error", error: message.error });
      } else if (message.type === "rpc_request") {
        fixture.rpcLog.push({ method: message.method, params: structuredClone(message.params) });
        const { method, params } = message;
        try {
          if (method === "files.read") {
            if (fixture.failFileReads > 0) { fixture.failFileReads -= 1; throw new Error("Fixture file read failed"); }
            if (!Object.hasOwn(fixture.files, params.path)) throw Object.assign(new Error("App data file not found"), { code: "file_not_found" });
            rpcResult(message, fixture.files[params.path]);
          } else if (method === "files.write") {
            if (fixture.failFileWrites > 0) { fixture.failFileWrites -= 1; throw new Error("Fixture file write failed"); }
            fixture.files[params.path] = params.text;
            rpcResult(message, { ok: true });
          } else if (method === "files.delete") {
            delete fixture.files[params.path]; rpcResult(message, { ok: true });
          } else if (method === "graph.subscribe") {
            if (fixture.failGraphSubscribe > 0) { fixture.failGraphSubscribe -= 1; throw new Error("Fixture Graph subscription failed"); }
            fixture.graphSubscriptions ??= [];
            fixture.graphSubscriptions.push(params.subscription_id);
            rpcResult(message, structuredClone(fixture.graph));
          } else if (method === "graph.unsubscribe") {
            rpcResult(message, { ok: true });
          } else if (method === "graph.mutate") {
            if (fixture.failGraphMutates > 0) { fixture.failGraphMutates -= 1; throw new Error("Fixture Graph mutation failed"); }
            for (const action of params.actions) {
              if (action.action === "create_node") fixture.graph.push({ id: `node-${fixture.graph.length + 1}`, type: action.type, properties: structuredClone(action.properties || {}) });
              else if (action.action === "update_node_property") {
                const node = fixture.graph.find((item) => item.id === action.id); if (node) Object.assign(node.properties, action.properties);
              } else if (action.action === "delete_node") fixture.graph = fixture.graph.filter((item) => item.id !== action.id);
            }
            rpcResult(message, { ok: true });
            for (const subscriptionId of fixture.graphSubscriptions || []) publishGraph(subscriptionId);
          } else if (method === "net.request") {
            const responses = fixture.nextNetworkResponses.length ? fixture.nextNetworkResponses : fixture.responses;
            const response = responses.shift();
            if (!response) throw new Error("No fixture network response remains");
            if (response.delay_ms) await new Promise((resolve) => setTimeout(resolve, response.delay_ms));
            if (response.status >= 400) throw new Error(`HTTP ${response.status}: ${response.body?.error || "Request failed"}`);
            rpcResult(message, structuredClone(response.body));
          } else {
            throw new Error(`Unexpected fixture RPC: ${method}`);
          }
        } catch (error) { rpcError(message, error.message || String(error), error.code || "fixture_error"); }
      } else if (message.type === "storage_request") {
        const { operation, key } = message;
        if (operation === "get") post({ type: "storage_response", request_id: message.request_id, result: fixture.storage[key] ?? null });
        else if (operation === "set") { fixture.storage[key] = message.value; post({ type: "storage_response", request_id: message.request_id, result: true }); }
        else if (operation === "delete") { delete fixture.storage[key]; post({ type: "storage_response", request_id: message.request_id, result: true }); }
        else if (operation === "clear") { fixture.storage = {}; post({ type: "storage_response", request_id: message.request_id, result: true }); }
        else if (operation === "list") post({ type: "storage_response", request_id: message.request_id, result: Object.keys(fixture.storage) });
      } else if (message.type === "host_event") window.__widgetEvents.push(message);
    };
    channel.port1.start();
    iframe.addEventListener("load", () => iframe.contentWindow.postMessage({ type: "ambient-widget-port", protocol_version: 1, nonce: "coding-agent-eval" }, "*", [channel.port2]), { once: true });
    window.__evalFixture = fixture;
  }, { frameUrl, index, source: controllerSource, capabilityIds: manifest.capabilities.map((item) => item.id), initialFixture: fixture });
  await page.waitForFunction(() => window.__widgetReady === true, null, { timeout: 10_000 });
  frame = page.frameLocator(`iframe[title="Generated Ambient Widget"]`);
}

async function reloadWidget() {
  await bootWidget();
  recordCheck("reload-recreated-production-frame", "frame shell reinitialized with retained fixture data");
}

async function rpcLog() {
  return page.evaluate(() => structuredClone(window.__evalFixture.rpcLog));
}

async function waitForRpc(predicate, description, timeout = 5_000) {
  const deadline = Date.now() + timeout;
  let entries = [];
  while (Date.now() < deadline) {
    entries = await rpcLog();
    const match = entries.find(predicate);
    if (match) return match;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${description}; RPC log: ${JSON.stringify(entries)}`);
}

async function fixtureFile(pathname) {
  return page.evaluate((name) => window.__evalFixture.files[name] ?? null, pathname);
}

async function waitForFile(predicate, description, timeout = 5_000) {
  const deadline = Date.now() + timeout;
  let raw;
  while (Date.now() < deadline) {
    raw = await fixtureFile("books.json");
    try {
      const data = JSON.parse(raw);
      if (predicate(data)) return data;
    } catch { /* wait for the current files.write request */ }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out waiting for ${description}; books.json was ${raw}`);
}

async function setFixture(changes) {
  await page.evaluate((next) => Object.assign(window.__evalFixture, structuredClone(next)), changes);
}

async function runReadingList() {
  await expectBody("empty|no books|no titles|no entries|nothing here|nothing yet", "render-empty-state");
  await fillFirstTextboxes(["The Left Hand of Darkness", "Ursula K. Le Guin"]);
  await button("add book");
  await expectBody("The Left Hand of Darkness", "create-book");
  await expectBody("Ursula K\\.? Le Guin", "read-book");
  let savedBooks = await waitForFile((books) => books.some((book) => book.title === "The Left Hand of Darkness"), "created book persistence");
  assert.equal(savedBooks.length, 1);
  assert.equal(savedBooks[0].author, "Ursula K. Le Guin");
  assert.equal(savedBooks[0].read, false);
  const initialFileCalls = await rpcLog();
  const fileCalls = initialFileCalls.filter((entry) => entry.method === "files.read" || entry.method === "files.write");
  assert.ok(fileCalls.some((entry) => entry.method === "files.read"), "Widget did not read books.json");
  assert.ok(fileCalls.some((entry) => entry.method === "files.write"), "Widget did not persist books.json");
  assert.ok(fileCalls.every((entry) => entry.params.path === "books.json"), "Widget accessed a file outside books.json");
  assert.ok(fileCalls.some((entry) => entry.method === "files.write" && entry.params.text.includes("The Left Hand of Darkness")), "saved books.json did not contain the created book");
  recordCheck("write-books-json", "private file read/write RPCs observed");

  await reloadWidget();
  await expectBody("The Left Hand of Darkness", "reload-retains-books");

  const row = await findRow("The Left Hand of Darkness");
  await clickInRow(row, "edit|rename|修改|编辑");
  await fillMatchingTextbox("The Left Hand of Darkness", "The Dispossessed");
  await button("save|update|done|保存|完成");
  await expectBody("The Dispossessed", "edit-book");
  savedBooks = await waitForFile((books) => books[0]?.title === "The Dispossessed", "edited book persistence");
  assert.equal(savedBooks[0].title, "The Dispossessed", "edited title was not saved to books.json");
  assert.equal(savedBooks[0].author, "Ursula K. Le Guin", "edited author was not retained");

  const updatedRow = await findRow("The Dispossessed");
  const checkbox = updatedRow.getByRole("checkbox").first();
  if (await checkbox.count()) {
    assert.equal(await checkbox.isChecked(), false, "new book should start unread");
    await checkbox.check();
    assert.equal(await checkbox.isChecked(), true, "read checkbox did not toggle on");
    savedBooks = await waitForFile((books) => books[0]?.read === true, "read state persistence");
    assert.equal(savedBooks[0].read, true, "read state was not persisted");
    recordCheck("toggle-read", "read checkbox changed to checked");
    await checkbox.uncheck();
    assert.equal(await checkbox.isChecked(), false, "read checkbox did not toggle back to unread");
    savedBooks = await waitForFile((books) => books[0]?.read === false, "unread state persistence");
    assert.equal(savedBooks[0].read, false, "unread state was not persisted");
    recordCheck("toggle-unread", "read checkbox changed back to unchecked");
  } else {
    await clickInRow(updatedRow, "mark read|read|已读|完成");
    savedBooks = await waitForFile((books) => books[0]?.read === true, "read state persistence");
    assert.equal(savedBooks[0].read, true, "read state was not persisted");
    recordCheck("toggle-read", "read state persisted as true");
    await clickInRow(updatedRow, "mark unread|unread|undo|未读");
    savedBooks = await waitForFile((books) => books[0]?.read === false, "unread state persistence");
    assert.equal(savedBooks[0].read, false, "unread state was not persisted");
    recordCheck("toggle-unread", "unread state persisted as false");
  }

  const finalRow = await findRow("The Dispossessed");
  await clickInRow(finalRow, "delete|remove|删除");
  await expectBodyAbsent("The Dispossessed", "delete-book");
  savedBooks = await waitForFile((books) => books.length === 0, "book deletion persistence");
  assert.equal(savedBooks.length, 0, "delete did not persist an empty books.json list");

  await setFixture({ failFileReads: 1 });
  await reloadWidget();
  await expectBody("error|failed|unavailable|problem|could.{0,4}load|unable to load", "file-error-is-visible");
  await button("retry|try again|reload|重试");
  await expectBody("empty|no books|no titles|no entries|nothing here|nothing yet", "file-read-retry");
  await setFixture({ failFileWrites: 1 });
  const beforeFailedWrite = await fixtureFile("books.json");
  await fillFirstTextboxes(["Write failure probe", "A. Reader"]);
  const authorField = (await textboxes())[1];
  await authorField.press("Tab");
  await page.keyboard.press("Enter");
  await expectBody("error|failed|unable|problem|could.?n.?t save|save your changes", "file-write-error-is-visible");
  assert.equal(await fixtureFile("books.json"), beforeFailedWrite, "a rejected file write changed books.json");
}

async function runTaskBoard() {
  await expectBody("empty|no tasks|no items|nothing here|nothing yet", "render-empty-state");
  const before = await rpcLog();
  assert.ok(before.some((entry) => entry.method === "graph.subscribe" && entry.params.query?.type === "Task"), "Widget did not subscribe to Task nodes");
  recordCheck("subscribe-task", "graph.subscribe RPC observed");
  const openFilter = frame.getByRole("button", { name: /open|active|incomplete/i }).first();
  const completedFilter = frame.getByRole("button", { name: /completed|done|finished/i }).first();
  await openFilter.waitFor({ state: "visible" });
  await completedFilter.waitFor({ state: "visible" });
  await openFilter.click();
  await fillFirstTextboxes(["Write acceptance tests"]);
  await (await textboxes())[0].press("Enter");
  await expectTextboxValue("Write acceptance tests", "create-task");
  await fillMatchingTextbox("Write acceptance tests", "Write browser acceptance tests");
  await button("rename|修改|重命名");
  await expectTextboxValue("Write browser acceptance tests", "rename-task");
  await fillFirstTextboxes(["Keep this draft across reload"]);
  await page.waitForFunction(() => window.__evalFixture.storage["task-board.drafts.v1"]?.newTitle === "Keep this draft across reload", null, { timeout: 5_000 });
  recordCheck("persist-task-draft", "unsent task draft written to host storage");
  const checkbox = frame.getByRole("checkbox").first();
  if (await checkbox.count()) {
    await checkbox.click();
    await waitForRpc((entry) => entry.method === "graph.mutate" && entry.params.actions?.some((action) => action.properties?.done === true), "done=true Graph mutation");
  }
  else await button("mark done|complete|done|完成");
  if (await checkbox.count()) {
    recordCheck("toggle-done", "done=true Graph mutation observed");
  } else {
    await expectBody("done|completed|✓|true|已完成", "toggle-done");
  }
  await expectTextboxAbsent("Write browser acceptance tests", "open-filter-excludes-completed-task");
  await completedFilter.click();
  await expectTextboxValue("Write browser acceptance tests", "completed-filter-includes-task");
  recordCheck("query-open-completed", "open filter excludes completed Task; completed filter includes it");
  await reloadWidget();
  await expectTextboxValue("Write browser acceptance tests", "reload-retains-graph-state");
  assert.equal(await frame.getByRole("checkbox").first().isChecked(), true, "reloaded Task did not retain its completed state");
  await expectTextboxValue("Keep this draft across reload", "reload-restores-task-draft");
  await button("delete|remove|删除");
  await expectTextboxAbsent("Write browser acceptance tests", "delete-task");

  const mutations = (await rpcLog()).filter((entry) => entry.method === "graph.mutate");
  const actions = mutations.flatMap((entry) => entry.params.actions || []);
  assert.ok(actions.some((action) => action.action === "create_node" && action.type === "Task" && action.properties?.title === "Write acceptance tests"), "Graph did not create the Task with its title");
  assert.ok(actions.some((action) => action.action === "update_node_property" && action.properties?.title === "Write browser acceptance tests"), "Graph did not save the renamed Task title");
  assert.ok(actions.some((action) => action.action === "update_node_property" && action.properties?.done === true), "Graph did not save the completed state");
  assert.ok(actions.some((action) => action.action === "delete_node"), "Graph did not delete the Task");
  await setFixture({ failGraphMutates: 1 });
  await fillFirstTextboxes(["Failure probe"]);
  await (await textboxes())[0].press("Enter");
  await expectBody("error|failed|unavailable|problem", "graph-error-is-visible");
}

async function runServiceStatus() {
  await expectBody("loading|checking|fetching|載入", "render-loading-state", 2_000);
  await page.waitForFunction(() => window.__evalFixture.rpcLog.some((item) => item.method === "net.request"), null, { timeout: 5_000 });
  let calls = await rpcLog();
  const networkCall = calls.find((entry) => entry.method === "net.request");
  assert.equal(networkCall.params.source_id, testCase.fixture.source_id);
  assert.equal(networkCall.params.request.path, testCase.fixture.path);
  assert.equal(networkCall.params.request.method, "GET");
  recordCheck("request-approved-source", "literal source, path, and GET observed in host RPC");
  await expectBody("error|failed|unavailable|temporarily", "render-request-error");
  await button("retry|try again|refresh|重试|刷新");
  await expectBody("Search", "render-success-response");
  await expectBody("operational", "render-success-status");
  calls = await rpcLog();
  assert.ok(calls.filter((entry) => entry.method === "net.request").length >= 2, "retry did not issue a fresh request");
  recordCheck("retry-after-error", "a second network request succeeded");
  await setFixture({ nextNetworkResponses: [{ status: 200, body: { services: [{ name: "Broken", status: { unexpected: true } }] } }] });
  await reloadWidget();
  await expectBody("error|invalid|malformed|unexpected|unable|missing", "reject-malformed-response");
  assert.ok(!(await bodyText()).includes("Broken"), "malformed service data was rendered as valid success");
  await setFixture({ nextNetworkResponses: [{ status: 200, body: { services: [] } }] });
  await reloadWidget();
  await expectBody("no services|no status|no data|empty|no monitored services", "render-empty-response");
  calls = await rpcLog();
  const networkCalls = calls.filter((entry) => entry.method === "net.request");
  assert.ok(networkCalls.length >= 4, "expected initial, retry, malformed, and empty-response requests");
  assert.ok(networkCalls.every((entry) => entry.params.source_id === testCase.fixture.source_id && entry.params.request.path === testCase.fixture.path && entry.params.request.method === "GET"), "network request escaped the approved source, path, or method");
}

let failure;
try {
  browser = await chromium.launch({ executablePath, headless: true });
  page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  page.on("pageerror", (error) => evidence.page_errors.push(error.message));
  await page.setContent("<!doctype html><html><body></body></html>");
  if (testCase.id === "private-reading-list") fixture.failFileReads = 0;
  if (testCase.id === "service-status") fixture.responses = structuredClone(testCase.fixture.responses).map((response, index) => ({ ...response, delay_ms: index === 0 ? 150 : 0 }));
  await bootWidget();
  if (testCase.id === "private-reading-list") await runReadingList();
  else if (testCase.id === "project-task-board") await runTaskBoard();
  else if (testCase.id === "service-status") await runServiceStatus();
  else throw new Error(`No interaction procedure for ${testCase.id}`);
  evidence.rpc = await rpcLog();
  evidence.runtime_events = await page.evaluate(() => structuredClone(window.__widgetEvents || []));
  assert.equal(evidence.page_errors.length, 0, `Widget page errors: ${evidence.page_errors.join("; ")}`);
  const runtimeErrors = evidence.runtime_events.filter((event) => event.type === "runtime_error");
  assert.equal(runtimeErrors.length, 0, `Widget runtime errors: ${JSON.stringify(runtimeErrors)}`);
  evidence.screenshot = path.join(path.dirname(outputPath), `${testCase.id}.png`);
  await fs.mkdir(path.dirname(evidence.screenshot), { recursive: true });
  await page.screenshot({ path: evidence.screenshot, fullPage: true });
} catch (error) {
  failure = error;
  if (page) {
    try {
      evidence.rpc = await rpcLog();
      evidence.page_text = await bodyText();
      evidence.screenshot = path.join(path.dirname(outputPath), `${testCase.id}-failed.png`);
      await fs.mkdir(path.dirname(evidence.screenshot), { recursive: true });
      await page.screenshot({ path: evidence.screenshot, fullPage: true });
    } catch { /* preserve the original assertion failure */ }
  }
}
evidence.passed = !failure;
if (failure) evidence.error = failure.stack || String(failure);
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`);
await browser?.close();
await new Promise((resolve) => server.close(resolve));
process.stdout.write(`${JSON.stringify({ case_id: testCase.id, passed: evidence.passed, checks: evidence.checks.length, result: outputPath, screenshot: evidence.screenshot })}\n`);
if (failure) process.exitCode = 1;
