import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";

import Babel from "@babel/standalone";
import htm from "htm";

import { mountController } from "../controller_facade.mjs";

const controllerSource = fs.readFileSync(new URL("./fixtures/weather_app/controller.js.txt", import.meta.url), "utf8");
const manifest = JSON.parse(fs.readFileSync(new URL("./fixtures/weather_app/manifest.json", import.meta.url), "utf8"));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const place = (name, latitude = 31.23, longitude = 121.47) => ({
  id: name, name, latitude, longitude, country: "中国",
});
const forecast = (temperature = 21) => ({
  current: { temperature_2m: temperature, apparent_temperature: 20, relative_humidity_2m: 60, wind_speed_10m: 12, weather_code: 0, time: "2026-10-03T08:00" },
  current_units: { temperature_2m: "°C", apparent_temperature: "°C", wind_speed_10m: "km/h" },
});

// A stateful hook host executes the actual Babel-transformed controller and
// real SDK primitives. Tests exercise returned DOM handlers, not source text.
function controllerHarness(rpc) {
  const slots = [];
  let cursor = 0;
  let mounted;
  let tree;
  let queued = false;
  const h = (type, props, ...children) => ({ type, props: { ...(props || {}), children } });
  const expand = (node) => {
    if (Array.isArray(node)) return node.map(expand);
    if (node === null || node === undefined || typeof node !== "object") return node;
    if (typeof node.type === "function") return expand(node.type(node.props));
    return { type: node.type, props: { ...node.props, children: expand(node.props.children) } };
  };
  const paint = () => { cursor = 0; tree = expand(mounted); };
  const schedule = () => {
    if (queued || !mounted) return;
    queued = true;
    queueMicrotask(() => { queued = false; if (mounted) paint(); });
  };
  const runtime = {
    Component: class {}, Fragment: Symbol("Fragment"), createContext: () => ({}), h, html: htm.bind(h),
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === "function" ? initial() : initial };
      return [slots[index].value, (next) => {
        slots[index].value = typeof next === "function" ? next(slots[index].value) : next;
        schedule();
      }];
    },
    useRef(value) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { current: value };
      return slots[index];
    },
    useEffect(effect) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { cleanup: effect() };
    },
    useCallback: (callback) => callback, useMemo: (factory) => factory(), useContext: () => undefined,
    useReducer: () => [undefined, () => undefined],
    render(value) {
      mounted = value;
      if (value) paint();
      else { for (const slot of slots) slot?.cleanup?.(); tree = null; }
    },
  };
  const calls = [];
  const mountedController = mountController({
    babel: Babel, runtime, controllerSource,
    root: { ownerDocument: { documentElement: { dataset: {}, style: {} } } },
    capabilityIds: manifest.capabilities.map((grant) => grant.id),
    transport: {
      rpc: (method, params) => { calls.push({ method, params }); return rpc(method, params); },
      storageRequest: async () => undefined, hostEvent: () => true,
    },
  });
  const nodes = () => {
    const found = [];
    const visit = (node) => {
      if (Array.isArray(node)) node.forEach(visit);
      else if (node && typeof node === "object") { found.push(node); visit(node.props.children); }
    };
    visit(tree);
    return found;
  };
  const text = (value = tree) => {
    if (Array.isArray(value)) return value.map((item) => text(item)).join(" ");
    if (value && typeof value === "object") return text(value.props.children);
    return value === null || value === undefined ? "" : String(value);
  };
  const button = (label) => {
    const node = nodes().find((value) => value.type === "button" && text(value) === label);
    assert.ok(node, `Missing button ${label}: ${text()}`);
    return node;
  };
  const click = (label) => {
    const node = button(label);
    assert.equal(Boolean(node.props.disabled), false, `Disabled button ${label}`);
    return node.props.onClick();
  };
  const input = (value) => nodes().find((node) => node.type === "input").props.onInput({ currentTarget: { value } });
  const choose = (name) => {
    const node = nodes().find((value) => value.type === "div" && typeof value.props.onClick === "function" && text(value).includes(name));
    assert.ok(node, `Missing result ${name}: ${text()}`);
    return node.props.onClick();
  };
  const flush = async () => { for (let index = 0; index < 8; index++) await Promise.resolve(); };
  return { calls, text, button, click, input, choose, flush, dispose: mountedController.dispose };
}

test("weather starts without collecting location or making network requests", (context) => {
  const app = controllerHarness(async () => { throw new Error("Unexpected initial request"); });
  context.after(app.dispose);
  assert.equal(app.calls.length, 0);
  assert.match(app.text(), /选择地点或点击当前位置/);
  assert.equal(app.button("重试天气").props.disabled, true);
});

test("real input and search-result handlers request the selected city's weather", async (context) => {
  const app = controllerHarness(async (_method, params) => params.source_id === "geocoding"
    ? { results: [place("上海")] } : forecast());
  context.after(app.dispose);
  app.input(" 上海 "); await app.flush();
  await app.click("搜索"); await app.flush();
  assert.match(app.text(), /请选择一个地点/);
  await app.choose("上海"); await app.flush();
  assert.match(app.text(), /晴朗 · 气温 21 °C/);
  assert.match(app.text(), /湿度 60%/);
  assert.match(app.text(), /数据来源：Open-Meteo/);
  assert.equal(app.calls[0].params.request.query.name, "上海");
  assert.equal(app.calls[0].params.request.path, "/v1/search");
  assert.equal(app.calls[1].params.source_id, "open-meteo");
  assert.equal(app.calls[1].params.request.method, "GET");
  assert.equal(app.calls[1].params.request.query.latitude, 31.23);
  assert.equal(app.calls.filter((call) => call.method === "location.getCurrentPosition").length, 0);
});

test("clicking current position uses one location RPC then exact forecast coordinates", async (context) => {
  const app = controllerHarness(async (method) => method === "location.getCurrentPosition"
    ? { latitude: 30, longitude: 120, accuracy: 50, timestamp: 1 } : forecast(18));
  context.after(app.dispose);
  await app.click("当前位置"); await app.flush();
  assert.match(app.text(), /已获得当前位置/);
  assert.match(app.text(), /气温 18 °C/);
  assert.equal(app.calls[0].method, "location.getCurrentPosition");
  assert.equal(app.calls[1].params.request.query.longitude, 120);
});

test("denied location keeps real place search available without invented weather", async (context) => {
  const app = controllerHarness(async (method, params) => {
    if (method === "location.getCurrentPosition") throw Object.assign(new Error("Denied"), { code: "device_location_denied" });
    return params.source_id === "geocoding" ? { results: [place("北京", 40, 116)] } : forecast(10);
  });
  context.after(app.dispose);
  await app.click("当前位置"); await app.flush();
  assert.match(app.text(), /定位权限被拒绝/);
  assert.equal(app.calls.length, 1);
  assert.doesNotMatch(app.text(), /气温/);
  app.input("北京"); await app.flush(); await app.click("搜索"); await app.flush();
  await app.choose("北京"); await app.flush();
  assert.match(app.text(), /气温 10 °C/);
});

test("empty query makes no request and empty results have an actionable message", async (context) => {
  const app = controllerHarness(async () => ({}));
  context.after(app.dispose);
  await app.click("搜索"); await app.flush();
  assert.match(app.text(), /请先输入/);
  assert.equal(app.calls.length, 0);
  app.input("不存在的地名"); await app.flush(); await app.click("搜索"); await app.flush();
  assert.match(app.text(), /没有找到地点/);
  assert.equal(app.calls.length, 1);
});

test("search service failure keeps the query and permits an actual retry", async (context) => {
  let searches = 0;
  const app = controllerHarness(async () => {
    if (++searches === 1) throw new Error("Search unavailable");
    return { results: [place("上海")] };
  });
  context.after(app.dispose);
  app.input("上海"); await app.flush(); await app.click("搜索"); await app.flush();
  assert.match(app.text(), /地点搜索失败/);
  await app.click("搜索"); await app.flush();
  assert.match(app.text(), /上海 · 中国/);
  assert.deepEqual(app.calls[0].params, app.calls[1].params);
});

test("invalid coordinates in search results cannot become a forecast request", async (context) => {
  const app = controllerHarness(async () => ({ results: [place("错误", 190, 120), place("字符串", "30", 120)] }));
  context.after(app.dispose);
  app.input("地点"); await app.flush(); await app.click("搜索"); await app.flush();
  assert.match(app.text(), /没有找到地点/);
  assert.equal(app.calls.length, 1);
  assert.doesNotMatch(app.text(), /错误 ·|字符串 ·/);
});

test("a device timeout is distinct from denial and can be retried by another click", async (context) => {
  let locations = 0;
  const app = controllerHarness(async (method) => {
    if (method !== "location.getCurrentPosition") return forecast(19);
    if (++locations === 1) throw Object.assign(new Error("Timed out"), { code: "device_location_timeout" });
    return { latitude: 30, longitude: 120 };
  });
  context.after(app.dispose);
  await app.click("当前位置"); await app.flush();
  assert.match(app.text(), /定位超时/);
  assert.equal(app.calls.length, 1);
  await app.click("当前位置"); await app.flush();
  assert.match(app.text(), /气温 19 °C/);
  assert.equal(locations, 2);
});

test("weather failure can retry the exact same selected location", async (context) => {
  let forecasts = 0;
  const app = controllerHarness(async (_method, params) => {
    if (params.source_id === "geocoding") return { results: [place("上海")] };
    if (++forecasts === 1) throw new Error("Weather unavailable");
    return forecast(22);
  });
  context.after(app.dispose);
  app.input("上海"); await app.flush(); await app.click("搜索"); await app.flush();
  await app.choose("上海"); await app.flush();
  assert.match(app.text(), /获取天气失败/);
  await app.click("重试天气"); await app.flush();
  assert.match(app.text(), /气温 22 °C/);
  assert.deepEqual(app.calls[1].params, app.calls[2].params);
});

test("invalid weather response is a retriable failure rather than fake values", async (context) => {
  const app = controllerHarness(async (method) => method === "location.getCurrentPosition"
    ? { latitude: 30, longitude: 120 } : { current: { temperature_2m: null, time: "2026-10-03" } });
  context.after(app.dispose);
  await app.click("当前位置"); await app.flush();
  assert.match(app.text(), /获取天气失败/);
  assert.doesNotMatch(app.text(), /气温 null|0 °C/);
});

test("late search results cannot overwrite a newer query and completed search", async (context) => {
  const old = deferred();
  const app = controllerHarness(async (_method, params) => params.request.query.name === "上海"
    ? old.promise : { results: [place("北京", 40, 116)] });
  context.after(app.dispose);
  app.input("上海"); await app.flush(); const first = app.click("搜索"); await app.flush();
  app.input("北京"); await app.flush(); await app.click("搜索"); await app.flush();
  old.resolve({ results: [place("上海")] }); await first; await app.flush();
  assert.match(app.text(), /北京/);
  assert.doesNotMatch(app.text(), /上海 ·/);
});

test("late weather for a previous result cannot overwrite the newer selected result", async (context) => {
  const old = deferred();
  const app = controllerHarness(async (_method, params) => {
    if (params.source_id === "geocoding") return { results: [place("上海"), place("北京", 40, 116)] };
    return params.request.query.latitude === 31.23 ? old.promise : forecast(8);
  });
  context.after(app.dispose);
  app.input("城市"); await app.flush(); await app.click("搜索"); await app.flush();
  const first = app.choose("上海"); await app.flush();
  await app.choose("北京"); await app.flush();
  old.resolve(forecast(30)); await first; await app.flush();
  assert.match(app.text(), /所选地点：北京/);
  assert.match(app.text(), /气温 8 °C/);
  assert.doesNotMatch(app.text(), /气温 30 °C/);
});

test("late device location cannot replace an explicitly selected search result", async (context) => {
  const old = deferred();
  const app = controllerHarness(async (method, params) => {
    if (method === "location.getCurrentPosition") return old.promise;
    return params.source_id === "geocoding" ? { results: [place("北京", 40, 116)] } : forecast(7);
  });
  context.after(app.dispose);
  const first = app.click("当前位置"); await app.flush();
  app.input("北京"); await app.flush(); await app.click("搜索"); await app.flush();
  await app.choose("北京"); await app.flush();
  old.resolve({ latitude: 31, longitude: 121 }); await first; await app.flush();
  assert.match(app.text(), /所选地点：北京/);
  assert.equal(app.calls.filter((call) => call.params.source_id === "open-meteo").length, 1);
});
