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
const CASES_PATH = path.join(ROOT, "scripts", "coding_agent_ui_quality_cases.json");
const cli = Object.fromEntries(process.argv.slice(2).reduce((args, item, index, all) => {
  if (index % 2 === 0) args.push([item.replace(/^--/, ""), all[index + 1]]);
  return args;
}, []));
const casesPath = path.resolve(cli.cases || CASES_PATH);
const casesDoc = JSON.parse(await fs.readFile(casesPath, "utf8"));
const testCase = casesDoc.cases.find((item) => item.id === cli.case);
if (!testCase) throw new Error(`Unknown UI-quality case '${cli.case}'`);
const caseRoot = path.resolve(cli["case-root"] || path.join(ROOT, "docs", "verification", "coding-agent-ui-quality-artifacts", testCase.id));
const appDir = path.resolve(cli["app-dir"] || path.join(caseRoot, "app"));
const outputPath = path.resolve(cli.result || path.join(caseRoot, "browser-report.json"));
const source = await fs.readFile(path.join(appDir, "controller.js"), "utf8");
const manifest = JSON.parse(await fs.readFile(path.join(appDir, "manifest.json"), "utf8"));
assert.equal(manifest.id, testCase.id);
const rendererSources = [
  "widget-runtime/controller_facade.mjs",
  "widget-runtime/frame_server.mjs",
  "widget-runtime/frame_shell.mjs",
  "widget-runtime/frame_shell.css",
  "widget-runtime/presentation_context.mjs",
  "widget-runtime/runtime.mjs",
];
const rendererSourceHashes = {};
for (const relative of rendererSources) rendererSourceHashes[relative] = createHash("sha256").update(await fs.readFile(path.join(ROOT, relative))).digest("hex");

async function artifactDigest(directory) {
  const files = [];
  async function collect(directory) {
    let entries;
    try { entries = await fs.readdir(directory, { withFileTypes: true }); }
    catch (error) { if (error.code === "ENOENT") return; throw error; }
    for (const entry of entries) {
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) await collect(file);
      else if (entry.isFile() && !entry.isSymbolicLink()) files.push(file);
    }
  }
  await collect(directory);
  const hash = createHash("sha256");
  for (const file of files.sort((a, b) => path.relative(directory, a).localeCompare(path.relative(directory, b)))) {
    hash.update(path.relative(directory, file).split(path.sep).join("/"));
    hash.update(await fs.readFile(file));
  }
  return hash.digest("hex");
}

const baseFixture = structuredClone(testCase.fixture);
baseFixture.files = baseFixture.initial || {};
baseFixture.graph = [];
baseFixture.rpcLog = [];
baseFixture.storage = {};
baseFixture.failFileReads = 0;
baseFixture.failFileWrites = 0;
baseFixture.nextNetworkResponses = [];
const evidence = {
  case_id: testCase.id,
  model: "gpt-6-luna",
  artifact_dir: appDir,
  artifact_hash: await artifactDigest(appDir),
  case_set_sha256: createHash("sha256").update(await fs.readFile(casesPath)).digest("hex"),
  frame: "production widget-runtime/frame_server.mjs",
  checks: [],
  screenshots: [],
  rpc: [],
  page_errors: [],
  blocked_network_attempts: [],
  capture_host: { light_background: "#f8fafc", dark_background: "#0b1220", color_scheme_tracks_frame: true },
};
const hostBackground = { light: "#f8fafc", dark: "#0b1220" };
const server = createFrameServer();
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
const frameUrl = `http://127.0.0.1:${server.address().port}/frame.html`;
const candidates = [process.env.CHROMIUM_EXECUTABLE_PATH, "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].filter(Boolean);
const executablePath = candidates.find((item) => require("node:fs").existsSync(item));
if (!executablePath) throw new Error("Chromium unavailable; set CHROMIUM_EXECUTABLE_PATH");
let browser;
let page;
let frame;
let fixture;
let frameSequence = 0;
let pendingFixtureChanges = {};

function check(id, detail = "") { evidence.checks.push({ id, passed: true, detail }); }
const recordRpc = async () => page.evaluate(() => structuredClone(window.__fixture.rpcLog));
async function bodyText() { return frame.locator("body").innerText(); }
async function waitForRpc(predicate, description, timeout = 6000) {
  const deadline = Date.now() + timeout;
  let entries = [];
  while (Date.now() < deadline) {
    entries = await recordRpc();
    if (predicate(entries)) return entries;
    await new Promise((resolve) => setTimeout(resolve, 70));
  }
  throw new Error(`Timed out waiting for ${description}; host RPC log: ${JSON.stringify(entries)}`);
}
async function waitBody(words, timeout = 7000) {
  const pattern = words instanceof RegExp ? words : new RegExp(words, "i");
  const deadline = Date.now() + timeout;
  let text = "";
  while (Date.now() < deadline) {
    try { text = await bodyText(); } catch { /* frame is mounting */ }
    if (pattern.test(text)) return text;
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  throw new Error(`Timed out waiting for ${pattern}; widget text: ${text}`);
}
async function namedButton(pattern) {
  const target = frame.getByRole("button", { name: pattern }).first();
  await target.waitFor({ state: "visible", timeout: 5000 });
  return target;
}
async function dataPlot() {
  const semanticChart = frame.getByRole("img", { name: /temperature|forecast|trend/i }).first();
  if (await semanticChart.count()) {
    await semanticChart.waitFor({ state: "visible", timeout: 5000 });
    return semanticChart;
  }
  const candidates = frame.locator("svg:visible");
  const count = await candidates.count();
  for (let index = 0; index < count; index += 1) {
    const candidate = candidates.nth(index);
    const properties = await candidate.evaluate((node) => ({
      bounds: node.getBoundingClientRect().toJSON(),
      plottedPath: [...node.querySelectorAll("path")].some((item) => (item.getAttribute("d") || "").length > 24),
      plottedMarks: node.querySelectorAll("circle, rect, polygon, polyline").length,
    }));
    if (properties.bounds.width > 80 && properties.bounds.height > 35 && (properties.plottedPath || properties.plottedMarks >= 7)) return candidate;
  }
  throw new Error("No visible chart with a data-graphic accessible name or plotted SVG geometry was found");
}
async function chartGeometry(chart) {
  return chart.evaluate((node) => {
    const svg = node.matches("svg") ? node : node.querySelector("svg");
    const svgPaths = [...(svg?.querySelectorAll("path") || [])].map((item) => item.getAttribute("d") || "");
    const svgMarks = [...(svg?.querySelectorAll("circle, rect, polygon, polyline") || [])].map((item) => ["cx", "cy", "x", "y", "width", "height", "points"].map((key) => item.getAttribute(key) || "").join(","));
    const cssMarks = [...node.querySelectorAll("*")].flatMap((item) => {
      const style = getComputedStyle(item);
      const bounds = item.getBoundingClientRect();
      return style.backgroundImage !== "none" && bounds.width >= 3 && bounds.height >= 12 ? [Math.round(bounds.height)] : [];
    });
    return { bounds: node.getBoundingClientRect().toJSON(), svgPaths, svgMarks, cssMarks, text: node.innerText || node.textContent || "" };
  });
}
async function startFrame(theme, width, height = 680) {
  frameSequence += 1;
  await page.emulateMedia({ colorScheme: theme });
  await page.setViewportSize({ width, height });
  fixture = structuredClone(baseFixture);
  fixture.rpcLog = [];
  fixture.graph = [];
  fixture.storage = {};
  if (testCase.fixture.kind === "http") fixture.responses = structuredClone(testCase.fixture.responses);
  Object.assign(fixture, structuredClone(pendingFixtureChanges));
  pendingFixtureChanges = {};
  await page.evaluate(({ url, sequence, controller, capabilityIds, initialFixture, theme, background }) => {
    document.body.replaceChildren();
    document.documentElement.style.colorScheme = theme;
    document.documentElement.style.background = background;
    document.body.style.cssText = `margin:0;padding:0;width:100%;min-height:100vh;background:${background}`;
    const iframe = document.createElement("iframe");
    iframe.title = "Generated Ambient Widget";
    iframe.style.cssText = "display:block;width:100%;height:680px;border:0";
    iframe.src = `${url}?run=${sequence}`;
    document.body.append(iframe);
    window.__fixture = initialFixture;
    window.__ready = false;
    window.__runtimeErrors = [];
    const channel = new MessageChannel();
    const post = (message) => channel.port1.postMessage(message);
    channel.port1.onmessage = async ({ data: message }) => {
      if (message.type === "port_ready") {
        post({ type: "init", protocol_version: 1, nonce: "ui-quality-eval", controller_source: controller,
          capability_ids: capabilityIds, presentation_context: { theme: { preference: theme, effective: theme }, locale: "en-US", reduced_motion: true } });
      } else if (message.type === "ready") window.__ready = true;
      else if (message.type === "runtime_error") window.__runtimeErrors.push(message.error);
      else if (message.type === "rpc_request") {
        const current = window.__fixture;
        current.rpcLog.push({ method: message.method, params: structuredClone(message.params) });
        const respond = (result) => post({ type: "rpc_response", request_id: message.request_id, result });
        const fail = (error, code = "fixture_error") => post({ type: "rpc_response", request_id: message.request_id, error: { message: String(error), code } });
        try {
          const { method, params } = message;
          if (method === "net.request") {
            if (params.source_id !== current.source_id || params.request.path !== current.path || params.request.method !== "GET") throw new Error("Request escaped approved fixture source");
            const response = current.nextNetworkResponses.length ? current.nextNetworkResponses.shift() : current.responses.shift();
            if (!response) throw new Error("No fixture response remains");
            if (response.delay_ms) await new Promise((resolve) => setTimeout(resolve, response.delay_ms));
            if (response.status >= 400) throw new Error(response.body?.error || `Request failed (${response.status})`);
            respond(structuredClone(response.body));
          } else if (method === "files.read") {
            if (current.failFileReads > 0) { current.failFileReads -= 1; throw new Error("TypeError: upstream provider failed\n    at vendor-provider.js:314:9"); }
            if (!Object.hasOwn(current.files, params.path)) throw Object.assign(new Error("App data file not found"), { code: "file_not_found" });
            respond(current.files[params.path]);
          } else if (method === "files.write") {
            if (current.failFileWrites > 0) { current.failFileWrites -= 1; throw new Error("Fixture file write failed"); }
            current.files[params.path] = params.text; respond({ ok: true });
          } else if (method === "files.delete") { delete current.files[params.path]; respond({ ok: true }); }
          else throw new Error(`Unexpected fixture RPC: ${method}`);
        } catch (error) { fail(error.message || error, error.code || "fixture_error"); }
      }
    };
    channel.port1.start();
    iframe.addEventListener("load", () => iframe.contentWindow.postMessage({ type: "ambient-widget-port", protocol_version: 1, nonce: "ui-quality-eval" }, "*", [channel.port2]), { once: true });
  }, { url: frameUrl, sequence: frameSequence, controller: source, capabilityIds: manifest.capabilities.map((capability) => capability.id), initialFixture: fixture, theme, background: hostBackground[theme] });
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 10000 });
  frame = page.frameLocator('iframe[title="Generated Ambient Widget"]');
}

async function verifyFrameBasics(theme, width) {
  await startFrame(theme, width);
  if (testCase.id === "forecast-operations-dashboard") await waitBody(/Harbor Station/);
  else await waitBody(/empty|no follow|nothing yet|add follow/i);
  await page.waitForTimeout(250);
  const metrics = await frame.locator("html").evaluate((html) => ({ client: html.clientWidth, scroll: Math.max(html.scrollWidth, html.ownerDocument.body.scrollWidth) }));
  assert.ok(metrics.scroll <= metrics.client + 1, `horizontal overflow at ${width}px ${theme}: ${JSON.stringify(metrics)}`);
  const unnamed = await frame.locator("button:visible, input:visible, textarea:visible, select:visible").evaluateAll((nodes) => nodes.flatMap((node) => {
    const labelledBy = (node.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map((id) => node.ownerDocument.getElementById(id)?.innerText || "").join(" ");
    const label = node.getAttribute("aria-label") || node.getAttribute("title") || labelledBy ||
      (node.labels ? [...node.labels].map((item) => item.innerText).join(" ") : "") ||
      (node.tagName === "BUTTON" ? node.innerText : "");
    return label.trim() ? [] : [node.outerHTML.slice(0, 180)];
  }));
  assert.deepEqual(unnamed, [], `unnamed visible controls at ${width}px ${theme}`);
  check(`no-overflow-${width}-${theme}`, `document scrollWidth ${metrics.scroll}, clientWidth ${metrics.client}`);
  check(`accessible-controls-${width}-${theme}`, "visible buttons and fields have accessible names");
  await page.waitForTimeout(200);
  const runtimeErrors = await page.evaluate(() => structuredClone(window.__runtimeErrors || []));
  assert.deepEqual(runtimeErrors, [], `runtime errors at ${width}px ${theme}`);
  check(`runtime-clean-${width}-${theme}`, "production frame reported no runtime errors");
  const screenshot = path.join(path.dirname(outputPath), "screenshots", `${testCase.id}-${width}-${theme}.png`);
  await fs.mkdir(path.dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot });
  evidence.screenshots.push({ viewport_css_px: { width, height: 680 }, theme, phase: "initial", path: screenshot });
  const visible = await bodyText();
  assert.ok(!/\bat\s+\S+\.(?:js|mjs|ts):\d+/.test(visible) && !/\b(?:TypeError|ReferenceError):/.test(visible), "raw provider or code stack leaked into widget UI");
  return visible;
}

async function verifyForecast() {
  await startFrame("light", 640);
  await waitBody(/Harbor Station|Loading|forecast/i);
  await waitBody(/Harbor Station/);
  await waitBody(/18/);
  await waitBody(/Cloudy/);
  check("current-conditions", "fixture location, temperature, and condition rendered");
  const requests = await recordRpc();
  assert.ok(requests.some(({ method, params }) => method === "net.request" && params.source_id === testCase.fixture.source_id && params.request.path === testCase.fixture.path && params.request.method === "GET"));
  assert.ok(requests.every(({ method }) => method === "net.request"), "unexpected capability was used by forecast dashboard");
  check("fixture-request", "approved source, path, and GET observed");
  const chart = await dataPlot();
  const initialGeometry = await chartGeometry(chart);
  assert.ok(initialGeometry.bounds.width > 80 && initialGeometry.bounds.height > 35, "chart has no visible plotting area");
  const hasSvgGeometry = initialGeometry.svgPaths.some((value) => value.length > 24) || initialGeometry.svgMarks.length >= 7;
  assert.ok(hasSvgGeometry || initialGeometry.cssMarks.length >= 7, "chart has no meaningful SVG or CSS data geometry");
  const highLabels = testCase.fixture.responses[0].body.days.map((day) => `${day.high}°`);
  assert.ok(highLabels.filter((value) => initialGeometry.text.includes(value)).length >= 3, "chart does not show representative fixture temperature values");
  check("svg-data-chart", `visible ${hasSvgGeometry ? "SVG" : "CSS"} plot with ${hasSvgGeometry ? initialGeometry.svgMarks.length : initialGeometry.cssMarks.length} geometric marks and fixture temperature labels`);
  const changedResponses = structuredClone(testCase.fixture.responses);
  changedResponses[0].body.days[5].high = 39;
  pendingFixtureChanges = { responses: changedResponses };
  await startFrame("light", 640);
  await waitBody(/Harbor Station/);
  const changedChart = await dataPlot();
  const changedGeometry = await chartGeometry(changedChart);
  assert.notDeepEqual({ svgPaths: changedGeometry.svgPaths, svgMarks: changedGeometry.svgMarks, cssMarks: changedGeometry.cssMarks }, { svgPaths: initialGeometry.svgPaths, svgMarks: initialGeometry.svgMarks, cssMarks: initialGeometry.cssMarks }, "changing fixture temperature did not change plotted chart geometry");
  assert.ok(changedGeometry.text.includes("39°"), "changed fixture temperature was not reflected in chart labels");
  check("svg-chart-uses-data", "a fixture temperature change changes plotted geometry and its visible value");
  await startFrame("light", 640);
  await waitBody(/Harbor Station/);
  const disclosures = frame.locator('button[aria-expanded="false"]:visible, details:not([open]) > summary:visible');
  const details = await disclosures.count() ? disclosures.first() : await namedButton(/more|week|detail|day|forecast/i);
  await details.waitFor({ state: "visible", timeout: 5000 });
  const detailCard = frame.getByRole("heading", { name: /7.day forecast/i }).locator("xpath=..");
  await detailCard.waitFor({ state: "visible", timeout: 5000 });
  const detailRowCount = () => detailCard.evaluate((card) => [...card.querySelectorAll("div")].filter((element) => {
    const style = getComputedStyle(element);
    return style.borderBottomWidth !== "0px" && style.borderBottomStyle !== "none" && style.paddingTop === "8px" && style.paddingBottom === "8px";
  }).length);
  const initialRowCount = await detailRowCount();
  assert.ok(initialRowCount < 7, `forecast summary shows ${initialRowCount} of seven daily detail rows initially`);
  check("compact-initial-summary", `only ${initialRowCount} daily detail rows are initially visible; remaining days are collapsed`);
  await details.focus();
  await details.press("Enter");
  const expanded = await bodyText();
  for (const day of testCase.fixture.responses[0].body.days) {
    for (const value of [day.date, String(day.high), String(day.low), `${day.precipitation}%`]) assert.ok(expanded.includes(value), `expanded forecast missed fixture detail '${value}'`);
  }
  assert.equal(await detailRowCount(), 7, "expanding forecast did not reveal exactly seven day rows");
  assert.match(expanded, /Tide sensor|Delayed/);
  check("keyboard-primary-action", "forecast disclosure opens with Enter from keyboard focus");
  check("expand-seven-day-details", "all seven dates, highs, lows, precipitation values and service details revealed");
  const expandedScreenshot = path.join(path.dirname(outputPath), "screenshots", `${testCase.id}-640-light-expanded.png`);
  await fs.mkdir(path.dirname(expandedScreenshot), { recursive: true });
  await page.screenshot({ path: expandedScreenshot });
  evidence.screenshots.push({ viewport_css_px: { width: 640, height: 680 }, theme: "light", phase: "expanded", path: expandedScreenshot });

  pendingFixtureChanges = { nextNetworkResponses: [{ status: 503, body: { error: "TypeError: upstream provider failed\n    at vendor-provider.js:314:9" } }, structuredClone(testCase.fixture.responses[0])] };
  await startFrame("light", 640);
  await waitBody(/error|unavailable|failed/i);
  const failureText = await bodyText();
  assert.ok(!/\bat\s+\S+\.(?:js|mjs|ts):\d+/.test(failureText) && !/\b(?:TypeError|ReferenceError):|vendor-provider/.test(failureText));
  await waitForRpc((entries) => entries.some((entry) => entry.method === "net.request"), "initial failed fixture request");
  const callsBeforeRetry = (await recordRpc()).filter((entry) => entry.method === "net.request").length;
  await (await namedButton(/retry|try again|refresh/i)).click();
  await waitForRpc((entries) => entries.filter((entry) => entry.method === "net.request").length > callsBeforeRetry, "retry fixture request");
  await waitBody(/Harbor Station/);
  await waitBody(/Cloudy/);
  check("retry-error", "concise request failure recovers through a fresh approved request");
  pendingFixtureChanges = { nextNetworkResponses: [{ status: 200, body: { location: "Harbor Station", current: { temperature: 18, condition: "Cloudy" }, days: null, services: [] } }] };
  await startFrame("light", 640);
  const malformed = await waitBody(/error|invalid|malformed|unexpected|unable|failed|could not be read/i);
  assert.ok(!/18°|Cloudy|\bMON\b|\bTUE\b/i.test(malformed), "malformed forecast was presented as successful weather details");
  check("malformed-data-error", "malformed fixture data rejected with a concise visible error");
}

async function waitForSaved(predicate, description, timeout = 6000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      const raw = await page.evaluate(() => window.__fixture.files["follow-ups.json"] ?? null);
      if (raw !== null && predicate(JSON.parse(raw))) return JSON.parse(raw);
    } catch { /* wait for write */ }
    await new Promise((resolve) => setTimeout(resolve, 70));
  }
  throw new Error(`Timed out waiting for ${description}`);
}
async function inputs() { return frame.locator('input[type="text"], input:not([type]), textarea').all(); }
async function fillNamedInput(pattern, value) { await frame.getByRole("textbox", { name: pattern }).first().fill(value); }
async function rowFor(text) { return frame.getByText(text, { exact: false }).first().locator("xpath=ancestor::*[.//button or .//input][1]"); }

async function verifyCrud() {
  await startFrame("light", 640);
  await waitBody(/empty|no follow|nothing yet|add follow/i);
  check("compact-empty-state", "first frame communicates that the private follow-up list is empty");
  await fillNamedInput(/follow|title|task/i, "Inspect berth radar");
  const addButton = await namedButton(/add|create|save/i);
  await addButton.focus();
  await addButton.press("Enter");
  await waitBody(/Inspect berth radar/);
  let saved = await waitForSaved((items) => items.some((item) => item.title === "Inspect berth radar"), "created follow-up");
  assert.equal(saved[0].done, false);
  check("create-follow-up", "successful private write appears in the rendered list");
  check("keyboard-primary-action", "follow-up creation works by pressing Enter on the focused action");
  const populatedScreenshot = path.join(path.dirname(outputPath), "screenshots", `${testCase.id}-640-light-populated.png`);
  await fs.mkdir(path.dirname(populatedScreenshot), { recursive: true });
  await page.screenshot({ path: populatedScreenshot });
  evidence.screenshots.push({ viewport_css_px: { width: 640, height: 680 }, theme: "light", phase: "populated", path: populatedScreenshot });
  const row = await rowFor("Inspect berth radar");
  await row.getByRole("button", { name: /edit|rename/i }).first().click();
  await fillNamedInput(/rename/i, "Inspect radar calibration");
  await (await namedButton(/save|update|done/i)).click();
  await waitBody(/Inspect radar calibration/);
  saved = await waitForSaved((items) => items.some((item) => item.title === "Inspect radar calibration"), "renamed follow-up");
  check("rename-follow-up", "updated private record appears in the rendered list");
  const checkbox = (await rowFor("Inspect radar calibration")).getByRole("checkbox").first();
  if (await checkbox.count()) await checkbox.check();
  else await (await rowFor("Inspect radar calibration")).getByRole("button", { name: /mark done|complete/i }).click();
  saved = await waitForSaved((items) => items.some((item) => item.done === true), "completed follow-up");
  assert.equal(saved[0].done, true);
  check("toggle-done", "done state persisted privately");
  await (await rowFor("Inspect radar calibration")).getByRole("button", { name: /delete|remove/i }).click();
  await waitBody(/empty|no follow[-\s]|nothing yet|add follow/i);
  saved = await waitForSaved((items) => items.length === 0, "deleted follow-up");
  check("delete-follow-up", "deletion persisted and removed the rendered row");
  const fileCalls = (await recordRpc()).filter(({ method }) => method.startsWith("files."));
  assert.ok(fileCalls.some(({ method, params }) => method === "files.read" && params.path === "follow-ups.json"));
  assert.ok(fileCalls.some(({ method, params }) => method === "files.write" && params.path === "follow-ups.json"));
  check("read-follow-up", "private file read and write observed");
  pendingFixtureChanges = { failFileReads: 1 };
  await startFrame("light", 640);
  await waitBody(/error|failed|unavailable|problem|could not load|could.{0,4}load|unable/i);
  const text = await bodyText();
  assert.ok(!/\bat\s+\S+\.(?:js|mjs|ts):\d+/.test(text) && !/\b(?:TypeError|ReferenceError):|vendor-provider/.test(text));
  await (await namedButton(/retry|try again|reload/i)).click();
  await waitBody(/empty|no follow[-\s]|nothing yet|add follow/i);
  check("storage-error", "file read failure is concise and retry recovers");

  pendingFixtureChanges = { failFileWrites: 1 };
  await startFrame("light", 640);
  await waitBody(/empty|no follow[-\s]|nothing yet|add follow/i);
  const savedBeforeFailure = await page.evaluate(() => window.__fixture.files["follow-ups.json"]);
  const pendingTitle = "Calibrate backup beacon";
  await fillNamedInput(/new follow-up|follow-up title/i, pendingTitle);
  const addAfterFailure = await namedButton(/add follow-up/i);
  await addAfterFailure.click();
  await waitBody(/could not save|unable to save|save failed|failed to save|could not persist|unable to persist/i);
  assert.equal(await page.evaluate(() => window.__fixture.files["follow-ups.json"]), savedBeforeFailure, "failed write was incorrectly reported as persisted");
  check("file-write-error", "failed private write leaves the file unchanged and shows an actionable error");
  const writeErrorScreenshot = path.join(path.dirname(outputPath), "screenshots", `${testCase.id}-640-light-write-error.png`);
  await fs.mkdir(path.dirname(writeErrorScreenshot), { recursive: true });
  await page.screenshot({ path: writeErrorScreenshot });
  evidence.screenshots.push({ viewport_css_px: { width: 640, height: 680 }, theme: "light", phase: "write-error", path: writeErrorScreenshot });
  await (await namedButton(/^retry(?: (?:save|saving|write))?$/i)).click();
  const retried = await waitForSaved((items) => items.some((item) => item.title === pendingTitle), "retried failed write", 6000);
  assert.ok(retried.some((item) => item.title === pendingTitle));
  await waitBody(new RegExp(pendingTitle));
  check("retry-file-write", "Retry persists the retained unsaved follow-up and leaves it visible");
}

let failure;
try {
  browser = await chromium.launch({ executablePath, headless: true });
  evidence.browser = { name: "Chromium", version: browser.version(), executable_path: executablePath, playwright_core_version: JSON.parse(await fs.readFile(require.resolve("playwright-core/package.json"), "utf8")).version };
  evidence.renderer_source_sha256 = rendererSourceHashes;
  page = await browser.newPage({ viewport: { width: 640, height: 680 }, colorScheme: "light" });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin === new URL(frameUrl).origin || url.protocol === "data:") await route.continue();
    else { evidence.blocked_network_attempts.push(url.href); await route.abort("blockedbyclient"); }
  });
  page.on("pageerror", (error) => evidence.page_errors.push(error.message));
  await page.setContent("<!doctype html><html><body></body></html>");
  for (const theme of ["light", "dark"]) {
    for (const width of [320, 640]) {
      await page.emulateMedia({ colorScheme: theme });
      await verifyFrameBasics(theme, width);
    }
  }
  if (testCase.id === "forecast-operations-dashboard") await verifyForecast();
  else if (testCase.id === "compact-follow-up-dashboard") await verifyCrud();
  else throw new Error(`No interaction routine for ${testCase.id}`);
  evidence.rpc = await recordRpc();
  assert.equal(evidence.page_errors.length, 0, `page errors: ${evidence.page_errors.join("; ")}`);
  assert.deepEqual(evidence.blocked_network_attempts, [], `unapproved browser network requests: ${evidence.blocked_network_attempts.join(", ")}`);
  check("network-isolated", "browser attempted no network access outside the local production frame");
} catch (error) {
  failure = error;
  evidence.error = error.stack || String(error);
  if (page) {
    try {
      evidence.rpc = await recordRpc();
      evidence.failure_text = await bodyText();
      const screenshot = path.join(path.dirname(outputPath), "screenshots", `${testCase.id}-failed.png`);
      await fs.mkdir(path.dirname(screenshot), { recursive: true });
      await page.screenshot({ path: screenshot });
      evidence.failure_screenshot = screenshot;
    } catch { /* preserve original error */ }
  }
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
evidence.passed = !failure;
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`);
process.stdout.write(`${JSON.stringify({ case_id: testCase.id, passed: evidence.passed, checks: evidence.checks.length, result: outputPath, screenshots: evidence.screenshots.length })}\n`);
if (failure) process.exitCode = 1;
