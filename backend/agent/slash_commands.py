"""Shared slash-command grammar and discovery metadata.

Slash commands are routing directives, not a second execution surface.  The
router compiles them into ``IntentPlan`` objects and the durable workflow keeps
ownership of preflight, approval, execution, and recovery.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


MAX_SLASH_COMMANDS = 8


@dataclass(frozen=True, slots=True)
class SlashCommandArgument:
    name: str
    kind: str
    required: bool
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    option_source: str | None = None

    def to_dict(self, *, options: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "required": self.required,
            "label": {"zh": self.label_zh, "en": self.label_en},
            "description": {
                "zh": self.description_zh,
                "en": self.description_en,
            },
        }
        if self.option_source:
            payload["option_source"] = self.option_source
            payload["options"] = list(options or [])
        return payload


@dataclass(frozen=True, slots=True)
class SlashCommandSpec:
    name: str
    intent_kind: str
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    arguments: tuple[SlashCommandArgument, ...]

    def to_dict(self, *, option_sets: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return {
            "name": self.name,
            "intent_kind": self.intent_kind,
            "label": {"zh": self.label_zh, "en": self.label_en},
            "description": {
                "zh": self.description_zh,
                "en": self.description_en,
            },
            "arguments": [
                argument.to_dict(
                    options=option_sets.get(argument.option_source or ""),
                )
                for argument in self.arguments
            ],
        }


_INSTRUCTION = SlashCommandArgument(
    name="instruction",
    kind="text",
    required=True,
    label_zh="指令",
    label_en="Instruction",
    description_zh="用自然语言描述要完成的事情",
    description_en="Describe what should be done in natural language",
)
_APP_ID = SlashCommandArgument(
    name="app_id",
    kind="choice",
    required=True,
    label_zh="App ID",
    label_en="App ID",
    description_zh="从全部已安装 App 中选择",
    description_en="Choose from every installed App",
    option_source="apps",
)
_NEW_APP_ID = SlashCommandArgument(
    name="app_id",
    kind="text",
    required=True,
    label_zh="新 App ID",
    label_en="New App ID",
    description_zh="输入新的小写 kebab-case ID",
    description_en="Enter a new lowercase kebab-case ID",
)
_SKILL_ID = SlashCommandArgument(
    name="skill_id",
    kind="choice",
    required=True,
    label_zh="Skill ID",
    label_en="Skill ID",
    description_zh="从全部已安装 Skill 中选择",
    description_en="Choose from every installed Skill",
    option_source="skills",
)


SLASH_COMMAND_SPECS: tuple[SlashCommandSpec, ...] = (
    SlashCommandSpec(
        name="ask",
        intent_kind="converse",
        label_zh="直接询问",
        label_en="Ask directly",
        description_zh="明确进入只读对话与推理路径",
        description_en="Explicitly use the read-only conversation path",
        arguments=(_INSTRUCTION,),
    ),
    SlashCommandSpec(
        name="app",
        intent_kind="widget_modify",
        label_zh="修改 App",
        label_en="Modify App",
        description_zh="指定已有 App，并进入规划、确认、生成与校验流程",
        description_en="Target an existing App and use its plan, approval, generation, and verification flow",
        arguments=(_APP_ID, _INSTRUCTION),
    ),
    SlashCommandSpec(
        name="create",
        intent_kind="widget_create",
        label_zh="创建 App",
        label_en="Create App",
        description_zh="使用明确的新 App ID 创建应用",
        description_en="Create an App with an explicit new App ID",
        arguments=(_NEW_APP_ID, _INSTRUCTION),
    ),
    SlashCommandSpec(
        name="query",
        intent_kind="graph_query",
        label_zh="查询 Graph",
        label_en="Query Graph",
        description_zh="把自然语言问题约束到只读 Graph 查询",
        description_en="Constrain a natural-language request to a read-only Graph query",
        arguments=(_INSTRUCTION,),
    ),
    SlashCommandSpec(
        name="mutate",
        intent_kind="graph_mutation",
        label_zh="修改 Graph",
        label_en="Mutate Graph",
        description_zh="生成 Graph 变更预览，并在确认后原子提交",
        description_en="Prepare a Graph mutation preview and apply it atomically after approval",
        arguments=(_INSTRUCTION,),
    ),
    SlashCommandSpec(
        name="skill",
        intent_kind="converse",
        label_zh="使用 Skill",
        label_en="Use Skill",
        description_zh="显式选择已安装 Skill，并保持其原有只读安全边界",
        description_en="Explicitly select an installed Skill while preserving its read-only safety boundary",
        arguments=(_SKILL_ID, _INSTRUCTION),
    ),
)

_SPEC_BY_NAME = {spec.name: spec for spec in SLASH_COMMAND_SPECS}
_COMMAND_NAMES_PATTERN = "|".join(
    re.escape(name) for name in sorted(_SPEC_BY_NAME, key=len, reverse=True)
)
_COMMAND_PATTERN = re.compile(
    rf"(?<![\\\S])/(?P<name>{_COMMAND_NAMES_PATTERN})(?=\s|$)",
    re.IGNORECASE,
)


class SlashCommandParseError(ValueError):
    """The user supplied more slash directives than one Run may contain."""


@dataclass(frozen=True, slots=True)
class ParsedSlashCommand:
    name: str
    arguments: dict[str, str]
    start: int
    end: int


def _restore_escaped_slashes(value: str) -> str:
    return value.replace(r"\/", "/")


def parse_slash_commands(content: str) -> list[ParsedSlashCommand]:
    """Parse every recognized slash command in source order.

    A command may appear at the start of the message or after whitespace.
    ``\\/app`` keeps a literal slash in instruction text.  Unknown slash words
    remain ordinary text and therefore cannot accidentally create a new route.
    """

    if not isinstance(content, str) or not content:
        return []
    matches = list(_COMMAND_PATTERN.finditer(content))
    if not matches:
        return []
    if len(matches) > MAX_SLASH_COMMANDS:
        raise SlashCommandParseError(
            f"At most {MAX_SLASH_COMMANDS} slash commands may be used in one message"
        )

    parsed: list[ParsedSlashCommand] = []
    prefix = _restore_escaped_slashes(content[: matches[0].start()].strip())
    if prefix:
        parsed.append(
            ParsedSlashCommand(
                name="ask",
                arguments={"instruction": prefix},
                start=0,
                end=matches[0].start(),
            )
        )

    for index, match in enumerate(matches):
        name = match.group("name").casefold()
        spec = _SPEC_BY_NAME[name]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = _restore_escaped_slashes(content[match.end() : end].strip())
        arguments: dict[str, str] = {}
        if spec.arguments and spec.arguments[0].name in {"app_id", "skill_id"}:
            parts = body.split(None, 1)
            arguments[spec.arguments[0].name] = parts[0].strip() if parts else ""
            if len(spec.arguments) > 1:
                arguments[spec.arguments[1].name] = parts[1].strip() if len(parts) > 1 else ""
        elif spec.arguments:
            arguments[spec.arguments[0].name] = body
        parsed.append(
            ParsedSlashCommand(
                name=name,
                arguments=arguments,
                start=match.start(),
                end=end,
            )
        )
    if len(parsed) > MAX_SLASH_COMMANDS:
        raise SlashCommandParseError(
            f"At most {MAX_SLASH_COMMANDS} slash commands may be used in one message"
        )
    return parsed


def explicit_skill_ids(content: str) -> list[str]:
    return [
        command.arguments.get("skill_id", "")
        for command in parse_slash_commands(content)
        if command.name == "skill" and command.arguments.get("skill_id")
    ]


def _app_options(apps: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for app in apps:
        app_id = str(app.get("id") or "").strip()
        if not app_id:
            continue
        title = str(app.get("title") or app_id).strip()
        options.append(
            {
                "value": app_id,
                "label": title,
                "description": str(app.get("description") or ""),
                "keywords": list(app.get("intents") or []),
                "disabled": False,
            }
        )
    return sorted(options, key=lambda item: (item["label"].casefold(), item["value"]))


def _skill_options(skills: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for skill in skills:
        catalog_id = str(skill.get("catalog_id") or "").strip()
        if not catalog_id:
            continue
        available = bool(skill.get("available", False))
        options.append(
            {
                "value": catalog_id,
                "label": str(skill.get("title") or skill.get("name") or catalog_id),
                "description": str(skill.get("description") or ""),
                "keywords": [
                    str(skill.get("name") or ""),
                    str(skill.get("market_id") or ""),
                    *[str(tag) for tag in skill.get("tags") or []],
                ],
                "disabled": not available,
                "status": str(skill.get("status") or ("ready" if available else "unavailable")),
            }
        )
    return sorted(options, key=lambda item: (item["label"].casefold(), item["value"]))


def build_slash_command_catalog(
    *,
    apps: Iterable[Mapping[str, Any]],
    skills: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return all command and dynamic ID options in one no-cache response."""

    option_sets = {
        "apps": _app_options(apps),
        "skills": _skill_options(skills),
    }
    return {
        "version": 1,
        "max_commands": MAX_SLASH_COMMANDS,
        "commands": [
            spec.to_dict(option_sets=option_sets)
            for spec in SLASH_COMMAND_SPECS
        ],
    }
