import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

test("native Frame CLI defaults to loopback and honors an explicit container host", async (t) => {
  for (const explicitHost of [undefined, "0.0.0.0"]) {
    const reservation = net.createServer();
    reservation.listen(0, "127.0.0.1");
    await once(reservation, "listening");
    const port = reservation.address().port;
    await new Promise((resolve) => reservation.close(resolve));
    const env = { ...process.env, WIDGET_FRAME_PORT: String(port) };
    delete env.WIDGET_FRAME_HOST;
    if (explicitHost) env.WIDGET_FRAME_HOST = explicitHost;
    const child = spawn(process.execPath, [fileURLToPath(new URL("../frame_server.mjs", import.meta.url))], {
      env, stdio: ["ignore", "pipe", "pipe"],
    });
    t.after(() => child.kill());
    const timeout = setTimeout(() => child.kill(), 20_000);
    try {
      const [output] = await once(child.stdout, "data");
      assert.match(String(output), new RegExp(`http://${explicitHost || "127.0.0.1"}:${port}`));
      assert.equal((await fetch(`http://127.0.0.1:${port}/health`)).status, 200);
    } finally {
      clearTimeout(timeout);
      child.kill();
      await once(child, "exit");
    }
  }
});
