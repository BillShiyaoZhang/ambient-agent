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
if (!fs.existsSync(manifestPath)) {
  process.stderr.write(JSON.stringify({
    ok: false,
    code: "capability_contract_error",
    message: "Widget staging requires manifest.json",
    hint: "Generate Manifest V2 from the approved Runtime Contract.",
  }));
  process.exit(1);
}
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
if (manifest.manifest_version !== 2 || !Array.isArray(manifest.capabilities)) {
  process.stderr.write(JSON.stringify({
    ok: false,
    code: "capability_contract_error",
    message: "Widget staging requires Manifest V2 with a capabilities array",
    hint: "Generate Manifest V2 from the approved Runtime Contract.",
  }));
  process.exit(1);
}
const grants = new Map(manifest.capabilities.map((grant) => [grant.id, grant.scope]));
const pathMatches = (pattern, candidate) => {
  if (typeof pattern !== "string" || typeof candidate !== "string") return false;
  if (pattern.endsWith("/**")) {
    const prefix = pattern.slice(0, -3).replace(/\/$/, "");
    return candidate === prefix || candidate.startsWith(`${prefix}/`);
  }
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, "[^/]*");
  return new RegExp(`^${escaped}$`).test(candidate);
};
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
      if (!t.isMemberExpression(callee) || callee.computed || !t.isIdentifier(callee.property)) return;
      const namespaceNode = callee.object;
      if (
        !t.isMemberExpression(namespaceNode) ||
        namespaceNode.computed ||
        !t.isIdentifier(namespaceNode.object, { name: "ambient" }) ||
        !t.isIdentifier(namespaceNode.property)
      ) return;

      const namespace = namespaceNode.property.name;
      const method = callee.property.name;
      const capabilityError = (message) => {
        throw path.buildCodeFrameError(`Capability contract: ${message}`);
      };
      const stringLiteral = (node, label) => {
        if (!t.isStringLiteral(node)) capabilityError(`${label} must be a string literal`);
        return node.value;
      };
      const objectProperty = (object, key) => object.properties.find(
        (item) => t.isObjectProperty(item) && !item.computed &&
          ((t.isIdentifier(item.key) && item.key.name === key) || (t.isStringLiteral(item.key) && item.key.value === key))
      );

      if (namespace === "mcp" || namespace === "runs") {
        capabilityError(`ambient.${namespace} is not part of the Widget SDK`);
      }

      if (namespace === "graph" && method === "subscribe") {
        const scope = grants.get("graph.query");
        if (!scope) capabilityError("ambient.graph.subscribe requires graph.query");
        const query = path.node.arguments[0];
        if (!t.isObjectExpression(query)) capabilityError("graph query must be an object literal");
        const property = objectProperty(query, "type");
        const entity = stringLiteral(property?.value, "graph query type");
        if (!scope.entities?.includes(entity)) capabilityError(`graph entity '${entity}' is not approved`);
        const includes = objectProperty(query, "include");
        if (includes) {
          if (!t.isArrayExpression(includes.value)) capabilityError("graph query include must be an array literal");
          for (const include of includes.value.elements) {
            if (!t.isObjectExpression(include)) capabilityError("graph query include entries must be object literals");
            const targetType = stringLiteral(objectProperty(include, "target_type")?.value, "graph include target_type");
            if (!scope.entities?.includes(targetType)) {
              capabilityError(`graph entity '${targetType}' is not approved`);
            }
          }
        }
      }

      if (namespace === "graph" && method === "mutate") {
        const scope = grants.get("graph.mutate");
        if (!scope) capabilityError("ambient.graph.mutate requires graph.mutate");
        const actions = path.node.arguments[0];
        if (!t.isArrayExpression(actions)) capabilityError("graph mutations must be an array literal");
        const actionOperations = {
          create_node: "create",
          update_node_property: "update",
          delete_node: "delete",
          create_edge: "create",
          delete_edge: "delete",
        };
        for (const action of actions.elements) {
          if (!t.isObjectExpression(action)) capabilityError("graph mutation entries must be object literals");
          const actionName = stringLiteral(objectProperty(action, "action")?.value, "graph mutation action");
          const operation = actionOperations[actionName];
          if (!operation || !scope.operations?.includes(operation)) {
            capabilityError(`graph operation '${operation || actionName}' is not approved`);
          }
          if (actionName === "create_node") {
            const entity = stringLiteral(objectProperty(action, "type")?.value, "created graph entity type");
            if (!scope.entities?.includes(entity)) capabilityError(`graph entity '${entity}' is not approved`);
          } else if (actionName === "create_edge" || actionName === "delete_edge") {
            const edgeType = stringLiteral(objectProperty(action, "type")?.value, "graph edge type");
            if (!scope.edge_types?.includes(edgeType)) capabilityError(`graph edge '${edgeType}' is not approved`);
          }
        }
      }

      if (namespace === "net" && method === "request") {
        const scope = grants.get("network.request");
        if (!scope) capabilityError("ambient.net.request requires network.request");
        const sourceId = stringLiteral(path.node.arguments[0], "network source id");
        if (!Object.hasOwn(scope.sources || {}, sourceId)) {
          capabilityError(`network source '${sourceId}' is not approved`);
        }
        const request = path.node.arguments[1];
        if (!t.isObjectExpression(request)) capabilityError("network request must be an object literal");
        const requestPath = stringLiteral(objectProperty(request, "path")?.value, "network path");
        const methodProperty = objectProperty(request, "method");
        const requestMethod = methodProperty ? stringLiteral(methodProperty.value, "network method").toUpperCase() : "GET";
        const source = scope.sources[sourceId];
        if (!source.paths?.includes(requestPath) || !source.methods?.includes(requestMethod)) {
          capabilityError(`network request '${sourceId} ${requestMethod} ${requestPath}' is not approved`);
        }
      }

      if (namespace === "capabilities" && method === "invoke") {
        const scope = grants.get("capability.invoke");
        if (!scope) capabilityError("ambient.capabilities.invoke requires capability.invoke");
        const catalogId = stringLiteral(path.node.arguments[0], "capability catalog id");
        const actionId = stringLiteral(path.node.arguments[2], "capability action id");
        if (!scope.catalog_ids?.includes(catalogId) || !scope.actions?.includes(actionId)) {
          capabilityError(`capability action '${catalogId}/${actionId}' is not approved`);
        }
      }

      if (namespace === "files" && ["read", "list", "write", "delete"].includes(method)) {
        const category = method === "write" ? "file.write" : method === "delete" ? "file.delete" : "file.read";
        const scope = grants.get(category);
        if (!scope) capabilityError(`ambient.files.${method} requires ${category}`);
        const filePath = stringLiteral(path.node.arguments[0], "file path");
        if (!scope.paths?.some((pattern) => pathMatches(pattern, filePath))) {
          capabilityError(`file path '${filePath}' is not approved`);
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
  if (message.includes("Capability contract")) {
    code = "capability_contract_error";
    hint = "Use only literal operations covered by the approved Manifest V2 capability grants.";
  } else if (message.includes("Forbidden host or network global")) {
    code = "forbidden_runtime_api";
    hint = "Use ambient.net.request, ambient.graph, ambient.files, or an exact approved ambient.capabilities action.";
  } else if (message.includes("Static imports") || message.includes("Dynamic imports")) {
    code = "unsupported_import";
    hint = "Widget controllers are single-file modules; use only the injected ambient SDK.";
  }
  process.stderr.write(JSON.stringify({ ok: false, code, message, hint }));
  process.exitCode = 1;
}
