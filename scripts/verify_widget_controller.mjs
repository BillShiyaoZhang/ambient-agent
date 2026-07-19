import fs from "node:fs";
import vm from "node:vm";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Babel = require("../frontend/node_modules/@babel/standalone");
const controllerPath = process.argv[2];

if (!controllerPath) {
  throw new Error("controller path is required");
}

const source = fs.readFileSync(controllerPath, "utf8");
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

const transformed = Babel.transform(source, {
  filename: "controller.js",
  sourceType: "module",
  presets: ["react"],
  plugins: [securityPlugin, "transform-modules-commonjs"],
  babelrc: false,
  configFile: false,
}).code;

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
