export interface OntologyProposalEntity {
  id: string;
  name: string;
  description: string;
  properties: Record<string, string>;
  subclass_of: string;
  ontology_iri: string;
  equivalent_to: string[];
  data_scope: "user_context";
}

export function createCustomOntologyEntity(existingIds: Iterable<string>): OntologyProposalEntity {
  const existing = new Set(existingIds);
  let counter = 1;
  let id = `CustomEntity${counter}`;
  while (existing.has(id)) {
    counter += 1;
    id = `CustomEntity${counter}`;
  }
  return {
    id,
    name: `Custom Entity ${counter}`,
    description: "Custom user-context entity",
    properties: { title: "string" },
    subclass_of: "Thing",
    ontology_iri: `urn:ambient:ontology:${id}`,
    equivalent_to: [],
    data_scope: "user_context",
  };
}

export function parseEquivalentOntologyIris(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}
