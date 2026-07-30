import type { WidgetSchemaProposal } from "./widgetDesign";
import {
  GRAPH_DATASET_VERSION,
  normalizeGraphDataset,
  stableGraphId,
  type GraphDataset,
  type GraphEdge,
  type GraphNode,
  type GraphStatus,
} from "./graphVisualization";

export interface WorkflowDescriptorNode {
  id: string;
  label: string;
  kind: "phase" | "wait" | "terminal";
  responsibility: string;
  sourceFile: string;
  symbol: string;
}

export interface WorkflowDescriptorEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  kind: "transition" | "condition" | "wait" | "rework" | "terminal";
}

export interface WorkflowDescriptor {
  /** Version of this serializable visualization descriptor. */
  version: 1;
  /** DurableAgentWorkflow.VERSION represented by the descriptor. */
  workflowVersion: 2;
  id: "durable-agent";
  nodes: WorkflowDescriptorNode[];
  edges: WorkflowDescriptorEdge[];
}

const WORKFLOW_SOURCE_FILE = "backend/agent/durable_workflow.py";
const GITHUB_SOURCE_ROOT =
  "https://github.com/BillShiyaoZhang/ambient-agent/blob/main";

function phase(
  id: string,
  label: string,
  responsibility: string,
  kind: WorkflowDescriptorNode["kind"] = "phase",
  sourceFile = WORKFLOW_SOURCE_FILE,
  symbol?: string,
): WorkflowDescriptorNode {
  return {
    id,
    label,
    kind,
    responsibility,
    sourceFile,
    symbol: symbol ?? `DurableAgentWorkflow._phase_${id}`,
  };
}

function transition(
  source: string,
  target: string,
  label: string,
  kind: WorkflowDescriptorEdge["kind"] = "transition",
): WorkflowDescriptorEdge {
  return {
    id: `${source}:${target}:${stableGraphId("condition", label)}`,
    source,
    target,
    label,
    kind,
  };
}

/**
 * Explicit, reviewable descriptor for durable workflow v2. Keeping this
 * separate from the rendering component makes phase additions visible in
 * tests and prevents UI code from guessing transitions from live events.
 */
export const DURABLE_WORKFLOW_DESCRIPTOR: WorkflowDescriptor = {
  version: 1,
  workflowVersion: 2,
  id: "durable-agent",
  nodes: [
    phase("route", "Route intent", "Classify the request and select a durable subflow."),
    phase("clarify", "Request clarification", "Persist a clarification response and finish the request."),
    phase("converse", "Converse", "Run the conversational tool loop and persist its response."),
    phase("graph_query", "Query graph", "Execute a read-only context graph query and format the result."),
    phase("graph_preflight", "Graph preflight", "Validate and preview graph mutation actions."),
    phase("wait_graph_approval", "Graph approval", "Wait for explicit approval of graph mutations.", "wait"),
    phase("graph_commit", "Commit graph mutation", "Apply the approved mutation as an idempotent durable effect."),
    phase("multi_preflight", "Saga preflight", "Validate every step before beginning a multi-intent saga."),
    phase("multi_dispatch", "Dispatch saga step", "Dispatch the next planned intent or finish the saga."),
    phase("plan", "Development plan", "Generate or revise the App development plan."),
    phase("wait_plan", "Plan approval", "Wait for approval or refinement of the development plan.", "wait"),
    phase("align_schema", "Align schema", "Create a schema and capability proposal from the approved plan."),
    phase("wait_schema", "Schema approval", "Wait for an editable schema and capability approval.", "wait"),
    phase("stage_code", "Generate staged App", "Generate or repair App code in an isolated staging directory."),
    phase("verify", "Verify App", "Verify staged code against the approved Runtime Contract."),
    phase("wait_override", "Repair decision", "Wait for a mandatory code, schema, or plan repair choice.", "wait"),
    phase("promote", "Publish App", "Atomically publish the verified App and approved schema effects."),
    phase(
      "done",
      "Complete",
      "The durable Run completed successfully and persisted its final checkpoint.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
    phase(
      "failed",
      "Failed",
      "The durable Run stopped with a preserved non-retryable error.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
    phase(
      "needs_attention",
      "Needs attention",
      "An effect may have committed and requires explicit reconciliation.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
    phase(
      "cancelled",
      "Cancelled",
      "The durable Run was safely cancelled without an unknown effect.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
  ],
  edges: [
    transition("route", "clarify", "clarification intent", "condition"),
    transition("route", "converse", "conversation intent", "condition"),
    transition("route", "graph_query", "graph query intent", "condition"),
    transition("route", "graph_preflight", "graph mutation intent", "condition"),
    transition("route", "plan", "widget create / modify", "condition"),
    transition("route", "multi_preflight", "multi-intent / plan-and-act", "condition"),
    transition("graph_preflight", "wait_graph_approval", "preview ready", "wait"),
    transition("wait_graph_approval", "graph_commit", "approved", "condition"),
    transition("multi_preflight", "multi_dispatch", "preflight passed"),
    transition("multi_dispatch", "graph_query", "next graph query", "condition"),
    transition("multi_dispatch", "graph_preflight", "next graph mutation", "condition"),
    transition("multi_dispatch", "plan", "next widget action", "condition"),
    transition("graph_query", "multi_dispatch", "return to saga", "transition"),
    transition("graph_commit", "multi_dispatch", "return to saga", "transition"),
    transition("plan", "wait_plan", "proposal ready", "wait"),
    transition("wait_plan", "align_schema", "approved", "condition"),
    transition("wait_plan", "wait_plan", "refine", "rework"),
    transition("wait_plan", "failed", "denied", "terminal"),
    transition("align_schema", "wait_schema", "proposal ready", "wait"),
    transition("wait_schema", "stage_code", "approved", "condition"),
    transition("wait_schema", "wait_schema", "refine / fix dependencies", "rework"),
    transition("wait_schema", "plan", "rework plan", "rework"),
    transition("wait_schema", "failed", "denied", "terminal"),
    transition("stage_code", "verify", "draft staged"),
    transition("verify", "promote", "verification clean", "condition"),
    transition("verify", "wait_override", "findings require repair", "wait"),
    transition("wait_override", "stage_code", "rework code", "rework"),
    transition("wait_override", "align_schema", "rework schema", "rework"),
    transition("wait_override", "plan", "rework plan", "rework"),
    transition("wait_override", "wait_override", "bypass rejected", "rework"),
    transition("wait_override", "failed", "denied", "terminal"),
    transition("wait_graph_approval", "failed", "denied", "terminal"),
    transition("clarify", "done", "response persisted", "terminal"),
    transition("converse", "done", "response persisted", "terminal"),
    transition("graph_query", "done", "query returned", "terminal"),
    transition("graph_commit", "done", "mutation committed", "terminal"),
    transition("promote", "done", "artifact published", "terminal"),
    transition("promote", "multi_dispatch", "return to saga", "transition"),
    transition("multi_dispatch", "done", "all saga steps complete", "terminal"),
    transition("needs_attention", "failed", "effect reconciled", "terminal"),
  ],
};

const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

const asString = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value.trim() : undefined;

const asFiniteNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

const asRecords = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value)
    ? value.map(asRecord).filter((item): item is Record<string, unknown> => item !== null)
    : [];

export type GraphSceneLanguage = "zh" | "en";

const SCHEMA_COPY = {
  en: {
    unnamedReused: "Unnamed reused entity",
    reusedSummary: "Reuse a canonical ontology entity.",
    entityId: "Entity ID",
    extendedProperties: "Extended properties",
    dataScope: "Data scope",
    reused: "Reused",
    unnamedNew: "Unnamed new entity",
    newSummary: "New user-context ontology entity.",
    properties: "Properties",
    ontologyIri: "Ontology IRI",
    equivalentIris: "Equivalent IRIs",
    new: "New",
    duplicateId: "Duplicate ID",
    canonicalRoot: "Canonical ontology root.",
    externalParent: "Referenced parent outside this proposal; backend validation remains authoritative.",
    parent: "Parent",
    parentVerify: "Parent · verify",
    proposalLabel: "Widget schema proposal",
    proposalSummary: "The editable proposal remains the source of truth.",
    unnamedCapability: "Unnamed capability",
    graphGrantSummary: "Entity-scoped Graph grant.",
    capabilitySummary: "Requested App capability.",
    scope: "Scope",
    graphGrant: "Graph grant",
    capability: "Capability",
    danglingGrantSummary: "This Graph grant references an entity outside the proposal.",
    danglingGrant: "Dangling grant",
    capabilityScope: "capability scope",
    dependencyError: "Dependency error",
    diagnostic: "Diagnostic",
    blocksApprovalBadge: "Blocks approval",
    blocksApprovalEdge: "blocks approval",
    title: "Schema and capability proposal",
    description: "A live projection of the editable approval proposal.",
    metadataDescription: "Edit the form to rebuild this graph; the graph never grants permission.",
  },
  zh: {
    unnamedReused: "未命名复用实体",
    reusedSummary: "复用规范本体实体。",
    entityId: "实体 ID",
    extendedProperties: "扩展属性",
    dataScope: "数据范围",
    reused: "复用",
    unnamedNew: "未命名新实体",
    newSummary: "新的用户上下文本体实体。",
    properties: "属性",
    ontologyIri: "本体 IRI",
    equivalentIris: "等价 IRI",
    new: "新建",
    duplicateId: "ID 重复",
    canonicalRoot: "规范本体根实体。",
    externalParent: "此父实体不在当前提案中；仍以后端校验为准。",
    parent: "父实体",
    parentVerify: "父实体 · 待验证",
    proposalLabel: "Widget Schema 提案",
    proposalSummary: "可编辑提案仍是事实来源。",
    unnamedCapability: "未命名能力",
    graphGrantSummary: "限定实体范围的 Graph 授权。",
    capabilitySummary: "请求的 App 能力。",
    scope: "范围",
    graphGrant: "Graph 授权",
    capability: "能力",
    danglingGrantSummary: "此 Graph 授权引用了提案之外的实体。",
    danglingGrant: "悬空授权",
    capabilityScope: "能力范围",
    dependencyError: "依赖错误",
    diagnostic: "诊断",
    blocksApprovalBadge: "阻止批准",
    blocksApprovalEdge: "阻止批准",
    title: "Schema 与能力提案",
    description: "可编辑批准提案的实时投影。",
    metadataDescription: "编辑表单会重建此图；图本身不会授予权限。",
  },
} as const;

const schemaNodeId = (category: "reused" | "new", id: string, index: number): string =>
  `schema:${category}:${encodeURIComponent(id || "unnamed")}:${index}`;

export function schemaProposalToGraph(
  proposal: WidgetSchemaProposal,
  dependencyErrors: string[] = [],
  language: GraphSceneLanguage = "en",
): GraphDataset {
  const copy = SCHEMA_COPY[language];
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const entityNodes = new Map<string, string[]>();
  const registerEntity = (entityId: string, nodeId: string): void => {
    const ids = entityNodes.get(entityId) ?? [];
    ids.push(nodeId);
    entityNodes.set(entityId, ids);
  };

  proposal.reused_schemas.forEach((schema, index) => {
    const entityId = schema.id.trim();
    const nodeId = schemaNodeId("reused", entityId, index);
    registerEntity(entityId, nodeId);
    nodes.push({
      id: nodeId,
      label: entityId || copy.unnamedReused,
      kind: "schema-reused",
      status: entityId ? "existing" : "error",
      summary: schema.reason || copy.reusedSummary,
      details: {
        [copy.entityId]: entityId,
        [copy.extendedProperties]: schema.extended_properties,
        [copy.dataScope]: schema.data_scope ?? "user_context",
      },
      badges: [copy.reused],
    });
  });

  proposal.new_schemas.forEach((schema, index) => {
    const entityId = schema.id.trim();
    const nodeId = schemaNodeId("new", entityId, index);
    registerEntity(entityId, nodeId);
    nodes.push({
      id: nodeId,
      label: schema.name.trim() || entityId || copy.unnamedNew,
      kind: "schema-new",
      status: entityId ? "default" : "error",
      summary: schema.description || copy.newSummary,
      details: {
        [copy.entityId]: entityId,
        [copy.properties]: schema.properties,
        [copy.ontologyIri]: schema.ontology_iri,
        [copy.equivalentIris]: schema.equivalent_to,
        [copy.dataScope]: schema.data_scope,
      },
      badges: [copy.new],
    });
  });

  for (const [entityId, ids] of entityNodes) {
    if (entityId && ids.length > 1) {
      nodes.forEach((node) => {
        if (ids.includes(node.id)) {
          node.status = "error";
          node.badges = [...(node.badges ?? []), copy.duplicateId];
        }
      });
    }
  }

  const parentNodeById = new Map<string, string>();
  proposal.new_schemas.forEach((schema, index) => {
    const parentId = schema.subclass_of.trim();
    if (!parentId) return;
    let targetId = entityNodes.get(parentId)?.[0];
    if (!targetId) {
      targetId = parentNodeById.get(parentId);
      if (!targetId) {
        targetId = `schema:parent:${encodeURIComponent(parentId)}`;
        parentNodeById.set(parentId, targetId);
        nodes.push({
          id: targetId,
          label: parentId,
          kind: "schema-parent",
          status: parentId === "Thing" ? "existing" : "unknown",
          summary: parentId === "Thing"
            ? copy.canonicalRoot
            : copy.externalParent,
          badges: [parentId === "Thing" ? copy.parent : copy.parentVerify],
        });
      }
    }
    edges.push({
      id: `schema-parent-edge:${index}:${encodeURIComponent(parentId)}`,
      source: schemaNodeId("new", schema.id.trim(), index),
      target: targetId,
      label: "subclass_of",
      kind: "inheritance",
    });
  });

  const proposalNodeId = "schema:proposal";
  nodes.push({
    id: proposalNodeId,
    label: copy.proposalLabel,
    kind: "schema-proposal",
    summary: copy.proposalSummary,
  });
  proposal.capabilities.forEach((capability, index) => {
    const capabilityId = capability.id.trim();
    const isGraphGrant = capabilityId === "graph.query" || capabilityId === "graph.mutate";
    const nodeId = `capability:${index}:${encodeURIComponent(capabilityId || "unnamed")}`;
    nodes.push({
      id: nodeId,
      label: capabilityId || copy.unnamedCapability,
      kind: isGraphGrant ? "graph-grant" : "capability",
      status: capabilityId ? "default" : "error",
      summary: isGraphGrant ? copy.graphGrantSummary : copy.capabilitySummary,
      details: { [copy.scope]: capability.scope },
      badges: [isGraphGrant ? copy.graphGrant : copy.capability],
    });

    const rawEntities = Array.isArray(capability.scope.entities)
      ? capability.scope.entities.filter((value): value is string =>
        typeof value === "string" && value.trim().length > 0)
      : [];
    if (isGraphGrant && rawEntities.length > 0) {
      for (const entityId of [...new Set(rawEntities.map((value) => value.trim()))].sort()) {
        let targets = entityNodes.get(entityId);
        if (!targets || targets.length === 0) {
          const missingNodeId = `schema:missing:${encodeURIComponent(entityId)}`;
          if (!nodes.some((node) => node.id === missingNodeId)) {
            nodes.push({
              id: missingNodeId,
              label: entityId,
              kind: "validation-error",
              status: "error",
              summary: copy.danglingGrantSummary,
              badges: [copy.danglingGrant],
            });
          }
          targets = [missingNodeId];
        }
        for (const target of targets) {
          edges.push({
            id: `grant:${index}:${encodeURIComponent(entityId)}:${target}`,
            source: nodeId,
            target,
            label: capabilityId,
            kind: "capability-scope",
            status: target.startsWith("schema:missing:") ? "error" : "default",
          });
        }
      }
    } else {
      edges.push({
        id: `capability-scope:${index}`,
        source: nodeId,
        target: proposalNodeId,
        label: copy.capabilityScope,
        kind: "capability-scope",
      });
    }
  });

  dependencyErrors.forEach((message, index) => {
    const normalizedMessage = message.trim();
    if (!normalizedMessage) return;
    const errorNodeId = `${stableGraphId("schema-validation", normalizedMessage)}:${index}`;
    nodes.push({
      id: errorNodeId,
      label: copy.dependencyError,
      kind: "validation-error",
      status: "error",
      summary: normalizedMessage,
      details: { [copy.diagnostic]: normalizedMessage },
      badges: [copy.blocksApprovalBadge],
    });
    edges.push({
      id: `validation:${index}`,
      source: errorNodeId,
      target: proposalNodeId,
      label: copy.blocksApprovalEdge,
      kind: "validation",
      status: "error",
    });
  });

  return normalizeGraphDataset({
    version: GRAPH_DATASET_VERSION,
    title: copy.title,
    description: copy.description,
    nodes,
    edges,
    metadata: {
      title: copy.title,
      description: copy.metadataDescription,
      truncated: false,
    },
  });
}

interface WorkflowOverlay {
  status?: GraphStatus;
  attempt?: number;
  summary?: string;
  result?: unknown;
  error?: unknown;
  eventType?: string;
  sequence?: number;
  current?: boolean;
  checkpoint?: boolean;
}

function phaseIdFromRecord(value: Record<string, unknown>): string | undefined {
  return asString(value.step_key)
    ?? asString(value.step_id)
    ?? asString(value.phase)
    ?? asString(value.id);
}

function normalizeRunStatus(value: unknown): GraphStatus | undefined {
  const status = asString(value)?.toLocaleLowerCase();
  if (!status) return undefined;
  if (["success", "succeeded", "completed", "committed", "passed"].includes(status)) return "succeeded";
  if (["failed", "failure", "error"].includes(status)) return "failed";
  if (["running", "started", "in_progress", "active"].includes(status)) return "running";
  if (status === "needs_attention") return "needs_attention";
  if (["waiting", "waiting_user", "paused", "blocked"].includes(status)) return "waiting";
  if (["cancelled", "canceled", "cancel_requested"].includes(status)) return "cancelled";
  if (["queued", "pending", "idle", "not_started"].includes(status)) return "not_started";
  return status;
}

function eventStatus(event: Record<string, unknown>): GraphStatus | undefined {
  const payload = asRecord(event.payload);
  const outcome = asRecord(payload?.outcome);
  const explicit = normalizeRunStatus(event.status ?? payload?.status ?? payload?.to);
  if (explicit) return explicit;
  const type = asString(event.type)?.toLocaleLowerCase() ?? "";
  if (type === "step_committed") {
    const outcomeKind = asString(outcome?.kind)?.toLocaleLowerCase();
    if (outcomeKind === "continue" || outcomeKind === "succeeded") return "succeeded";
    if (outcomeKind === "wait") return "waiting";
    if (outcomeKind === "failed") return "failed";
    if (outcomeKind === "cancelled" || outcomeKind === "canceled") return "cancelled";
  }
  if (type.includes("failed") || type.includes("error")) return "failed";
  if (type.includes("started") || type.includes("running")) return "running";
  if (type.includes("waiting") || type.includes("attention")) return "waiting";
  if (type.includes("committed") || type.includes("completed") || type.includes("succeeded")) return "succeeded";
  if (type.includes("cancel")) return "cancelled";
  return undefined;
}

function errorFromRecord(value: Record<string, unknown>): unknown {
  const payload = asRecord(value.payload);
  return value.error ?? payload?.error ?? payload?.message;
}

function addBadge(badges: string[], badge: string): void {
  if (!badges.includes(badge)) badges.push(badge);
}

const WORKFLOW_NODE_ZH: Record<string, { label: string; responsibility: string }> = {
  route: { label: "路由意图", responsibility: "分类请求并选择可持久化的子流程。" },
  clarify: { label: "请求澄清", responsibility: "持久化澄清回复并结束请求。" },
  converse: { label: "对话", responsibility: "运行对话工具循环并持久化其回复。" },
  graph_query: { label: "查询图谱", responsibility: "执行只读上下文图谱查询并格式化结果。" },
  graph_preflight: { label: "图谱预检", responsibility: "校验并预览图谱变更动作。" },
  wait_graph_approval: { label: "图谱批准", responsibility: "等待对图谱变更的明确批准。" },
  graph_commit: { label: "提交图谱变更", responsibility: "将获批变更作为幂等持久化 effect 执行。" },
  multi_preflight: { label: "Saga 预检", responsibility: "在启动多意图 Saga 前校验每个步骤。" },
  multi_dispatch: { label: "分派 Saga 步骤", responsibility: "分派下一个计划意图或结束 Saga。" },
  plan: { label: "开发计划", responsibility: "生成或修订 App 开发计划。" },
  wait_plan: { label: "计划批准", responsibility: "等待批准或优化开发计划。" },
  align_schema: { label: "对齐 Schema", responsibility: "根据获批计划创建 Schema 与能力提案。" },
  wait_schema: { label: "Schema 批准", responsibility: "等待可编辑的 Schema 与能力批准。" },
  stage_code: { label: "生成暂存 App", responsibility: "在隔离的暂存目录生成或修复 App 代码。" },
  verify: { label: "验证 App", responsibility: "依据获批的 Runtime Contract 验证暂存代码。" },
  wait_override: { label: "修复决策", responsibility: "等待必需的代码、Schema 或计划修复选择。" },
  promote: { label: "发布 App", responsibility: "原子发布已验证 App 及获批的 Schema effect。" },
  done: { label: "完成", responsibility: "持久化最终检查点后，durable Run 成功完成。" },
  failed: { label: "失败", responsibility: "durable Run 因保留的不可重试错误而停止。" },
  needs_attention: { label: "需要关注", responsibility: "某个 effect 可能已提交，需要明确对账。" },
  cancelled: { label: "已取消", responsibility: "durable Run 已安全取消，不存在未知 effect。" },
};

const WORKFLOW_EDGE_ZH: Record<string, string> = {
  "clarification intent": "澄清意图",
  "conversation intent": "对话意图",
  "graph query intent": "图谱查询意图",
  "graph mutation intent": "图谱变更意图",
  "widget create / modify": "Widget 创建 / 修改",
  "multi-intent / plan-and-act": "多意图 / 计划并执行",
  "preview ready": "预览就绪",
  approved: "批准",
  "preflight passed": "预检通过",
  "next graph query": "下一个图谱查询",
  "next graph mutation": "下一个图谱变更",
  "next widget action": "下一个 Widget 动作",
  "return to saga": "返回 Saga",
  "proposal ready": "提案就绪",
  refine: "优化",
  denied: "拒绝",
  "refine / fix dependencies": "优化 / 修复依赖",
  "rework plan": "返工计划",
  "draft staged": "草稿已暂存",
  "verification clean": "验证通过",
  "findings require repair": "发现问题需修复",
  "rework code": "返工代码",
  "rework schema": "返工 Schema",
  "bypass rejected": "拒绝绕过",
  "response persisted": "响应已持久化",
  "query returned": "查询已返回",
  "mutation committed": "变更已提交",
  "artifact published": "产物已发布",
  "all saga steps complete": "所有 Saga 步骤已完成",
  "effect reconciled": "effect 已完成对账",
};

const WORKFLOW_COPY = {
  en: {
    waitBadge: "Wait",
    terminalBadge: "Terminal",
    attemptBadge: (attempt: number) => `Attempt ${attempt}`,
    checkpointBadge: "Checkpoint",
    currentBadge: "Current phase",
    responsibility: "Responsibility",
    sourceFile: "Source file",
    symbol: "Symbol",
    executionStatus: "Execution status",
    latestEvent: "Latest event",
    result: "Result",
    error: "Error",
    openSource: "Open source",
    unknownPhaseBadge: "Unknown phase",
    unknownSummary: "This phase is not present in visualization descriptor v1.",
    phaseId: "Phase ID",
    unknownTransition: "unknown transition",
    unknownWorkflowType: "unknown",
    condition: "Condition",
    runTitle: "Agent workflow run",
    designTitle: "Agent workflow design",
    runDescription: "Durable workflow descriptor with the retained Run evidence overlaid.",
    designDescription: "Versioned design for the durable Agent workflow.",
    runMetadataDescription: (
      workflowType: string,
      workflowVersion: number,
      descriptorVersion: number,
      descriptorWorkflowVersion: number,
    ) =>
      `Workflow ${workflowType} v${workflowVersion} · descriptor v${descriptorVersion} for workflow v${descriptorWorkflowVersion}`,
    designMetadataDescription: (workflowVersion: number) => `Durable workflow v${workflowVersion}`,
    blindSpot: "The retained event window or an older Run version may be incomplete; missing events do not prove a phase did not execute.",
    versionMismatch: (runVersion: number, descriptorVersion: number) =>
      `Run workflow version ${runVersion} differs from descriptor workflow version ${descriptorVersion}; topology and phase coverage may be inaccurate.`,
  },
  zh: {
    waitBadge: "等待",
    terminalBadge: "终态",
    attemptBadge: (attempt: number) => `第 ${attempt} 次尝试`,
    checkpointBadge: "检查点",
    currentBadge: "当前阶段",
    responsibility: "职责",
    sourceFile: "源文件",
    symbol: "符号",
    executionStatus: "执行状态",
    latestEvent: "最新事件",
    result: "结果",
    error: "错误",
    openSource: "打开源码",
    unknownPhaseBadge: "未知阶段",
    unknownSummary: "此阶段不在可视化描述符 v1 中。",
    phaseId: "阶段 ID",
    unknownTransition: "未知转换",
    unknownWorkflowType: "未知",
    condition: "条件",
    runTitle: "Agent 工作流运行",
    designTitle: "Agent 工作流设计",
    runDescription: "叠加了保留 Run 证据的 durable 工作流描述符。",
    designDescription: "durable Agent 工作流的版本化设计。",
    runMetadataDescription: (
      workflowType: string,
      workflowVersion: number,
      descriptorVersion: number,
      descriptorWorkflowVersion: number,
    ) =>
      `工作流 ${workflowType} v${workflowVersion} · 描述符 v${descriptorVersion}，对应工作流 v${descriptorWorkflowVersion}`,
    designMetadataDescription: (workflowVersion: number) => `Durable 工作流 v${workflowVersion}`,
    blindSpot: "保留的事件窗口或旧版 Run 可能不完整；缺少事件不能证明某个阶段未执行。",
    versionMismatch: (runVersion: number, descriptorVersion: number) =>
      `Run 工作流版本 ${runVersion} 与描述符工作流版本 ${descriptorVersion} 不同；拓扑与阶段覆盖可能不准确。`,
  },
} as const;

const WORKFLOW_STATUS_ZH: Record<string, string> = {
  default: "默认",
  existing: "已存在",
  not_started: "未开始",
  running: "运行中",
  waiting: "等待中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  warning: "警告",
  error: "错误",
  observed: "已观察",
  declared: "已声明",
  unknown: "未知",
  needs_attention: "需要关注",
};

function workflowStatusDetail(status: GraphStatus, language: GraphSceneLanguage): string {
  return language === "zh" ? WORKFLOW_STATUS_ZH[status] ?? status : status;
}

export function workflowToGraph(
  runValue?: unknown,
  language: GraphSceneLanguage = "en",
): GraphDataset {
  const copy = WORKFLOW_COPY[language];
  const run = asRecord(runValue);
  const overlays = new Map<string, WorkflowOverlay>();
  const knownPhases = new Set(DURABLE_WORKFLOW_DESCRIPTOR.nodes.map((node) => node.id));
  const discoveredPhases = new Set<string>();

  const checkpoint = asRecord(run?.checkpoint);
  const checkpointState = asRecord(checkpoint?.state);
  const state = asRecord(run?.state);
  const currentPhase =
    asString(state?.phase)
    ?? asString(checkpointState?.phase)
    ?? asString(checkpoint?.phase);
  const checkpointPhase =
    asString(checkpoint?.last_step)
    ?? asString(checkpoint?.step_id);

  if (checkpointPhase) {
    discoveredPhases.add(checkpointPhase);
    overlays.set(checkpointPhase, {
      status: "succeeded",
      attempt: asFiniteNumber(checkpoint?.attempt),
      checkpoint: true,
    });
  }

  for (const step of asRecords(run?.steps)) {
    const id = phaseIdFromRecord(step);
    if (!id) continue;
    discoveredPhases.add(id);
    const prior = overlays.get(id) ?? {};
    const attempt = asFiniteNumber(step.attempt);
    if (
      prior.attempt !== undefined
      && attempt !== undefined
      && attempt < prior.attempt
    ) continue;
    overlays.set(id, {
      ...prior,
      status: normalizeRunStatus(step.status) ?? prior.status,
      attempt: attempt ?? prior.attempt,
      summary: asString(step.summary) ?? prior.summary,
      result: step.result ?? step.output ?? prior.result,
      error: step.error ?? prior.error,
    });
  }

  const events = asRecords(run?.events)
    .sort((left, right) =>
      (asFiniteNumber(left.sequence) ?? 0) - (asFiniteNumber(right.sequence) ?? 0));
  for (const event of events) {
    const id = phaseIdFromRecord(event);
    if (!id) continue;
    discoveredPhases.add(id);
    const prior = overlays.get(id) ?? {};
    const sequence = asFiniteNumber(event.sequence) ?? 0;
    if (prior.sequence !== undefined && sequence < prior.sequence) continue;
    const payload = asRecord(event.payload);
    overlays.set(id, {
      ...prior,
      status: eventStatus(event) ?? prior.status,
      attempt: asFiniteNumber(event.attempt) ?? prior.attempt,
      summary: asString(payload?.summary) ?? asString(event.summary) ?? prior.summary,
      result: payload?.result ?? event.result ?? prior.result,
      error: errorFromRecord(event) ?? prior.error,
      eventType: asString(event.type) ?? prior.eventType,
      sequence,
    });
  }

  if (currentPhase) {
    discoveredPhases.add(currentPhase);
    const prior = overlays.get(currentPhase) ?? {};
    overlays.set(currentPhase, {
      ...prior,
      status: normalizeRunStatus(run?.status) ?? prior.status ?? "running",
      current: true,
      error: run?.error ?? prior.error,
    });
  }

  const descriptorNodes: GraphNode[] = DURABLE_WORKFLOW_DESCRIPTOR.nodes.map((node) => {
    const localizedDescriptor = language === "zh" ? WORKFLOW_NODE_ZH[node.id] : undefined;
    const descriptorLabel = localizedDescriptor?.label ?? node.label;
    const descriptorResponsibility = localizedDescriptor?.responsibility ?? node.responsibility;
    const overlay = overlays.get(node.id);
    let status: GraphStatus = overlay?.status ?? "not_started";
    if (node.id === "done" && normalizeRunStatus(run?.status) === "succeeded") status = "succeeded";
    if (node.id === "failed" && normalizeRunStatus(run?.status) === "failed") status = "failed";
    if (node.id === "needs_attention" && asString(run?.status) === "needs_attention") {
      status = "needs_attention";
    }
    if (node.id === "cancelled" && normalizeRunStatus(run?.status) === "cancelled") status = "cancelled";
    const badges: string[] = [];
    if (node.kind === "wait") addBadge(badges, copy.waitBadge);
    if (node.kind === "terminal") addBadge(badges, copy.terminalBadge);
    if (overlay?.attempt !== undefined) addBadge(badges, copy.attemptBadge(overlay.attempt));
    if (overlay?.checkpoint) addBadge(badges, copy.checkpointBadge);
    if (overlay?.current) addBadge(badges, copy.currentBadge);
    return {
      id: `workflow:${node.id}`,
      label: descriptorLabel,
      kind: node.kind === "terminal" ? "workflow-terminal" : `workflow-${node.kind}`,
      status,
      summary: overlay?.summary ?? descriptorResponsibility,
      details: {
        [copy.responsibility]: descriptorResponsibility,
        [copy.sourceFile]: node.sourceFile,
        [copy.symbol]: node.symbol,
        [copy.executionStatus]: workflowStatusDetail(status, language),
        ...(overlay?.eventType ? { [copy.latestEvent]: overlay.eventType } : {}),
        ...(overlay?.result !== undefined ? { [copy.result]: overlay.result } : {}),
        ...(overlay?.error !== undefined ? { [copy.error]: overlay.error } : {}),
      },
      ...(badges.length > 0 ? { badges } : {}),
      action: { label: copy.openSource, href: `${GITHUB_SOURCE_ROOT}/${node.sourceFile}` },
    };
  });

  const unknownNodes: GraphNode[] = [...discoveredPhases]
    .filter((id) => !knownPhases.has(id))
    .sort()
    .map((id) => {
      const overlay = overlays.get(id);
      const badges: string[] = [copy.unknownPhaseBadge];
      if (overlay?.attempt !== undefined) badges.push(copy.attemptBadge(overlay.attempt));
      if (overlay?.checkpoint) badges.push(copy.checkpointBadge);
      if (overlay?.current) badges.push(copy.currentBadge);
      return {
        id: `workflow:${id}`,
        label: id.replaceAll("_", " "),
        kind: "workflow-unknown",
        status: overlay?.status ?? "unknown",
        summary: copy.unknownSummary,
        details: {
          [copy.phaseId]: id,
          [copy.executionStatus]: workflowStatusDetail(overlay?.status ?? "unknown", language),
          ...(overlay?.eventType ? { [copy.latestEvent]: overlay.eventType } : {}),
          ...(overlay?.result !== undefined ? { [copy.result]: overlay.result } : {}),
          ...(overlay?.error !== undefined ? { [copy.error]: overlay.error } : {}),
        },
        badges,
      };
    });

  const descriptorEdges: GraphEdge[] = DURABLE_WORKFLOW_DESCRIPTOR.edges.map((edge) => ({
    id: `workflow-edge:${edge.id}`,
    source: `workflow:${edge.source}`,
    target: `workflow:${edge.target}`,
    label: language === "zh" ? WORKFLOW_EDGE_ZH[edge.label] ?? edge.label : edge.label,
    kind: edge.kind,
    status: edge.kind === "rework" ? "warning" : "default",
    details: {
      [copy.condition]: language === "zh" ? WORKFLOW_EDGE_ZH[edge.label] ?? edge.label : edge.label,
    },
  }));

  if (unknownNodes.length > 0) {
    const source = currentPhase && knownPhases.has(currentPhase)
      ? `workflow:${currentPhase}`
      : "workflow:route";
    for (const node of unknownNodes) {
      if (node.id === source) continue;
      descriptorEdges.push({
        id: `workflow-edge:unknown:${node.id}`,
        source,
        target: node.id,
        label: copy.unknownTransition,
        kind: "condition",
        status: "warning",
      });
    }
  }

  const runError = run?.error;
  if (runError !== undefined && !currentPhase) {
    const failed = descriptorNodes.find((node) => node.id === "workflow:failed");
    if (failed) {
      failed.status = "failed";
      failed.details = { ...(failed.details ?? {}), [copy.error]: runError };
    }
  }
  const result = run?.result;
  if (result !== undefined) {
    const complete = descriptorNodes.find((node) => node.id === "workflow:done");
    if (complete) complete.details = { ...(complete.details ?? {}), [copy.result]: result };
  }
  const workflowType = asString(run?.workflow_type);
  const workflowVersion =
    asFiniteNumber(run?.workflow_version)
    ?? asFiniteNumber(state?.workflow_version)
    ?? DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion;
  const workflowVersionMismatch =
    Boolean(run) && workflowVersion !== DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion;

  return normalizeGraphDataset({
    version: GRAPH_DATASET_VERSION,
    title: run ? copy.runTitle : copy.designTitle,
    description: run
      ? copy.runDescription
      : copy.designDescription,
    nodes: [...descriptorNodes, ...unknownNodes],
    edges: descriptorEdges,
    metadata: {
      title: run ? copy.runTitle : copy.designTitle,
      description: run
        ? copy.runMetadataDescription(
            workflowType ?? copy.unknownWorkflowType,
            workflowVersion,
            DURABLE_WORKFLOW_DESCRIPTOR.version,
            DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion,
          )
        : copy.designMetadataDescription(DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion),
      truncated: false,
      blind_spots: run
        ? [copy.blindSpot]
        : [],
      limitations: workflowVersionMismatch
        ? [
            copy.versionMismatch(workflowVersion, DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion),
          ]
        : [],
      workflow_type: workflowType,
      workflow_version: workflowVersion,
      descriptor_version: DURABLE_WORKFLOW_DESCRIPTOR.version,
      descriptor_workflow_version: DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion,
      workflow_version_mismatch: workflowVersionMismatch,
    },
  });
}

const SAFE_DATA_MAP_DETAIL_KEYS = new Set([
  "category",
  "data_category",
  "data_categories",
  "stage",
  "target",
  "target_type",
  "source_category",
  "target_category",
  "provider",
  "provider_category",
  "capability",
  "capability_category",
  "capability_categories",
  "app_id",
  "semantic",
  "semantics",
  "reason",
  "count",
  "first_seen",
  "last_seen",
  "evidence_count",
  "declared_app_count",
  "instrumented",
  "coverage",
  "blind_spot",
]);

const DATA_MAP_DETAIL_KEYS_ZH: Record<string, string> = {
  category: "类别",
  data_category: "数据类别",
  data_categories: "数据类别",
  stage: "阶段",
  target: "目标",
  target_type: "目标类型",
  source_category: "来源类别",
  target_category: "目标类别",
  provider: "Provider",
  provider_category: "Provider 类别",
  capability: "能力",
  capability_category: "能力类别",
  capability_categories: "能力类别",
  app_id: "App ID",
  semantic: "语义",
  semantics: "语义",
  reason: "原因",
  count: "次数",
  first_seen: "首次观察时间",
  last_seen: "最近观察时间",
  evidence_count: "证据数",
  declared_app_count: "已声明 App 数",
  instrumented: "是否插桩",
  coverage: "覆盖范围",
  blind_spot: "盲区",
};

const DATA_MAP_SEMANTIC_VALUES_ZH: Record<string, string> = {
  observed: "已观察",
  declared: "已声明",
  unknown: "未知",
};

const DATA_MAP_REASON_VALUES_ZH: Record<string, string> = {
  "Audit evidence outside the retained window is unavailable.":
    "保留窗口之外的审计证据不可用。",
  "A possible flow without retained Audit instrumentation.":
    "可能存在但没有保留审计插桩的数据流。",
};

function safeDataMapDetails(
  value: unknown,
  language: GraphSceneLanguage,
): Record<string, unknown> | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const entries = Object.entries(record)
    .filter(([key]) => SAFE_DATA_MAP_DETAIL_KEYS.has(key.toLocaleLowerCase()))
    .map(([key, entryValue]) => {
      const normalizedKey = key.toLocaleLowerCase();
      let localizedValue = entryValue;
      if (language === "zh" && typeof entryValue === "string") {
        if (normalizedKey === "semantic" || normalizedKey === "semantics") {
          localizedValue = DATA_MAP_SEMANTIC_VALUES_ZH[entryValue] ?? entryValue;
        } else if (normalizedKey === "reason") {
          localizedValue = DATA_MAP_REASON_VALUES_ZH[entryValue] ?? entryValue;
        }
      }
      return [
        language === "zh" ? DATA_MAP_DETAIL_KEYS_ZH[normalizedKey] ?? key : key,
        localizedValue,
      ];
    });
  return entries.length > 0 ? Object.fromEntries(entries) : undefined;
}

const DATA_MAP_COPY = {
  en: {
    title: "Privacy data map",
    description: "Observed, declared, and unknown data-flow metadata without raw payloads.",
    localLabel: "Local runtime context",
    localSummary: "Local context category; raw values are intentionally omitted.",
    providerSummary: "Provider observed in retained Audit metadata.",
    appSummary: "Current App Manifest declaration.",
    categorySummary: "Schema category referenced by a current App Manifest.",
    capabilitySummary: "Capability category declared by a current App Manifest.",
    outsideRetentionLabel: "Outside retained window",
    outsideRetentionReason: "Audit evidence outside the retained window is unavailable.",
    uninstrumentedLabel: "Uninstrumented flow",
    uninstrumentedReason: "A possible flow without retained Audit instrumentation.",
    declaredInput: "declared input",
    declaredCapability: "declared capability",
    coverageUnknown: "coverage unknown",
    badges: {
      local: "local",
      observed: "observed",
      declared: "declared",
      unknown: "unknown",
    },
    blindSpots: {
      "Data flows outside the retained Audit Log window are not observable.":
        "Data flows outside the retained Audit Log window are not observable.",
      "Local or third-party operations without Audit instrumentation remain unknown.":
        "Local or third-party operations without Audit instrumentation remain unknown.",
      "Manifest declarations describe potential access, not runtime grants or observed use.":
        "Manifest declarations describe potential access, not runtime grants or observed use.",
    },
    limitations: {
      "This map is a request-time projection and does not extend Audit Log retention.":
        "This map is a request-time projection and does not extend Audit Log retention.",
      "Observed flows are aggregated only from retained provider, stage, and timestamp metadata.":
        "Observed flows are aggregated only from retained provider, stage, and timestamp metadata.",
      "The map is not an authorization, compliance, or proof-of-absence decision.":
        "The map is not an authorization, compliance, or proof-of-absence decision.",
    },
    semantics: {
      "Aggregated evidence in the retained Audit metadata window.":
        "Aggregated evidence in the retained Audit metadata window.",
      "Potential flow inferred from a current App Manifest only.":
        "Potential flow inferred from a current App Manifest only.",
      "Possible flow outside retention or instrumentation coverage.":
        "Possible flow outside retention or instrumentation coverage.",
    },
  },
  zh: {
    title: "隐私数据地图",
    description: "不含原始载荷的已观察、已声明及未知数据流元数据。",
    localLabel: "本地运行时上下文",
    localSummary: "本地上下文类别；有意省略原始值。",
    providerSummary: "在保留的审计元数据中观察到的 provider。",
    appSummary: "当前 App Manifest 声明。",
    categorySummary: "当前 App Manifest 引用的 Schema 类别。",
    capabilitySummary: "当前 App Manifest 声明的能力类别。",
    outsideRetentionLabel: "保留窗口之外",
    outsideRetentionReason: "保留窗口之外的审计证据不可用。",
    uninstrumentedLabel: "未插桩的数据流",
    uninstrumentedReason: "可能存在但没有保留审计插桩的数据流。",
    declaredInput: "已声明输入",
    declaredCapability: "已声明能力",
    coverageUnknown: "覆盖未知",
    badges: {
      local: "本地",
      observed: "已观察",
      declared: "已声明",
      unknown: "未知",
    },
    blindSpots: {
      "Data flows outside the retained Audit Log window are not observable.":
        "无法观察审计日志保留窗口之外的数据流。",
      "Local or third-party operations without Audit instrumentation remain unknown.":
        "缺少审计插桩的本地或第三方操作仍属于未知范围。",
      "Manifest declarations describe potential access, not runtime grants or observed use.":
        "Manifest 声明描述潜在访问，并不代表运行时授权或已观察到的使用。",
    },
    limitations: {
      "This map is a request-time projection and does not extend Audit Log retention.":
        "此地图是请求时投影，不会延长审计日志的保留期限。",
      "Observed flows are aggregated only from retained provider, stage, and timestamp metadata.":
        "已观察的数据流仅由保留的 provider、stage 和时间戳元数据聚合。",
      "The map is not an authorization, compliance, or proof-of-absence decision.":
        "此地图不作授权、合规或不存在数据流的证明。",
    },
    semantics: {
      "Aggregated evidence in the retained Audit metadata window.":
        "保留的审计元数据窗口中的聚合证据。",
      "Potential flow inferred from a current App Manifest only.":
        "仅从当前 App Manifest 推断的潜在数据流。",
      "Possible flow outside retention or instrumentation coverage.":
        "保留或插桩覆盖之外的可能数据流。",
    },
  },
} as const;

function translateKnown(
  value: unknown,
  translations: Readonly<Record<string, string>>,
): unknown {
  return typeof value === "string" ? translations[value] ?? value : value;
}

function translateKnownList(
  value: unknown,
  translations: Readonly<Record<string, string>>,
): unknown {
  return Array.isArray(value)
    ? value.map((item) => translateKnown(item, translations))
    : value;
}

const DATA_MAP_COVERAGE_KEYS_ZH: Record<string, string> = {
  evidence_count: "证据数",
  declared_app_count: "已声明 App 数",
  observed_flow_count: "已观察数据流数",
  declared_flow_count: "已声明数据流数",
  observed_stages: "已观察阶段",
  instrumentation: "插桩范围",
};

const DATA_MAP_INSTRUMENTATION_ZH: Record<string, string> = {
  retained_llm_provider_call_metadata: "保留的 LLM provider 调用元数据",
  current_app_manifest_declarations: "当前 App Manifest 声明",
};

function localizeDataMapCoverage(
  value: unknown,
  language: GraphSceneLanguage,
): unknown {
  const coverage = asRecord(value);
  if (!coverage || language === "en") return value;
  return Object.fromEntries(Object.entries(coverage).map(([key, item]) => [
    DATA_MAP_COVERAGE_KEYS_ZH[key] ?? key,
    key === "instrumentation" && Array.isArray(item)
      ? item.map((entry) =>
          typeof entry === "string" ? DATA_MAP_INSTRUMENTATION_ZH[entry] ?? entry : entry)
      : item,
  ]));
}

/**
 * Privacy maps are a deliberately narrow projection. The frontend repeats the
 * backend allow-list boundary so raw prompt/response/tool/provider payload
 * fields cannot accidentally enter the shared details panel.
 */
export function dataMapToGraph(
  value: unknown,
  language: GraphSceneLanguage = "en",
): GraphDataset {
  const copy = DATA_MAP_COPY[language];
  const root = asRecord(value) ?? {};
  const rawMetadata = asRecord(root.metadata) ?? {};
  const nodes = asRecords(root.nodes).map((node) => {
    const id = asString(node.id) ?? "";
    const kind = asString(node.kind);
    let label = node.label;
    let summary = node.summary;
    let knownReason: string | undefined;
    if (id === "data-source:local-runtime-context") {
      label = copy.localLabel;
      summary = copy.localSummary;
    } else if (kind === "provider" && node.summary === DATA_MAP_COPY.en.providerSummary) {
      summary = copy.providerSummary;
    } else if (kind === "app" && node.summary === DATA_MAP_COPY.en.appSummary) {
      summary = copy.appSummary;
    } else if (kind === "data_category" && node.summary === DATA_MAP_COPY.en.categorySummary) {
      summary = copy.categorySummary;
    } else if (kind === "capability" && node.summary === DATA_MAP_COPY.en.capabilitySummary) {
      summary = copy.capabilitySummary;
    } else if (id === "unknown:outside-retention") {
      label = copy.outsideRetentionLabel;
      summary = copy.outsideRetentionReason;
      knownReason = copy.outsideRetentionReason;
    } else if (id === "unknown:uninstrumented") {
      label = copy.uninstrumentedLabel;
      summary = copy.uninstrumentedReason;
      knownReason = copy.uninstrumentedReason;
    }
    const details = safeDataMapDetails(node.details, language);
    if (knownReason && details) details[language === "zh" ? "原因" : "reason"] = knownReason;
    const badges = Array.isArray(node.badges)
      ? node.badges.map((badge) =>
          typeof badge === "string"
            ? copy.badges[badge as keyof typeof copy.badges] ?? badge
            : badge)
      : node.badges;
    return {
      id: node.id,
      label,
      kind: node.kind,
      status: node.status,
      summary,
      badges,
      ...(details ? { details } : {}),
    };
  });
  const edges = asRecords(root.edges).map((edge) => {
    const id = asString(edge.id) ?? "";
    const label = id.startsWith("declared-schema:")
      ? copy.declaredInput
      : id.startsWith("declared-capability:")
        ? copy.declaredCapability
        : id.startsWith("unknown:")
          ? copy.coverageUnknown
          : edge.label;
    const details = safeDataMapDetails(edge.details, language);
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label,
      kind: edge.kind,
      status: edge.status,
      ...(details ? { details } : {}),
    };
  });
  const rawSemantics = asRecord(rawMetadata.semantics);
  const semantics = rawSemantics
    ? Object.fromEntries(Object.entries(rawSemantics).map(([key, item]) => [
        language === "zh" ? DATA_MAP_SEMANTIC_VALUES_ZH[key] ?? key : key,
        translateKnown(item, copy.semantics),
      ]))
    : undefined;
  return normalizeGraphDataset({
    version: root.version,
    title: copy.title,
    description: copy.description,
    nodes,
    edges,
    metadata: {
      title: copy.title,
      description: copy.description,
      counts: rawMetadata.counts,
      total_nodes: rawMetadata.total_nodes,
      returned_nodes: rawMetadata.returned_nodes,
      total_edges: rawMetadata.total_edges,
      returned_edges: rawMetadata.returned_edges,
      truncated: rawMetadata.truncated,
      time_window: rawMetadata.time_window,
      coverage: localizeDataMapCoverage(rawMetadata.coverage, language),
      blind_spots: translateKnownList(rawMetadata.blind_spots, copy.blindSpots),
      limitations: translateKnownList(rawMetadata.limitations, copy.limitations),
      semantics,
      evidence_count: rawMetadata.evidence_count,
      declared_app_count: rawMetadata.declared_app_count,
      generated_at: rawMetadata.generated_at,
    },
  });
}
