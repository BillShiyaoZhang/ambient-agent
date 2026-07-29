interface SkillOutputMessage {
  context_policy?: string;
  provenance?: {
    kind?: string;
    skills?: Array<{
      catalog_id?: string;
      name?: string;
      principal_id?: string;
    }>;
  } | null;
}

export function externalSkillMessageLabel(
  message: SkillOutputMessage,
  isZh: boolean,
): string | null {
  if (
    message.context_policy !== "display_only"
    || message.provenance?.kind !== "external_skill_output"
  ) {
    return null;
  }
  const names = Array.from(new Set(
    (message.provenance.skills ?? [])
      .map((skill) => {
        if (typeof skill.name === "string" && skill.name.trim()) {
          return skill.name.trim();
        }
        if (typeof skill.catalog_id !== "string") return "";
        return skill.catalog_id.split(":").pop()?.trim() ?? "";
      })
      .filter(Boolean),
  )).slice(0, 3);
  const boundary = isZh ? "外部 Skill 沙盒" : "External Skill sandbox";
  return names.length ? `${boundary} · ${names.join(", ")}` : boundary;
}
