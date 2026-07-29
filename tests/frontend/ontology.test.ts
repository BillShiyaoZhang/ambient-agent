import { describe, expect, it } from "vitest";

import {
  createCustomOntologyEntity,
  parseEquivalentOntologyIris,
} from "../../frontend/src/lib/ontology";

describe("ontology proposal helpers", () => {
  it("creates an aligned user-context entity with a unique id", () => {
    const entity = createCustomOntologyEntity(["CustomEntity1", "CustomEntity2"]);

    expect(entity.id).toBe("CustomEntity3");
    expect(entity.subclass_of).toBe("Thing");
    expect(entity.ontology_iri).toBe("urn:ambient:ontology:CustomEntity3");
    expect(entity.equivalent_to).toEqual([]);
    expect(entity.data_scope).toBe("user_context");
  });

  it("normalizes and deduplicates equivalent ontology IRIs", () => {
    expect(
      parseEquivalentOntologyIris(
        " https://schema.org/Action, https://example.org/Habit, https://schema.org/Action ",
      ),
    ).toEqual(["https://schema.org/Action", "https://example.org/Habit"]);
  });
});
