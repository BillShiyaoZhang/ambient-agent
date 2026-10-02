import { describe, expect, it } from "vitest";

import {
  reconcileProposalGraphEntity,
  schemaProposalDependencyErrors,
  type WidgetSchemaProposal,
} from "../../frontend/src/lib/widgetDesign";

function proposal(): WidgetSchemaProposal {
  return {
    reused_schemas: [
      { id: "Place", reason: "Location", extended_properties: {} },
    ],
    new_schemas: [
      {
        id: "WeatherObservation",
        name: "Weather observation",
        description: "Weather facts",
        properties: { temperature: "number" },
        subclass_of: "Thing",
        ontology_iri: "urn:ambient:ontology:WeatherObservation",
        equivalent_to: [],
        data_scope: "user_context",
      },
    ],
    capabilities: [
      {
        id: "graph.query",
        scope: { entities: ["Place", "WeatherObservation"] },
      },
      {
        id: "graph.mutate",
        scope: { entities: ["WeatherObservation"], operations: ["create"] },
      },
      {
        id: "network.request",
        scope: { sources: { forecast: { paths: ["/v1/forecast"] } } },
      },
    ],
  };
}

describe("widget design dependency helpers", () => {
  it("recomputes the visible permission diff after a schema removal", () => {
    const original = proposal();
    const updated = reconcileProposalGraphEntity({ ...original, baseline_capabilities: original.capabilities }, "WeatherObservation", null);
    expect(updated.capability_changes?.removed.map((grant) => grant.id)).toEqual(["graph.mutate"]);
    expect(updated.capability_changes?.changed[0].after.scope.entities).toEqual(["Place"]);
    expect(updated.capability_changes?.added).toEqual([]);
  });

  it("reports required dependencies removed from an edited proposal", () => {
    expect(schemaProposalDependencyErrors({ reused_schemas: [], new_schemas: [], capabilities: [], required_features: [{
      id: "custom:weather.location", description: "当前位置", capability_ids: ["device.location"], network_sources: []
    }] })).toEqual(["custom:weather.location requires the missing capability device.location"]);
  });
  it("renames Graph grant references with an edited schema entity", () => {
    const updated = reconcileProposalGraphEntity(
      proposal(),
      "WeatherObservation",
      "WeatherSnapshot",
    );

    expect(updated.capabilities[0].scope.entities).toEqual(["Place", "WeatherSnapshot"]);
    expect(updated.capabilities[1].scope.entities).toEqual(["WeatherSnapshot"]);
    expect(proposal().capabilities[0].scope.entities).toEqual(["Place", "WeatherObservation"]);
    expect(schemaProposalDependencyErrors({
      ...updated,
      new_schemas: [{ ...updated.new_schemas[0], id: "WeatherSnapshot" }],
    })).toEqual([]);
  });

  it("removes deleted entities from Graph grants and drops empty grants", () => {
    const updated = reconcileProposalGraphEntity(
      { ...proposal(), new_schemas: [] },
      "WeatherObservation",
      null,
    );

    expect(updated.capabilities.map((grant) => grant.id)).toEqual([
      "graph.query",
      "network.request",
    ]);
    expect(updated.capabilities[0].scope.entities).toEqual(["Place"]);
    expect(schemaProposalDependencyErrors(updated)).toEqual([]);
  });

  it("reports stale Graph grant references before approval", () => {
    const invalid = { ...proposal(), new_schemas: [] };
    expect(schemaProposalDependencyErrors(invalid)).toEqual([
      "graph.query references entities not present in the schema proposal: WeatherObservation",
      "graph.mutate references entities not present in the schema proposal: WeatherObservation",
    ]);
  });
});
