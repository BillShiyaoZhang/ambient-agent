export interface LocalizedSlashText {
  zh: string;
  en: string;
}

export interface SlashCommandOption {
  value: string;
  label: string;
  description?: string;
  keywords?: string[];
  disabled?: boolean;
  status?: string;
}

export interface SlashCommandArgument {
  name: string;
  kind: "text" | "choice";
  required: boolean;
  label: LocalizedSlashText;
  description: LocalizedSlashText;
  option_source?: string;
  options?: SlashCommandOption[];
}

export interface SlashCommandDefinition {
  name: string;
  intent_kind: string;
  label: LocalizedSlashText;
  description: LocalizedSlashText;
  arguments: SlashCommandArgument[];
}

export interface SlashCommandCatalog {
  version: 1;
  max_commands: number;
  commands: SlashCommandDefinition[];
}

export type SlashSuggestionKind = "command" | "option";

export interface SlashSuggestion {
  id: string;
  kind: SlashSuggestionKind;
  title: string;
  description: string;
  value: string;
  meta: string;
  disabled: boolean;
  replaceStart: number;
  replaceEnd: number;
  insertText: string;
}

export interface SlashSuggestionContext {
  key: string;
  activationKey: string;
  kind: SlashSuggestionKind;
  commandName?: string;
  suggestions: SlashSuggestion[];
}

const localized = (zh: string, en: string): LocalizedSlashText => ({ zh, en });
const instruction = {
  name: "instruction",
  kind: "text" as const,
  required: true,
  label: localized("指令", "Instruction"),
  description: localized("用自然语言描述要完成的事情", "Describe what should be done"),
};

export const FALLBACK_SLASH_COMMAND_CATALOG: SlashCommandCatalog = {
  version: 1,
  max_commands: 8,
  commands: [
    {
      name: "ask",
      intent_kind: "converse",
      label: localized("直接询问", "Ask directly"),
      description: localized("进入只读对话与推理路径", "Use the read-only conversation path"),
      arguments: [instruction],
    },
    {
      name: "app",
      intent_kind: "widget_modify",
      label: localized("修改 App", "Modify App"),
      description: localized("指定已有 App 并进入完整开发流程", "Target an existing App"),
      arguments: [{
        name: "app_id",
        kind: "choice",
        required: true,
        label: localized("App ID", "App ID"),
        description: localized("从全部已安装 App 中选择", "Choose from every installed App"),
        option_source: "apps",
        options: [],
      }, instruction],
    },
    {
      name: "create",
      intent_kind: "widget_create",
      label: localized("创建 App", "Create App"),
      description: localized("使用明确的新 App ID 创建应用", "Create an App with a new ID"),
      arguments: [{
        name: "app_id",
        kind: "text",
        required: true,
        label: localized("新 App ID", "New App ID"),
        description: localized("输入新的小写 kebab-case ID", "Enter a new lowercase kebab-case ID"),
      }, instruction],
    },
    {
      name: "query",
      intent_kind: "graph_query",
      label: localized("查询 Graph", "Query Graph"),
      description: localized("约束到只读 Graph 查询", "Constrain the request to a read-only Graph query"),
      arguments: [instruction],
    },
    {
      name: "mutate",
      intent_kind: "graph_mutation",
      label: localized("修改 Graph", "Mutate Graph"),
      description: localized("预览并确认 Graph 变更", "Preview and approve a Graph mutation"),
      arguments: [instruction],
    },
    {
      name: "skill",
      intent_kind: "converse",
      label: localized("使用 Skill", "Use Skill"),
      description: localized("显式选择已安装 Skill", "Explicitly select an installed Skill"),
      arguments: [{
        name: "skill_id",
        kind: "choice",
        required: true,
        label: localized("Skill ID", "Skill ID"),
        description: localized("从全部已安装 Skill 中选择", "Choose from every installed Skill"),
        option_source: "skills",
        options: [],
      }, instruction],
    },
  ],
};

export function slashText(
  value: LocalizedSlashText | undefined,
  language: "zh" | "en",
): string {
  return value?.[language] ?? value?.en ?? value?.zh ?? "";
}

export async function loadSlashCommandCatalog(apiBase: string): Promise<SlashCommandCatalog> {
  const response = await fetch(`${apiBase}/api/chat/commands`, { cache: "no-store" });
  const body = await response.json().catch(() => null);
  if (
    !response.ok
    || body?.version !== 1
    || !Array.isArray(body.commands)
    || !body.commands.every((command: unknown) => (
      typeof command === "object"
      && command !== null
      && typeof (command as SlashCommandDefinition).name === "string"
      && Array.isArray((command as SlashCommandDefinition).arguments)
    ))
  ) {
    throw new Error(`Unable to load slash commands (${response.status})`);
  }
  return body as SlashCommandCatalog;
}

function normalizedSearch(value: string): string {
  return value.trim().toLocaleLowerCase();
}

function relevance(haystacks: string[], query: string): number {
  if (!query) return 1;
  const normalized = haystacks.map((value) => value.toLocaleLowerCase());
  if (normalized.some((value) => value === query)) return 4;
  if (normalized.some((value) => value.startsWith(query))) return 3;
  if (normalized.some((value) => value.includes(query))) return 2;
  return 0;
}

function lastSlashToken(value: string, cursor: number): {
  slashStart: number;
  name: string;
  nameEnd: number;
} | null {
  const before = value.slice(0, cursor);
  const pattern = /(^|\s)\/([^\s/]*)/g;
  let found: RegExpExecArray | null = null;
  for (const match of before.matchAll(pattern)) {
    const slashStart = (match.index ?? 0) + match[1].length;
    if (slashStart > 0 && value[slashStart - 1] === "\\") continue;
    found = match as RegExpExecArray;
  }
  if (!found) return null;
  const slashStart = (found.index ?? 0) + found[1].length;
  const name = found[2];
  return { slashStart, name, nameEnd: slashStart + 1 + name.length };
}

export function slashSuggestionContext(
  value: string,
  cursor: number,
  catalog: SlashCommandCatalog,
  language: "zh" | "en",
): SlashSuggestionContext | null {
  const token = lastSlashToken(value, cursor);
  if (!token) return null;
  const tailBeforeCursor = value.slice(token.nameEnd, cursor);
  const command = catalog.commands.find(
    (candidate) => candidate.name.toLocaleLowerCase() === token.name.toLocaleLowerCase(),
  );

  if (!tailBeforeCursor) {
    const query = normalizedSearch(token.name);
    const scored = catalog.commands
      .map((candidate, index) => ({
        candidate,
        index,
        score: relevance([
          candidate.name,
          slashText(candidate.label, language),
          slashText(candidate.description, language),
        ], query),
      }))
      .filter((entry) => entry.score > 0)
      .sort((left, right) => right.score - left.score || left.index - right.index);
    const nameMatches = query
      ? scored.filter(({ candidate }) => candidate.name.toLocaleLowerCase().startsWith(query))
      : [];
    const ranked = nameMatches.length ? nameMatches : scored;
    return {
      key: `command:${token.slashStart}:${query}`,
      activationKey: `command:${token.slashStart}`,
      kind: "command",
      suggestions: ranked.map(({ candidate }) => ({
        id: `slash-command-${candidate.name}`,
        kind: "command",
        title: `/${candidate.name}`,
        description: slashText(candidate.description, language),
        value: candidate.name,
        meta: slashText(candidate.label, language),
        disabled: false,
        replaceStart: token.slashStart,
        replaceEnd: token.nameEnd,
        insertText: `/${candidate.name} `,
      })),
    };
  }

  if (!command || !/^\s/.test(tailBeforeCursor)) return null;
  const firstArgument = command.arguments[0];
  if (!firstArgument || firstArgument.kind !== "choice") return null;

  const whitespace = tailBeforeCursor.match(/^\s*/)?.[0] ?? "";
  const argumentStart = token.nameEnd + whitespace.length;
  let argumentEnd = argumentStart;
  while (argumentEnd < value.length && !/\s/.test(value[argumentEnd])) argumentEnd += 1;
  if (cursor > argumentEnd) return null;

  const query = normalizedSearch(value.slice(argumentStart, cursor));
  const ranked = (firstArgument.options ?? [])
    .map((option, index) => ({
      option,
      index,
      score: relevance([
        option.value,
        option.label,
        option.description ?? "",
        ...(option.keywords ?? []),
      ], query),
    }))
    .filter((entry) => entry.score > 0)
    .sort((left, right) => right.score - left.score || left.index - right.index);
  return {
    key: `option:${token.slashStart}:${command.name}:${query}`,
    activationKey: `option:${token.slashStart}:${command.name}`,
    kind: "option",
    commandName: command.name,
    suggestions: ranked.map(({ option }, index) => ({
      id: `slash-option-${command.name}-${index}-${option.value.replace(/[^a-z0-9_-]/gi, "-")}`,
      kind: "option",
      title: option.label,
      description: option.description ?? "",
      value: option.value,
      meta: option.value,
      disabled: Boolean(option.disabled),
      replaceStart: argumentStart,
      replaceEnd: argumentEnd,
      insertText: `${option.value} `,
    })),
  };
}

export function applySlashSuggestion(
  value: string,
  suggestion: SlashSuggestion,
): { value: string; cursor: number } {
  const next = (
    value.slice(0, suggestion.replaceStart)
    + suggestion.insertText
    + value.slice(suggestion.replaceEnd)
  );
  return {
    value: next,
    cursor: suggestion.replaceStart + suggestion.insertText.length,
  };
}
