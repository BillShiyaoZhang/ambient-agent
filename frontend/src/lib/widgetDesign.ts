import type { OntologyProposalEntity } from "./ontology";

export interface WidgetSchemaProposal {
  reused_schemas: Array<{
    id: string;
    reason: string;
    extended_properties: Record<string, string>;
    data_scope?: "user_context";
  }>;
  new_schemas: OntologyProposalEntity[];
  capabilities: Array<{
    id: string;
    scope: Record<string, unknown>;
  }>;
  required_features?: Array<{
    id: string;
    description: string;
    capability_ids: string[];
    network_sources: Array<{ source_id: string; path: string }>;
  }>;
  capability_changes?: {
    added: Array<{ id: string; scope: Record<string, unknown> }>;
    changed: Array<{ before: { id: string; scope: Record<string, unknown> }; after: { id: string; scope: Record<string, unknown> } }>;
    removed: Array<{ id: string; scope: Record<string, unknown> }>;
  };
  baseline_capabilities?: Array<{ id: string; scope: Record<string, unknown> }>;
}

const GRAPH_CAPABILITIES = new Set(["graph.query", "graph.mutate"]);

function cloneProposal(proposal: WidgetSchemaProposal): WidgetSchemaProposal {
  return JSON.parse(JSON.stringify(proposal)) as WidgetSchemaProposal;
}

/**
 * Keep Graph grant references aligned when a user renames or removes an
 * editable ontology entity. This function only narrows or renames existing
 * references; it never adds a new grant.
 */
export function reconcileProposalGraphEntity(
  proposal: WidgetSchemaProposal,
  previousId: string,
  nextId: string | null,
): WidgetSchemaProposal {
  const updated = cloneProposal(proposal);
  updated.capabilities = updated.capabilities.flatMap((grant) => {
    if (!GRAPH_CAPABILITIES.has(grant.id) || !Array.isArray(grant.scope.entities)) {
      return [grant];
    }
    const entities = (grant.scope.entities as unknown[])
      .filter((entity): entity is string => typeof entity === "string")
      .flatMap((entity) => {
        if (entity !== previousId) return [entity];
        return nextId === null ? [] : [nextId];
      });
    const uniqueEntities = [...new Set(entities)];
    if (uniqueEntities.length === 0) return [];
    return [{ ...grant, scope: { ...grant.scope, entities: uniqueEntities } }];
  });
  if (updated.baseline_capabilities) {
    const before = new Map(updated.baseline_capabilities.map((grant) => [grant.id, grant]));
    const after = new Map(updated.capabilities.map((grant) => [grant.id, grant]));
    updated.capability_changes = {
      added: updated.capabilities.filter((grant) => !before.has(grant.id)),
      removed: updated.baseline_capabilities.filter((grant) => !after.has(grant.id)),
      changed: updated.capabilities.filter((grant) => before.has(grant.id) && JSON.stringify(before.get(grant.id)) !== JSON.stringify(grant))
        .map((grant) => ({ before: before.get(grant.id)!, after: grant })),
    };
  }
  return updated;
}

export function schemaProposalDependencyErrors(proposal: WidgetSchemaProposal): string[] {
  const schemaIds = [
    ...proposal.reused_schemas.map((schema) => schema.id.trim()),
    ...proposal.new_schemas.map((schema) => schema.id.trim()),
  ];
  const errors: string[] = [];
  if (schemaIds.some((schemaId) => !schemaId)) {
    errors.push("Every schema entity must have a non-empty ID.");
  }
  const duplicates = [...new Set(schemaIds.filter((schemaId, index) => schemaIds.indexOf(schemaId) !== index))];
  if (duplicates.length > 0) {
    errors.push(`Duplicate schema entities: ${duplicates.join(", ")}`);
  }

  const approvedEntityIds = new Set(schemaIds.filter(Boolean));
  for (const grant of proposal.capabilities) {
    if (!GRAPH_CAPABILITIES.has(grant.id) || !Array.isArray(grant.scope.entities)) continue;
    const unknown = [...new Set(
      (grant.scope.entities as unknown[])
        .filter((entity): entity is string => typeof entity === "string")
        .filter((entity) => !approvedEntityIds.has(entity)),
    )].sort();
    if (unknown.length > 0) {
      errors.push(`${grant.id} references entities not present in the schema proposal: ${unknown.join(", ")}`);
    }
  }
  const grantIds = new Set(proposal.capabilities.map((grant) => grant.id));
  for (const feature of proposal.required_features || []) {
    for (const capabilityId of feature.capability_ids) {
      if (!grantIds.has(capabilityId)) errors.push(`${feature.id} requires the missing capability ${capabilityId}`);
    }
  }
  return errors;
}
