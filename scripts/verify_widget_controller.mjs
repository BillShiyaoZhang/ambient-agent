import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Babel = require("../frontend/node_modules/@babel/standalone");
const controllerPath = process.argv[2];

if (!controllerPath) {
  throw new Error("controller path is required");
}

const source = fs.readFileSync(controllerPath, "utf8");
const manifestPath = path.join(path.dirname(controllerPath), "manifest.json");
let declaredDataSources = new Set();
if (fs.existsSync(manifestPath)) {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  declaredDataSources = new Set(Object.keys(manifest.data_sources || {}));
}
const forbiddenGlobals = new Set([
  "window",
  "document",
  "globalThis",
  "self",
  "parent",
  "top",
  "opener",
  "localStorage",
  "sessionStorage",
  "indexedDB",
  "fetch",
  "XMLHttpRequest",
  "WebSocket",
  "EventSource",
  "Worker",
  "SharedWorker",
  "navigator",
  "eval",
  "Function",
  "require",
  "process",
]);

const securityPlugin = ({ types: t }) => ({
  visitor: {
    ImportDeclaration(path) {
      throw path.buildCodeFrameError("Static imports are not allowed in Widget controllers");
    },
    CallExpression(path) {
      if (path.node.callee?.type === "Import") {
        throw path.buildCodeFrameError("Dynamic imports are not allowed in Widget controllers");
      }
      const callee = path.node.callee;
      const isAmbientNetRequest =
        t.isMemberExpression(callee) &&
        !callee.computed &&
        t.isIdentifier(callee.property, { name: "request" }) &&
        t.isMemberExpression(callee.object) &&
        !callee.object.computed &&
        t.isIdentifier(callee.object.object, { name: "ambient" }) &&
        t.isIdentifier(callee.object.property, { name: "net" });
      if (isAmbientNetRequest) {
        const sourceId = path.node.arguments[0];
        if (!t.isStringLiteral(sourceId)) {
          throw path.buildCodeFrameError(
            "ambient.net.request source id must be a string literal declared in manifest.json data_sources"
          );
        }
        if (!declaredDataSources.has(sourceId.value)) {
          throw path.buildCodeFrameError(
            `ambient.net.request source '${sourceId.value}' is not declared in manifest.json data_sources`
          );
        }
      }
    },
    ReferencedIdentifier(path) {
      const name = path.node.name;
      if (forbiddenGlobals.has(name) && !path.scope.hasBinding(name)) {
        throw path.buildCodeFrameError(`Forbidden host or network global: ${name}`);
      }
    },
    NewExpression(path) {
      if (t.isIdentifier(path.node.callee) && forbiddenGlobals.has(path.node.callee.name)) {
        throw path.buildCodeFrameError(`Forbidden constructor: ${path.node.callee.name}`);
      }
    },
  },
});

const makeHostProxy = () => new Proxy(function hostCapability() {}, {
  get(_target, property) {
    if (property === "then") return undefined;
    return makeHostProxy();
  },
  apply() {
    return makeHostProxy();
  },
  construct() {
    return makeHostProxy();
  },
});

try {
  const transformed = Babel.transform(source, {
    filename: "controller.js",
    sourceType: "module",
    presets: ["react"],
    plugins: [securityPlugin, "transform-modules-commonjs"],
    babelrc: false,
    configFile: false,
  }).code;
  const exportsObject = {};
  const noop = () => 0;
  const sandbox = {
    exports: exportsObject,
    module: { exports: exportsObject },
    React: makeHostProxy(),
    ambient: makeHostProxy(),
    console: { log: noop, warn: noop, error: noop },
    setTimeout: noop,
    clearTimeout: noop,
    setInterval: noop,
    clearInterval: noop,
  };

  vm.runInNewContext(transformed, sandbox, {
    timeout: 1000,
    contextCodeGeneration: { strings: false, wasm: false },
  });

  const component = exportsObject.default ?? sandbox.module.exports.default;
  if (typeof component !== "function") {
    throw new Error("Widget controller default export must be a component function");
  }
  process.stdout.write(JSON.stringify({ ok: true }));
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  let code = "widget_verification_failed";
  let hint = "Fix controller.js according to the Widget Runtime Contract, then rerun validation.";
  if (message.includes("ambient.net.request")) {
    code = "data_source_contract_error";
    hint = "Declare the literal source id in manifest.json data_sources and use an exact allowed path.";
  } else if (message.includes("Forbidden host or network global")) {
    code = "forbidden_runtime_api";
    hint = "Use ambient.net.request for declared public JSON data, ambient.graph for context, or an explicitly provided MCP/Capability.";
  } else if (message.includes("Static imports") || message.includes("Dynamic imports")) {
    code = "unsupported_import";
    hint = "Widget controllers are single-file modules; use only the injected ambient SDK.";
  }
  process.stderr.write(JSON.stringify({ ok: false, code, message, hint }));
  process.exitCode = 1;
}
