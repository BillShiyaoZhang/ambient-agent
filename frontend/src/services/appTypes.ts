export interface AppTypeText {
  zh: string;
  en: string;
}

export type AppFeatureStatus = "implemented" | "partial" | "planned";
export type AppFeatureSurface = "data" | "tools" | "ui";

export interface AppFeatureDeclaration {
  id: string;
  status: AppFeatureStatus;
  surfaces: AppFeatureSurface[];
  notes?: string;
}

export interface AppSpec {
  spec_version: number;
  types: string[];
  features: AppFeatureDeclaration[];
}

export interface AppTypeDefinition {
  id: string;
  title: AppTypeText;
  description: AppTypeText;
  features: Array<{ id: string; title: AppTypeText }>;
}

export interface AppTypeCatalog {
  spec_version: number;
  types: AppTypeDefinition[];
}

export function appTypeTitle(id: string, catalog: AppTypeCatalog | undefined, language: "zh" | "en"): string {
  return catalog?.types.find((type) => type.id === id)?.title[language] ?? id;
}

export function appTypeSearchText(spec: AppSpec | null | undefined, catalog: AppTypeCatalog | undefined): string[] {
  if (!spec) return [];
  const definitions = catalog?.types ?? [];
  const features = definitions.flatMap((type) => type.features);
  return [
    ...spec.types.flatMap((id) => {
      const type = definitions.find((definition) => definition.id === id);
      return type ? [id, type.title.zh, type.title.en, type.description.zh, type.description.en] : [id];
    }),
    ...spec.features.flatMap((declaration) => {
      const feature = features.find((definition) => definition.id === declaration.id);
      return feature ? [declaration.id, feature.title.zh, feature.title.en] : [declaration.id];
    }),
  ];
}

export interface AppFeatureView {
  id: string;
  title: string;
  status: AppFeatureStatus | "not_declared";
  surfaces: AppFeatureSurface[];
  notes?: string;
}

export function appFeatureViews(spec: AppSpec, catalog: AppTypeCatalog | undefined, language: "zh" | "en"): AppFeatureView[] {
  const declared = new Map(spec.features.map((feature) => [feature.id, feature]));
  const standards = spec.types.flatMap((id) => catalog?.types.find((type) => type.id === id)?.features ?? []);
  const standardIds = new Set(standards.map((feature) => feature.id));
  return [
    ...standards.map((feature) => ({
      id: feature.id,
      title: feature.title[language],
      status: declared.get(feature.id)?.status ?? "not_declared" as const,
      surfaces: declared.get(feature.id)?.surfaces ?? [],
      notes: declared.get(feature.id)?.notes,
    })),
    ...spec.features.filter((feature) => !standardIds.has(feature.id)).map((feature) => ({ ...feature, title: feature.id })),
  ];
}
