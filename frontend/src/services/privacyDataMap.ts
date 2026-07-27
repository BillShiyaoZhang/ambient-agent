export type PrivacyMapStage =
  | "chat"
  | "route"
  | "plan"
  | "mutation"
  | "verify"
  | "title"
  | "other";

export type PrivacyMapCoverageChannelId =
  | "llm"
  | "mcp"
  | "http_agent"
  | "coding_agent_acp"
  | "provider_management"
  | "isolated_widget_runtime";

export type PrivacyMapWarningCode =
  | "malformed_json_records"
  | "structurally_invalid_audit_records"
  | "projection_ineligible_audit_records"
  | "oversized_audit_lines"
  | "invalid_app_declarations"
  | "unsafe_schema_ids"
  | "missing_schema_references";

export type PrivacyMapErrorCode =
  | "privacy_map_audit_source_unreadable"
  | "privacy_map_app_source_unreadable"
  | "privacy_map_invalid_request"
  | "privacy_map_schema_source_unreadable"
  | "privacy_map_id_collision"
  | "privacy_map_projection_failed"
  | "privacy_map_resource_limit_exceeded";

export interface PrivacyMapObservedWindow {
  from: string;
  to: string;
}

export interface PrivacyMapScope {
  kind: "workspace";
  observed_window: PrivacyMapObservedWindow | null;
}

export interface PrivacyMapSourceHealth {
  status: "healthy" | "degraded";
  valid_audit_records: number;
  malformed_json_records: number;
  structurally_invalid_audit_records: number;
  projection_ineligible_audit_records: number;
  oversized_audit_lines: number;
  invalid_app_declarations: number;
  unsafe_schema_ids: number;
  missing_schema_references: number;
}

export interface PrivacyMapCoverageChannel {
  id: PrivacyMapCoverageChannelId;
  observation: "partial" | "not_instrumented";
}

export interface PrivacyMapCoverage {
  status: "partial";
  channels: PrivacyMapCoverageChannel[];
}

export interface PrivacyMapPlatformNode {
  id: "platform:ambient-agent";
  kind: "platform";
  label: "Ambient Agent";
}

export interface PrivacyMapRecordedModelTargetNode {
  id: string;
  kind: "recorded_model_target";
  label: string;
  location: "unknown";
}

export interface PrivacyMapAppNode {
  id: string;
  kind: "app";
  label: string;
}

export interface PrivacyMapSchemaNode {
  id: string;
  kind: "schema";
  label: string;
}

export type PrivacyMapNode =
  | PrivacyMapPlatformNode
  | PrivacyMapRecordedModelTargetNode
  | PrivacyMapAppNode
  | PrivacyMapSchemaNode;

export interface PrivacyMapObservedFlow {
  id: string;
  evidence: "observed";
  source_node_id: "platform:ambient-agent";
  destination_node_id: string;
  stage: PrivacyMapStage;
  count: number;
  first_observed_at: string;
  last_observed_at: string;
}

export interface PrivacyMapDeclaredAssociation {
  id: string;
  evidence: "declared";
  app_node_id: string;
  schema_node_id: string;
}

export interface PrivacyMapWarning {
  code: PrivacyMapWarningCode;
  count: number;
}

export interface PrivacyDataMapResponse {
  contract_version: 1;
  generated_at: string;
  scope: PrivacyMapScope;
  source_health: PrivacyMapSourceHealth;
  coverage: PrivacyMapCoverage;
  nodes: PrivacyMapNode[];
  observed_flows: PrivacyMapObservedFlow[];
  declared_associations: PrivacyMapDeclaredAssociation[];
  warnings: PrivacyMapWarning[];
}

const PUBLIC_ERROR_MESSAGE = "Privacy Map is temporarily unavailable.";
const ENDPOINT_PATH = "/api/privacy-data-map";
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const MAX_RECORDED_MODEL_TARGET_NODES = 512;
const MAX_APP_NODES = 2_048;
const MAX_SCHEMA_NODES = 4_096;
const MAX_NODES =
  1 + MAX_RECORDED_MODEL_TARGET_NODES + MAX_APP_NODES + MAX_SCHEMA_NODES;
const MAX_OBSERVED_FLOWS = 2_048;
const MAX_DECLARED_ASSOCIATIONS = 8_192;
const MAX_WARNINGS = 7;
const MAX_RECORDED_MODEL_TARGET_LABEL_CODEPOINTS = 403;
const UTC_TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{0,5}[1-9]))?Z$/;
const DERIVED_ID = /^[a-f0-9]{64}$/;
const APP_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const MAX_APP_ID_LENGTH = 64;
const SCHEMA_ID = /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/;
const PROHIBITED_PROJECTION_TEXT =
  /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u;
const WINDOWS_RESERVED_APP_IDS = new Set([
  "aux",
  "clock$",
  "com1",
  "com2",
  "com3",
  "com4",
  "com5",
  "com6",
  "com7",
  "com8",
  "com9",
  "con",
  "lpt1",
  "lpt2",
  "lpt3",
  "lpt4",
  "lpt5",
  "lpt6",
  "lpt7",
  "lpt8",
  "lpt9",
  "nul",
  "prn",
]);
const COVERAGE_CHANNELS: readonly PrivacyMapCoverageChannelId[] = [
  "llm",
  "mcp",
  "http_agent",
  "coding_agent_acp",
  "provider_management",
  "isolated_widget_runtime",
];
const STAGES = new Set<PrivacyMapStage>([
  "chat",
  "route",
  "plan",
  "mutation",
  "verify",
  "title",
  "other",
]);
const WARNING_CODES = new Set<PrivacyMapWarningCode>([
  "malformed_json_records",
  "structurally_invalid_audit_records",
  "projection_ineligible_audit_records",
  "oversized_audit_lines",
  "invalid_app_declarations",
  "unsafe_schema_ids",
  "missing_schema_references",
]);
const ERROR_CODES = new Set<PrivacyMapErrorCode>([
  "privacy_map_audit_source_unreadable",
  "privacy_map_app_source_unreadable",
  "privacy_map_invalid_request",
  "privacy_map_schema_source_unreadable",
  "privacy_map_id_collision",
  "privacy_map_projection_failed",
  "privacy_map_resource_limit_exceeded",
]);

export class PrivacyDataMapContractError extends Error {
  constructor() {
    super("Privacy Map response did not match contract version 1.");
    this.name = "PrivacyDataMapContractError";
  }
}

export class PrivacyDataMapRequestError extends Error {
  readonly status: number;
  readonly code: PrivacyMapErrorCode;

  constructor(status: number, code: PrivacyMapErrorCode) {
    super(PUBLIC_ERROR_MESSAGE);
    this.name = "PrivacyDataMapRequestError";
    this.status = status;
    this.code = code;
  }
}

export async function loadPrivacyDataMap(
  apiBase: string,
  signal?: AbortSignal
): Promise<PrivacyDataMapResponse> {
  const response = await fetch(`${apiBase.replace(/\/+$/, "")}${ENDPOINT_PATH}`, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });

  const payload = await readBoundedJson(response);

  if (!response.ok) {
    if (!isErrorResponse(payload)) throw new PrivacyDataMapContractError();
    throw new PrivacyDataMapRequestError(response.status, payload.error.code);
  }
  if (!isPrivacyDataMapResponse(payload)) throw new PrivacyDataMapContractError();
  return payload;
}

function isPrivacyDataMapResponse(value: unknown): value is PrivacyDataMapResponse {
  if (
    !isExactRecord(value, [
      "contract_version",
      "generated_at",
      "scope",
      "source_health",
      "coverage",
      "nodes",
      "observed_flows",
      "declared_associations",
      "warnings",
    ]) ||
    value.contract_version !== 1 ||
    !isUtcTimestamp(value.generated_at) ||
    !isScope(value.scope) ||
    !isSourceHealth(value.source_health) ||
    !isCoverage(value.coverage) ||
    !Array.isArray(value.nodes) ||
    value.nodes.length > MAX_NODES ||
    !value.nodes.every(isNode) ||
    !Array.isArray(value.observed_flows) ||
    value.observed_flows.length > MAX_OBSERVED_FLOWS ||
    !value.observed_flows.every(isObservedFlow) ||
    !Array.isArray(value.declared_associations) ||
    value.declared_associations.length > MAX_DECLARED_ASSOCIATIONS ||
    !value.declared_associations.every(isDeclaredAssociation) ||
    !Array.isArray(value.warnings) ||
    value.warnings.length > MAX_WARNINGS ||
    !value.warnings.every(isWarning)
  ) {
    return false;
  }
  if (!nodesWithinKindLimits(value.nodes)) return false;

  const nodesById = uniqueIndex(value.nodes, (node) => node.id);
  const flowsById = uniqueIndex(value.observed_flows, (flow) => flow.id);
  const declarationsById = uniqueIndex(value.declared_associations, (association) => association.id);
  const warningsByCode = uniqueIndex(value.warnings, (warning) => warning.code);
  if (!nodesById || !flowsById || !declarationsById || !warningsByCode) return false;
  if (nodesById.get("platform:ambient-agent")?.kind !== "platform") return false;

  const recordedModelNodeIds = new Set(
    value.nodes.filter((node) => node.kind === "recorded_model_target").map((node) => node.id)
  );
  const schemaNodeIds = new Set(
    value.nodes.filter((node) => node.kind === "schema").map((node) => node.id)
  );
  const observedModelNodeIds = new Set<string>();
  const associatedSchemaNodeIds = new Set<string>();
  const observedFlowGroups = new Set<string>();
  const declaredAssociationPairs = new Set<string>();
  let observedRecordCount = 0;
  let firstObservedKey: string | null = null;
  let lastObservedKey: string | null = null;

  for (const flow of value.observed_flows) {
    if (nodesById.get(flow.destination_node_id)?.kind !== "recorded_model_target") return false;
    const firstKey = utcTimestampKey(flow.first_observed_at);
    const lastKey = utcTimestampKey(flow.last_observed_at);
    if (firstKey === null || lastKey === null || firstKey > lastKey) return false;
    const groupKey = `${flow.destination_node_id}\u0000${flow.stage}`;
    if (observedFlowGroups.has(groupKey)) return false;
    observedFlowGroups.add(groupKey);
    observedModelNodeIds.add(flow.destination_node_id);
    observedRecordCount += flow.count;
    if (!Number.isSafeInteger(observedRecordCount)) return false;
    firstObservedKey = earlierTimestampKey(firstObservedKey, firstKey);
    lastObservedKey = laterTimestampKey(lastObservedKey, lastKey);
  }
  for (const association of value.declared_associations) {
    if (nodesById.get(association.app_node_id)?.kind !== "app") return false;
    if (nodesById.get(association.schema_node_id)?.kind !== "schema") return false;
    const pairKey = `${association.app_node_id}\u0000${association.schema_node_id}`;
    if (declaredAssociationPairs.has(pairKey)) return false;
    declaredAssociationPairs.add(pairKey);
    associatedSchemaNodeIds.add(association.schema_node_id);
  }

  if (observedRecordCount !== value.source_health.valid_audit_records) return false;
  if (!sameSet(recordedModelNodeIds, observedModelNodeIds)) return false;
  if (!sameSet(schemaNodeIds, associatedSchemaNodeIds)) return false;
  if (firstObservedKey === null || lastObservedKey === null) {
    if (value.scope.observed_window !== null) return false;
  } else {
    if (value.scope.observed_window === null) return false;
    if (utcTimestampKey(value.scope.observed_window.from) !== firstObservedKey) return false;
    if (utcTimestampKey(value.scope.observed_window.to) !== lastObservedKey) return false;
  }
  return warningsMatchHealth(warningsByCode, value.source_health);
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const contentLength = response.headers.get("Content-Length");
  if (contentLength !== null) {
    if (!/^\d+$/.test(contentLength)) throw new PrivacyDataMapContractError();
    const declaredBytes = Number(contentLength);
    if (!Number.isSafeInteger(declaredBytes) || declaredBytes > MAX_RESPONSE_BYTES) {
      throw new PrivacyDataMapContractError();
    }
  }

  if (response.body === null) throw new PrivacyDataMapContractError();

  const reader = response.body.getReader();
  const bytes = new Uint8Array(MAX_RESPONSE_BYTES);
  let bytesRead = 0;

  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      if (bytesRead + chunk.value.byteLength > MAX_RESPONSE_BYTES) {
        try {
          await reader.cancel();
        } catch {
          // The response is already rejected; cancellation is best-effort cleanup.
        }
        throw new PrivacyDataMapContractError();
      }
      bytes.set(chunk.value, bytesRead);
      bytesRead += chunk.value.byteLength;
    }

    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, bytesRead));
    return JSON.parse(text) as unknown;
  } catch (error) {
    if (error instanceof PrivacyDataMapContractError) throw error;
    throw new PrivacyDataMapContractError();
  } finally {
    reader.releaseLock();
  }
}

function isScope(value: unknown): value is PrivacyMapScope {
  if (!isExactRecord(value, ["kind", "observed_window"]) || value.kind !== "workspace") return false;
  if (value.observed_window === null) return true;
  if (
    !isExactRecord(value.observed_window, ["from", "to"]) ||
    !isUtcTimestamp(value.observed_window.from) ||
    !isUtcTimestamp(value.observed_window.to)
  ) {
    return false;
  }
  const fromKey = utcTimestampKey(value.observed_window.from);
  const toKey = utcTimestampKey(value.observed_window.to);
  return fromKey !== null && toKey !== null && fromKey <= toKey;
}

function isSourceHealth(value: unknown): value is PrivacyMapSourceHealth {
  if (
    !isExactRecord(value, [
      "status",
      "valid_audit_records",
      "malformed_json_records",
      "structurally_invalid_audit_records",
      "projection_ineligible_audit_records",
      "oversized_audit_lines",
      "invalid_app_declarations",
      "unsafe_schema_ids",
      "missing_schema_references",
    ]) ||
    (value.status !== "healthy" && value.status !== "degraded")
  ) {
    return false;
  }
  const counts = [
    value.valid_audit_records,
    value.malformed_json_records,
    value.structurally_invalid_audit_records,
    value.projection_ineligible_audit_records,
    value.oversized_audit_lines,
    value.invalid_app_declarations,
    value.unsafe_schema_ids,
    value.missing_schema_references,
  ];
  if (!counts.every(isNonnegativeInteger)) return false;
  const issueTotal = counts.slice(1).reduce((total, count) => total + count, 0);
  return value.status === (issueTotal === 0 ? "healthy" : "degraded");
}

function isCoverage(value: unknown): value is PrivacyMapCoverage {
  if (
    !isExactRecord(value, ["status", "channels"]) ||
    value.status !== "partial" ||
    !Array.isArray(value.channels) ||
    value.channels.length !== COVERAGE_CHANNELS.length
  ) {
    return false;
  }
  return value.channels.every((channel, index) => {
    if (!isExactRecord(channel, ["id", "observation"])) return false;
    const id = COVERAGE_CHANNELS[index];
    return (
      channel.id === id &&
      channel.observation === (id === "llm" ? "partial" : "not_instrumented")
    );
  });
}

function isNode(value: unknown): value is PrivacyMapNode {
  if (!isRecord(value) || typeof value.kind !== "string") return false;
  if (value.kind === "platform") {
    return (
      isExactRecord(value, ["id", "kind", "label"]) &&
      value.id === "platform:ambient-agent" &&
      value.label === "Ambient Agent"
    );
  }
  if (value.kind === "recorded_model_target") {
    return (
      isExactRecord(value, ["id", "kind", "label", "location"]) &&
      isDerivedId(value.id, "model_target:") &&
      isRecordedModelTargetLabel(value.label) &&
      value.location === "unknown"
    );
  }
  if (value.kind === "app") {
    return (
      isExactRecord(value, ["id", "kind", "label"]) &&
      isValidAppId(value.label) &&
      value.id === `app:${value.label}` &&
      isNonemptyString(value.id)
    );
  }
  if (value.kind === "schema") {
    return (
      isExactRecord(value, ["id", "kind", "label"]) &&
      isNonemptyString(value.label) &&
      SCHEMA_ID.test(value.label) &&
      value.id === `schema:${value.label}`
    );
  }
  return false;
}

function isObservedFlow(value: unknown): value is PrivacyMapObservedFlow {
  return (
    isExactRecord(value, [
      "id",
      "evidence",
      "source_node_id",
      "destination_node_id",
      "stage",
      "count",
      "first_observed_at",
      "last_observed_at",
    ]) &&
    isDerivedId(value.id, "flow:") &&
    value.evidence === "observed" &&
    value.source_node_id === "platform:ambient-agent" &&
    isNonemptyString(value.destination_node_id) &&
    typeof value.stage === "string" &&
    STAGES.has(value.stage as PrivacyMapStage) &&
    isPositiveInteger(value.count) &&
    isUtcTimestamp(value.first_observed_at) &&
    isUtcTimestamp(value.last_observed_at)
  );
}

function isDeclaredAssociation(value: unknown): value is PrivacyMapDeclaredAssociation {
  return (
    isExactRecord(value, [
      "id",
      "evidence",
      "app_node_id",
      "schema_node_id",
    ]) &&
    isDerivedId(value.id, "declaration:") &&
    value.evidence === "declared" &&
    isNonemptyString(value.app_node_id) &&
    isNonemptyString(value.schema_node_id)
  );
}

function isWarning(value: unknown): value is PrivacyMapWarning {
  return (
    isExactRecord(value, ["code", "count"]) &&
    typeof value.code === "string" &&
    WARNING_CODES.has(value.code as PrivacyMapWarningCode) &&
    isPositiveInteger(value.count)
  );
}

function isErrorResponse(
  value: unknown
): value is {
  contract_version: 1;
  error: { code: PrivacyMapErrorCode; message: typeof PUBLIC_ERROR_MESSAGE };
} {
  return (
    isExactRecord(value, ["contract_version", "error"]) &&
    value.contract_version === 1 &&
    isExactRecord(value.error, ["code", "message"]) &&
    typeof value.error.code === "string" &&
    ERROR_CODES.has(value.error.code as PrivacyMapErrorCode) &&
    value.error.message === PUBLIC_ERROR_MESSAGE
  );
}

function warningsMatchHealth(
  warnings: Map<PrivacyMapWarningCode, PrivacyMapWarning>,
  health: PrivacyMapSourceHealth
): boolean {
  for (const code of WARNING_CODES) {
    const count = health[code];
    const warning = warnings.get(code);
    if (count === 0 ? warning !== undefined : warning?.count !== count) return false;
  }
  return true;
}

function uniqueIndex<T, K>(items: T[], keyOf: (item: T) => K): Map<K, T> | null {
  const index = new Map<K, T>();
  for (const item of items) {
    const key = keyOf(item);
    if (index.has(key)) return null;
    index.set(key, item);
  }
  return index;
}

function isDerivedId(value: unknown, prefix: string): value is string {
  return (
    typeof value === "string" &&
    value.startsWith(prefix) &&
    DERIVED_ID.test(value.slice(prefix.length))
  );
}

function isUtcTimestamp(value: unknown): value is string {
  return utcTimestampKey(value) !== null;
}

function utcTimestampKey(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const match = UTC_TIMESTAMP.exec(value);
  if (match === null) return null;

  const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction = ""] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  if (
    year < 1 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth(year, month) ||
    hour > 23 ||
    minute > 59 ||
    second > 59
  ) {
    return null;
  }
  return `${yearText}${monthText}${dayText}${hourText}${minuteText}${secondText}${fraction.padEnd(
    6,
    "0"
  )}`;
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    return leapYear ? 29 : 28;
  }
  return month === 4 || month === 6 || month === 9 || month === 11 ? 30 : 31;
}

function isValidAppId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= MAX_APP_ID_LENGTH &&
    APP_ID.test(value) &&
    !WINDOWS_RESERVED_APP_IDS.has(value)
  );
}

function nodesWithinKindLimits(nodes: PrivacyMapNode[]): boolean {
  let platformNodes = 0;
  let recordedModelTargets = 0;
  let appNodes = 0;
  let schemaNodes = 0;

  for (const node of nodes) {
    if (node.kind === "platform") platformNodes += 1;
    else if (node.kind === "recorded_model_target") recordedModelTargets += 1;
    else if (node.kind === "app") appNodes += 1;
    else schemaNodes += 1;
  }

  return (
    platformNodes === 1 &&
    recordedModelTargets <= MAX_RECORDED_MODEL_TARGET_NODES &&
    appNodes <= MAX_APP_NODES &&
    schemaNodes <= MAX_SCHEMA_NODES
  );
}

function isRecordedModelTargetLabel(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value === value.normalize("NFKC") &&
    value === value.trim() &&
    [...value].length <= MAX_RECORDED_MODEL_TARGET_LABEL_CODEPOINTS &&
    !PROHIBITED_PROJECTION_TEXT.test(value)
  );
}

function isNonemptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isNonnegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isExactRecord<const K extends string>(
  value: unknown,
  keys: readonly K[]
): value is Record<K, unknown> {
  if (!isRecord(value)) return false;
  const actualKeys = Object.keys(value);
  return (
    actualKeys.length === keys.length &&
    keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
}

function sameSet<T>(left: Set<T>, right: Set<T>): boolean {
  return left.size === right.size && [...left].every((item) => right.has(item));
}

function earlierTimestampKey(current: string | null, candidate: string): string {
  return current === null || candidate < current ? candidate : current;
}

function laterTimestampKey(current: string | null, candidate: string): string {
  return current === null || candidate > current ? candidate : current;
}
