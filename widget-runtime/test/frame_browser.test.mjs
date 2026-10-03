import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import { chromium } from "playwright-core";

import { createFrameServer } from "../frame_server.mjs";


const CHROMIUM_CANDIDATES = [
  process.env.CHROMIUM_EXECUTABLE_PATH,
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const CHROMIUM_PATH = CHROMIUM_CANDIDATES.find((candidate) =>
  fs.existsSync(candidate),
);


test(
  "renders a controller natively while CSP blocks its direct network access",
  {
    skip: CHROMIUM_PATH ? false : "Chromium is not installed",
    timeout: 30_000,
  },
  async (context) => {
    const server = createFrameServer();
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    let browser;
    context.after(async () => {
      try {
        await browser?.close();
      } finally {
        await new Promise((resolve) => server.close(resolve));
      }
    });
    const address = server.address();
    const frameUrl = `http://127.0.0.1:${address.port}/frame.html`;

    browser = await chromium.launch({
      executablePath: CHROMIUM_PATH,
      headless: true,
    });
    const page = await browser.newPage();
    const pageErrors = [];
    const diagnostics = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) =>
      diagnostics.push(`console:${message.type()}:${message.text()}`),
    );
    page.on("requestfailed", (request) =>
      diagnostics.push(
        `requestfailed:${request.url()}:${request.failure()?.errorText}`,
      ),
    );
    await page.setContent("<!doctype html><body></body>");

    let result;
    try {
      result = await page.evaluate(async (url) => {
        const iframe = document.createElement("iframe");
        iframe.style.width = "600px";
        iframe.style.height = "480px";
        iframe.src = url;
        document.body.append(iframe);
        window.__widgetFocused = false;
        await new Promise((resolve, reject) => {
          iframe.addEventListener("load", resolve, { once: true });
          iframe.addEventListener("error", reject, { once: true });
        });

        return new Promise((resolve, reject) => {
          const channel = new MessageChannel();
          const hostEvents = [];
          window.__widgetHostEvents = hostEvents;
          let runtimeReady = false;
          const timer = setTimeout(
            () => reject(
              new Error(
                "Timed out waiting for the Widget frame: "
                  + JSON.stringify(hostEvents)
              )
            ),
            10_000,
          );
          channel.port1.onmessage = (event) => {
            const message = event.data;
            if (
              message.type === "port_ready"
              && message.nonce === "browser-test-nonce"
            ) {
              channel.port1.postMessage({
                type: "init",
                protocol_version: 1,
                nonce: "browser-test-nonce",
                controller_source: `
                  export default function Controller({ ambient }) {
                    const { Button } = ambient.components;
                    ambient.react.useEffect(() => {
                      try {
                        ambient.sendMessage("graphics-mounted");
                      } catch (error) {
                        ambient.sendMessage("graphics-error:" + error.message);
                      }
                      fetch("https://example.invalid/controller-egress")
                        .then(() => ambient.sendMessage("external-network-allowed"))
                        .catch(() => ambient.sendMessage("network-blocked"));
                      ambient.storage.get("draft")
                        .then((value) => ambient.sendMessage(
                          "storage:" + JSON.stringify(value)
                        ));
                    }, []);
                    const points = [{ day: "Mon", value: 3 }, { day: "Tue", value: 5 }];
                    const polyline = points.map((point, index) => index * 50 + "," + (60 - point.value * 10)).join(" ");
                    return <section aria-label="Activity dashboard" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
                      <div style={{ borderRadius: "13px", backgroundColor: "#d03030", minHeight: 100 }}>
                        <h2>Native controller</h2>
                        <svg viewBox="0 0 100 70" role="img" aria-label="Activity trend">
                          <title>Activity trend</title>
                          <rect x="0" y="0" width="100" height="70" fill="#23b35a" />
                          <path d="M0 60 L50 10" stroke="#111111" />
                          <polyline points={polyline} fill="none" stroke="#111111" strokeWidth="3" />
                        </svg>
                        <Button onClick={() => ambient.sendMessage("button-click")}>
                          <span aria-hidden="true">☀</span> View all seven days
                        </Button>
                      </div>
                      <div style={{ backgroundColor: "#245bd0", minHeight: 100 }} aria-label="Daily activity details">
                        {points.map((point) => <p key={point.day}>{point.day}: {point.value}</p>)}
                      </div>
                    </section>;
                  }
                `,
                capability_ids: [],
                presentation_context: {
                  theme: { preference: "system", effective: "dark" },
                  locale: "en-US",
                  reduced_motion: false,
                },
              });
              return;
            }
            if (message.type === "storage_request") {
              channel.port1.postMessage({
                type: "storage_response",
                request_id: message.request_id,
                result: null,
              });
              return;
            }
            if (message.type === "host_event") {
              if (message.event === "focus") {
                window.__widgetFocused = true;
              } else {
                hostEvents.push(message.text);
              }
            } else if (message.type === "ready") {
              runtimeReady = true;
            } else if (message.type === "runtime_error") {
              clearTimeout(timer);
              reject(new Error(JSON.stringify(message.error)));
              return;
            }
            if (
              runtimeReady
              && hostEvents.includes("network-blocked")
              && hostEvents.includes("storage:null")
              && hostEvents.includes("graphics-mounted")
            ) {
              clearTimeout(timer);
              resolve({ hostEvents, runtimeReady });
            }
          };
          channel.port1.start();
          iframe.contentWindow.postMessage(
            {
              type: "ambient-widget-port",
              protocol_version: 1,
              nonce: "browser-test-nonce",
            },
            "*",
            [channel.port2],
          );
        });
      }, frameUrl);
    } catch (error) {
      throw new Error(`${error.message}\n${diagnostics.join("\n")}`);
    }

    assert.equal(result.runtimeReady, true);
    assert.ok(result.hostEvents.includes("network-blocked"));
    assert.ok(result.hostEvents.includes("storage:null"));
    assert.ok(result.hostEvents.includes("graphics-mounted"));
    assert.equal(result.hostEvents.includes("external-network-allowed"), false);
    const widgetFrame = page.frames().find((frame) => frame.url() === frameUrl);
    assert.ok(widgetFrame, "expected the isolated Widget frame");
    const chart = widgetFrame.getByRole("img", { name: "Activity trend" });
    assert.equal(await chart.getAttribute("viewBox"), "0 0 100 70");
    assert.equal(await chart.locator("rect").count(), 1);
    assert.equal(await chart.locator("path").count(), 1);
    assert.equal(await chart.locator("polyline").count(), 1);
    const button = widgetFrame.getByRole("button", { name: "View all seven days" });
    await button.waitFor({ timeout: 3_000 });
    await button.focus();
    await button.press("Enter");
    await page.waitForFunction(() => window.__widgetHostEvents.includes("button-click"));
    const styledCard = widgetFrame.getByText("Native controller").locator("..");
    assert.equal(await styledCard.evaluate((element) => getComputedStyle(element).borderRadius), "13px");
    const iframe = page.locator("iframe");
    await iframe.evaluate((element) => {
      element.style.width = "600px";
      element.style.height = "480px";
    });
    const section = widgetFrame.getByRole("region", { name: "Activity dashboard" });
    const wideColumns = await section.evaluate((element) => getComputedStyle(element).gridTemplateColumns);
    assert.equal(wideColumns.trim().split(/\s+/).filter((track) => parseFloat(track) > 100).length, 2);
    await iframe.evaluate((element) => { element.style.width = "320px"; });
    await widgetFrame.waitForFunction(() =>
      getComputedStyle(document.querySelector("section")).gridTemplateColumns.trim().split(/\s+/).filter((track) => parseFloat(track) > 100).length === 1,
      undefined,
      { timeout: 3_000 },
    );
    const narrowColumns = await section.evaluate((element) => getComputedStyle(element).gridTemplateColumns);
    assert.equal(narrowColumns.trim().split(/\s+/).filter((track) => parseFloat(track) > 100).length, 1);
    await widgetFrame.getByText("Native controller").click();
    await page.waitForFunction(() => window.__widgetFocused === true);
    await page.evaluate(() => {
      window.__widgetFocused = false;
    });
    await page.keyboard.press("A");
    await page.waitForFunction(() => window.__widgetFocused === true);
    assert.deepEqual(pageErrors, []);
  },
);
