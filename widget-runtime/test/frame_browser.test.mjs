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
  { skip: CHROMIUM_PATH ? false : "Chromium is not installed" },
  async (context) => {
    const server = createFrameServer();
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    context.after(
      () => new Promise((resolve) => server.close(resolve)),
    );
    const address = server.address();
    const frameUrl = `http://127.0.0.1:${address.port}/frame.html`;

    const browser = await chromium.launch({
      executablePath: CHROMIUM_PATH,
      headless: true,
    });
    context.after(() => browser.close());
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
        iframe.src = url;
        document.body.append(iframe);
        await new Promise((resolve, reject) => {
          iframe.addEventListener("load", resolve, { once: true });
          iframe.addEventListener("error", reject, { once: true });
        });

        return new Promise((resolve, reject) => {
          const channel = new MessageChannel();
          const hostEvents = [];
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
                    const cardRef = ambient.react.useRef(null);
                    ambient.react.useEffect(() => {
                      try {
                        ambient.sendMessage(
                          "style:" + getComputedStyle(cardRef.current).borderRadius
                        );
                      } catch (error) {
                        ambient.sendMessage("style-error:" + error.message);
                      }
                      fetch("https://example.invalid/controller-egress")
                        .then(() => ambient.sendMessage("external-network-allowed"))
                        .catch(() => ambient.sendMessage("network-blocked"));
                      ambient.storage.get("draft")
                        .then((value) => ambient.sendMessage(
                          "storage:" + JSON.stringify(value)
                        ));
                    }, []);
                    return (
                      <div ref={cardRef} style={{ borderRadius: "13px" }}>
                        Native controller
                      </div>
                    );
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
              hostEvents.push(message.text);
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
              && hostEvents.includes("style:13px")
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
    assert.ok(result.hostEvents.includes("style:13px"));
    assert.equal(result.hostEvents.includes("external-network-allowed"), false);
    assert.deepEqual(pageErrors, []);
  },
);
