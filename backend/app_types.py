"""Versioned App classifications and author-declared functionality.

These declarations describe an implementation. They neither grant runtime
access nor certify that an implementation behaves correctly.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

APP_SPEC_VERSION = 1
MAX_APP_TYPES = 20
MAX_APP_FEATURES = 100
MAX_SPEC_ID_LENGTH = 200
MAX_FEATURE_NOTES_LENGTH = 2000

_CUSTOM_TYPE = re.compile(r"^custom:([a-z0-9]+(?:-[a-z0-9]+)*)$")
_CUSTOM_FEATURE = re.compile(r"^custom:([a-z0-9]+(?:-[a-z0-9]+)*)\.([a-z0-9]+(?:-[a-z0-9]+)*)$")
_STATUSES = {"implemented", "partial", "planned"}
_SURFACES = {"data", "tools", "ui"}


def _localized(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def _feature(app_type: str, feature: str, zh: str, en: str, zh_description: str, en_description: str) -> dict[str, Any]:
    return {
        "id": f"{app_type}.{feature}",
        "title": _localized(zh, en),
        "description": _localized(zh_description, en_description),
    }


def _type(
    type_id: str,
    zh: str,
    en: str,
    zh_description: str,
    en_description: str,
    features: list[tuple[str, str, str, str, str]],
) -> dict[str, Any]:
    return {
        "id": type_id,
        "title": _localized(zh, en),
        "description": _localized(zh_description, en_description),
        "features": [_feature(type_id, *feature) for feature in features],
    }


_CATALOG: dict[str, Any] = {
    "spec_version": APP_SPEC_VERSION,
    "types": [
        _type(
            "calendar",
            "日历",
            "Calendar",
            "安排事件、查看日程与提醒。",
            "Schedule events, browse agendas, and manage reminders.",
            [
                (
                    "events",
                    "事件管理",
                    "Events",
                    "创建、查看或修改日程事件。",
                    "Create, view, or update scheduled events.",
                ),
                ("reminders", "日程提醒", "Reminders", "在指定时间提醒用户。", "Notify users at a scheduled time."),
                (
                    "views",
                    "日历视图",
                    "Calendar views",
                    "按天、周、月或议程查看日程。",
                    "Browse day, week, month, or agenda views.",
                ),
                (
                    "recurrence",
                    "重复日程",
                    "Recurring events",
                    "设置和管理重复发生的事件。",
                    "Define and manage recurring events.",
                ),
            ],
        ),
        _type(
            "tasks",
            "任务",
            "Tasks",
            "管理待办、进度与任务组织。",
            "Manage to-dos, progress, and task organization.",
            [
                ("items", "任务管理", "Task items", "创建、查看或编辑任务。", "Create, view, or edit tasks."),
                (
                    "complete",
                    "任务完成",
                    "Task completion",
                    "记录任务完成或重新开启。",
                    "Mark tasks complete or reopen them.",
                ),
                (
                    "projects",
                    "任务分组",
                    "Task projects",
                    "按项目或列表组织任务。",
                    "Organize tasks into projects or lists.",
                ),
            ],
        ),
        _type(
            "notes",
            "笔记",
            "Notes",
            "记录、查找与组织个人笔记。",
            "Capture, find, and organize personal notes.",
            [
                ("items", "笔记管理", "Note items", "创建、阅读或编辑笔记。", "Create, read, or edit notes."),
                ("search", "笔记搜索", "Note search", "检索笔记内容。", "Search note content."),
                (
                    "organize",
                    "笔记组织",
                    "Note organization",
                    "按标签、分组或关联整理笔记。",
                    "Organize notes with tags, groups, or links.",
                ),
            ],
        ),
        _type(
            "contacts",
            "联系人",
            "Contacts",
            "维护联系人资料与关系。",
            "Maintain contact details and relationships.",
            [
                (
                    "people",
                    "联系人管理",
                    "Contact records",
                    "保存和编辑联系人资料。",
                    "Store and edit contact details.",
                ),
                (
                    "search",
                    "联系人搜索",
                    "Contact search",
                    "按姓名或资料查找联系人。",
                    "Find contacts by name or details.",
                ),
                ("groups", "联系人分组", "Contact groups", "将联系人组织成分组。", "Organize contacts into groups."),
            ],
        ),
        _type(
            "documents",
            "文档",
            "Documents",
            "管理、查找与编辑文档。",
            "Manage, find, and edit documents.",
            [
                (
                    "files",
                    "文档管理",
                    "Document records",
                    "保存、浏览和管理文档。",
                    "Store, browse, and manage documents.",
                ),
                (
                    "search",
                    "文档搜索",
                    "Document search",
                    "按内容或资料检索文档。",
                    "Find documents by content or metadata.",
                ),
                ("editor", "文档编辑", "Document editing", "编辑并保存文档内容。", "Edit and save document content."),
            ],
        ),
        _type(
            "messaging",
            "消息",
            "Messaging",
            "浏览会话、编写与查找消息。",
            "Browse conversations, compose, and find messages.",
            [
                (
                    "conversations",
                    "会话浏览",
                    "Conversations",
                    "查看会话及其消息。",
                    "Read conversations and their messages.",
                ),
                ("compose", "消息编写", "Message composition", "编写并发送消息。", "Compose and send messages."),
                (
                    "search",
                    "消息搜索",
                    "Message search",
                    "检索会话或消息内容。",
                    "Search conversations or message content.",
                ),
            ],
        ),
        _type(
            "finance",
            "财务",
            "Finance",
            "记录收支、预算与财务汇总。",
            "Track transactions, budgets, and financial summaries.",
            [
                (
                    "transactions",
                    "收支记录",
                    "Transactions",
                    "记录、查看或分类收支。",
                    "Record, view, or categorize transactions.",
                ),
                (
                    "budgets",
                    "预算管理",
                    "Budgets",
                    "设置预算并跟踪执行情况。",
                    "Set budgets and track spending against them.",
                ),
                (
                    "reports",
                    "财务报表",
                    "Financial reports",
                    "汇总和分析收支。",
                    "Summarize and analyze income and expenses.",
                ),
            ],
        ),
        _type(
            "media",
            "媒体",
            "Media",
            "组织、浏览与播放媒体内容。",
            "Organize, browse, and play media content.",
            [
                ("library", "媒体库", "Media library", "保存和浏览媒体资料。", "Store and browse media records."),
                ("playback", "媒体播放", "Media playback", "播放或预览媒体内容。", "Play or preview media content."),
                (
                    "organize",
                    "媒体组织",
                    "Media organization",
                    "用集合或标签组织媒体。",
                    "Organize media with collections or tags.",
                ),
            ],
        ),
        _type(
            "dashboard",
            "仪表盘",
            "Dashboard",
            "汇总信息、指标与交互视图。",
            "Present combined information, metrics, and interactive views.",
            [
                (
                    "widgets",
                    "信息面板",
                    "Information panels",
                    "组合多个信息面板。",
                    "Combine multiple information panels.",
                ),
                ("metrics", "指标展示", "Metrics", "汇总和展示关键指标。", "Aggregate and present key metrics."),
                (
                    "filters",
                    "数据筛选",
                    "Data filters",
                    "按条件筛选仪表盘信息。",
                    "Filter dashboard information by criteria.",
                ),
            ],
        ),
        _type(
            "utility",
            "工具",
            "Utility",
            "提供数据处理、计算与转换工具。",
            "Provide data processing, calculation, and conversion tools.",
            [
                (
                    "transform",
                    "数据处理",
                    "Data transformation",
                    "按规则处理或转换数据。",
                    "Process or transform data using defined rules.",
                ),
                (
                    "calculate",
                    "计算",
                    "Calculation",
                    "执行计算并展示结果。",
                    "Perform calculations and present results.",
                ),
                ("convert", "格式转换", "Format conversion", "转换单位或数据格式。", "Convert units or data formats."),
            ],
        ),
    ],
}
_TYPE_IDS = frozenset(item["id"] for item in _CATALOG["types"])
_FEATURE_TYPES = {feature["id"]: item["id"] for item in _CATALOG["types"] for feature in item["features"]}


def get_app_type_catalog() -> dict[str, Any]:
    """Return an independent copy of the common classification vocabulary."""
    return deepcopy(_CATALOG)


class AppSpecificationError(ValueError):
    """An App implementation declaration does not match the standard."""

    def __init__(self, message: str, *, path: str = "app_spec", expected: str = "", observed: str = ""):
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.observed = observed


@dataclass(frozen=True, slots=True)
class AppFeatureDeclaration:
    id: str
    status: str
    surfaces: tuple[str, ...]
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "status": self.status, "surfaces": list(self.surfaces)}
        if self.notes is not None:
            result["notes"] = self.notes
        return result


@dataclass(frozen=True, slots=True)
class AppSpecification:
    spec_version: int
    types: tuple[str, ...]
    features: tuple[AppFeatureDeclaration, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_version": self.spec_version,
            "types": list(self.types),
            "features": [feature.to_dict() for feature in self.features],
        }


def _observed_shape(value: Any) -> str:
    """Describe malformed metadata without copying its contents into diagnostics."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "non-JSON value"


def _error(message: str, *, path: str = "app_spec", expected: str = "", observed: str = "") -> AppSpecificationError:
    detail = f". Expected {expected}; observed {observed}" if expected or observed else ""
    return AppSpecificationError(f"{path}: {message}{detail}", path=path, expected=expected, observed=observed)


def _identifier(value: Any, field: str, path: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_SPEC_ID_LENGTH:
        hint = "; use the ID string, not a catalog object" if isinstance(value, dict) else ""
        raise _error(
            f"{field} must be a non-empty string of at most {MAX_SPEC_ID_LENGTH} characters{hint}",
            path=path,
            expected=f"non-empty {field} string (max {MAX_SPEC_ID_LENGTH} characters)",
            observed=_observed_shape(value),
        )
    return value


def validate_app_spec(value: Any) -> AppSpecification | None:
    """Normalize strict implementation metadata, preserving declared order."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _error("must be an object or null", expected="object or null", observed=_observed_shape(value))
    if set(value) != {"spec_version", "types", "features"}:
        raise _error(
            "requires only spec_version, types, and features",
            expected="object with exactly spec_version, types, features",
            observed="object with missing or extra fields",
        )
    if type(value["spec_version"]) is not int or value["spec_version"] != APP_SPEC_VERSION:
        raise _error(
            f"spec_version must be the supported integer {APP_SPEC_VERSION}",
            path="app_spec.spec_version",
            expected=f"integer {APP_SPEC_VERSION}",
            observed=_observed_shape(value["spec_version"]),
        )
    raw_types = value["types"]
    if not isinstance(raw_types, list) or not 1 <= len(raw_types) <= MAX_APP_TYPES:
        raise _error(
            f"types must be a non-empty array of at most {MAX_APP_TYPES} type IDs",
            path="app_spec.types",
            expected=f"array of 1 to {MAX_APP_TYPES} type ID strings",
            observed=_observed_shape(raw_types),
        )
    types: list[str] = []
    for type_index, raw_type in enumerate(raw_types):
        path = f"app_spec.types[{type_index}]"
        type_id = _identifier(raw_type, "type ID", path)
        if type_id not in _TYPE_IDS and not _CUSTOM_TYPE.fullmatch(type_id):
            raise _error(
                "unknown type ID",
                path=path,
                expected="standard type ID or custom:<lowercase-kebab-case-namespace>",
                observed="unrecognized string",
            )
        if type_id in types:
            raise _error(
                "types must not contain duplicate IDs",
                path=path,
                expected="unique type ID string",
                observed="duplicate string",
            )
        types.append(type_id)
    raw_features = value["features"]
    if not isinstance(raw_features, list) or len(raw_features) > MAX_APP_FEATURES:
        raise _error(
            f"features must be an array of at most {MAX_APP_FEATURES} declarations",
            path="app_spec.features",
            expected=f"array of at most {MAX_APP_FEATURES} feature objects",
            observed=_observed_shape(raw_features),
        )
    seen_features: set[str] = set()
    features: list[AppFeatureDeclaration] = []
    for feature_index, feature in enumerate(raw_features):
        path = f"app_spec.features[{feature_index}]"
        if (
            not isinstance(feature, dict)
            or not {"id", "status", "surfaces"} <= set(feature)
            or set(feature) - {"id", "status", "surfaces", "notes"}
        ):
            raise _error(
                "each feature requires id, status, surfaces and optional notes only",
                path=path,
                expected="object with id, status, surfaces and optional notes only",
                observed=_observed_shape(feature),
            )
        feature_id = _identifier(feature["id"], "feature ID", f"{path}.id")
        feature_type = _FEATURE_TYPES.get(feature_id)
        if feature_type is None:
            custom_feature = _CUSTOM_FEATURE.fullmatch(feature_id)
            if custom_feature is None:
                raise _error(
                    "unknown feature ID",
                    path=f"{path}.id",
                    expected="standard feature ID or custom:<namespace>.<lowercase-kebab-case-feature>",
                    observed="unrecognized string",
                )
            feature_type = f"custom:{custom_feature.group(1)}"
        if feature_type not in types:
            raise _error(
                f"feature requires declared type {feature_type}",
                path=f"{path}.id",
                expected="feature namespace declared in app_spec.types",
                observed="undeclared feature type",
            )
        if feature_id in seen_features:
            raise _error(
                "features must not contain duplicate IDs",
                path=f"{path}.id",
                expected="unique feature ID string",
                observed="duplicate string",
            )
        seen_features.add(feature_id)
        status = feature["status"]
        if not isinstance(status, str) or status not in _STATUSES:
            raise _error(
                "feature status must be implemented, partial, or planned",
                path=f"{path}.status",
                expected="implemented, partial, or planned",
                observed=_observed_shape(status),
            )
        raw_surfaces = feature["surfaces"]
        if not isinstance(raw_surfaces, list) or len(raw_surfaces) > len(_SURFACES):
            raise _error(
                "surfaces must be an array containing data, tools, or ui",
                path=f"{path}.surfaces",
                expected="array containing at most data, tools, ui",
                observed=_observed_shape(raw_surfaces),
            )
        surfaces: list[str] = []
        for surface_index, surface in enumerate(raw_surfaces):
            if not isinstance(surface, str) or surface not in _SURFACES:
                raise _error(
                    "unknown feature surface; use data, tools, or ui",
                    path=f"{path}.surfaces[{surface_index}]",
                    expected="data, tools, or ui",
                    observed=_observed_shape(surface),
                )
            if surface in surfaces:
                raise _error(
                    "surfaces must not contain duplicates",
                    path=f"{path}.surfaces[{surface_index}]",
                    expected="unique surface string",
                    observed="duplicate string",
                )
            surfaces.append(surface)
        if status == "planned" and surfaces:
            raise _error(
                "planned features must have empty surfaces",
                path=f"{path}.surfaces",
                expected="empty array for planned feature",
                observed="non-empty array",
            )
        if status != "planned" and not surfaces:
            raise _error(
                "implemented and partial features require at least one surface",
                path=f"{path}.surfaces",
                expected="at least one surface for implemented or partial feature",
                observed="empty array",
            )
        notes = feature.get("notes")
        if "notes" in feature and (not isinstance(notes, str) or len(notes) > MAX_FEATURE_NOTES_LENGTH):
            raise _error(
                f"feature notes must be a string of at most {MAX_FEATURE_NOTES_LENGTH} characters",
                path=f"{path}.notes",
                expected=f"string of at most {MAX_FEATURE_NOTES_LENGTH} characters",
                observed=_observed_shape(notes),
            )
        features.append(AppFeatureDeclaration(feature_id, status, tuple(surfaces), notes))
    return AppSpecification(APP_SPEC_VERSION, tuple(types), tuple(features))
