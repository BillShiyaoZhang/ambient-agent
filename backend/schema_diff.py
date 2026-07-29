"""Structured Schema Verification Diff.

This module replaces the old free-form-text ``SchemaVerificationService.verify``
with a deterministic, structured diff between a widget's controller.js and the
registered graph schemas.

The output is a ``VerificationDiff`` that:

1. enumerates every ``UnknownProperty`` the JS code reads/writes but the schema
   does not list;
2. enumerates every ``TypeMismatch`` between schema-declared type and observed
   runtime value;
3. renders itself to Markdown (back-compat for the existing UI) **and** to a
   per-field payload for the new frontend UI.

JS extraction is regex-first with a 5-second LLM fallback (see
``backend.schema_verification``). The aim is that simple cases — like the
calendar widget using ``category``, ``color``, ``reminder``, ``end_date`` —
are caught deterministically without any LLM call.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("schema_diff")


# ---------------------------------------------------------------------------
# Diff data classes
# ---------------------------------------------------------------------------


@dataclass
class UnknownProperty:
    node_type: str
    property_name: str
    sample_value_repr: str = ""
    occurrences: int = 0
    file_locations: list[str] = field(default_factory=list)


@dataclass
class TypeMismatch:
    node_type: str
    property_name: str
    schema_type: str
    observed_value_repr: str


@dataclass
class UnknownType:
    type_name: str
    occurrences: int = 0


@dataclass
class PerFieldOption:
    node_type: str
    property_name: str
    detected_type: str
    action: str  # "extend_schema" | "rename_in_code" | "drop_field"
    risk: str  # "safe" | "needs_review"

    def to_dict(self) -> dict[str, str]:
        return {
            "node_type": self.node_type,
            "property_name": self.property_name,
            "detected_type": self.detected_type,
            "action": self.action,
            "risk": self.risk,
        }


@dataclass
class VerificationDiff:
    unknown_props: list[UnknownProperty] = field(default_factory=list)
    type_mismatches: list[TypeMismatch] = field(default_factory=list)
    unknown_types: list[UnknownType] = field(default_factory=list)
    # Cached boolean derived from the three lists above; populated in __post_init__
    # so UML-style "is_clean" field check still finds it.
    is_clean: bool = True

    def __post_init__(self) -> None:
        self.is_clean = not (self.unknown_props or self.type_mismatches or self.unknown_types)

    @property
    def has_issues(self) -> bool:
        return not self.is_clean

    def to_markdown(self) -> str:
        if self.is_clean:
            return "✅ Schema Verification PASSED"
        lines: list[str] = []
        lines.append("⚠️ Schema Verification WARNING")
        if self.unknown_props:
            lines.append("")
            lines.append("### Unknown properties (used in code but missing from schema)")
            for up in self.unknown_props:
                lines.append(
                    f"- `{up.node_type}.{up.property_name}` "
                    f"(observed {up.occurrences}×, sample: `{up.sample_value_repr}`)"
                )
        if self.type_mismatches:
            lines.append("")
            lines.append("### Type mismatches")
            for tm in self.type_mismatches:
                lines.append(
                    f"- `{tm.node_type}.{tm.property_name}` schema says `{tm.schema_type}`, "
                    f"code observes `{tm.observed_value_repr}`"
                )
        if self.unknown_types:
            lines.append("")
            lines.append("### Unknown node types")
            for ut in self.unknown_types:
                lines.append(f"- `{ut.type_name}` ({ut.occurrences}×)")
        lines.append("")
        lines.append("Recommendations:")
        if self.unknown_props:
            lines.append("- Extend the registered schemas with the listed properties, OR")
            lines.append("- Rename the JS field to match an existing schema field, OR")
            lines.append("- Drop the unused field.")
        if self.unknown_types:
            lines.append("- Register a new schema for the listed types, OR refactor the JS to reuse existing types.")
        return "\n".join(lines)

    def to_per_field_payload(self) -> list[dict[str, str]]:
        options: list[PerFieldOption] = []
        for up in self.unknown_props:
            detected = _infer_js_type(up.sample_value_repr)
            risk = "needs_review" if up.occurrences <= 1 else "safe"
            options.append(
                PerFieldOption(
                    node_type=up.node_type,
                    property_name=up.property_name,
                    detected_type=detected,
                    action="extend_schema",
                    risk=risk,
                )
            )
        for ut in self.unknown_types:
            options.append(
                PerFieldOption(
                    node_type=ut.type_name,
                    property_name="*",
                    detected_type="object",
                    action="register_new_type",
                    risk="needs_review",
                )
            )
        return [o.to_dict() for o in options]


# ---------------------------------------------------------------------------
# JS extraction
# ---------------------------------------------------------------------------


_API_CALL_RE = re.compile(r"ambient\.graph\.(mutate|subscribe)\s*\(", re.MULTILINE)

_ACTION_OBJECT_RE = re.compile(r"\{\s*(?:[^{}]|\{[^{}]*\})*\}", re.DOTALL)


def _extract_balanced(source: str, start_idx: int) -> tuple[str, int]:
    """Given a source string and the index of an opening paren, return the
    substring inside the matching close paren and the index AFTER the close.

    Raises ValueError if the parens are unbalanced.
    """
    depth = 0
    in_str: str | None = None
    in_tmpl = False
    i = start_idx
    while i < len(source):
        ch = source[i]
        if in_str is not None:
            if ch == "\\" and i + 1 < len(source):
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif in_tmpl:
            if ch == "`":
                in_tmpl = False
            elif ch == "\\" and i + 1 < len(source):
                i += 2
                continue
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "`":
                in_tmpl = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return source[start_idx + 1 : i], i + 1
        i += 1
    raise ValueError("unbalanced parentheses")


def _js_literal_to_python(src: str) -> Any:
    """Convert a JS object literal to Python dict/list.

    Handles:
    - quoted strings (single + double) with standard escapes
    - backtick template strings (kept as raw)
    - numbers (int, float, negative)
    - booleans (true/false)
    - null
    - arrays
    - nested objects
    - variable references (evt.title, state.events, ...) — rendered as
      ``{"__var__": "evt.title"}`` so the caller can still see the structure
      and (importantly) the *key names* of the object.

    This is intentionally lenient — it never raises. On any unparseable input
    it returns ``{"__raw__": src}`` so the caller can still inspect the source.
    """
    src = src.strip()
    if not src:
        return None
    try:
        normalized = _normalize_js_literal(src)
        return json.loads(normalized)
    except Exception:
        # Second attempt: try a more lenient parser that allows var refs.
        try:
            return _lenient_parse(src)
        except Exception:
            return {"__raw__": src}


def _lenient_parse(src: str) -> Any:
    """A small recursive-descent parser tolerant of JS variable references.

    Returns Python dict/list. Variable references become ``{"__var__": name}``
    so key names of objects are preserved for downstream schema diffing.
    """
    import re as _re

    src = src.strip()

    def skip_ws(p: int) -> int:
        while p < len(src) and src[p].isspace():
            p += 1
        return p

    def parse_value(p: int) -> tuple[Any, int]:
        p = skip_ws(p)
        if p >= len(src):
            return None, p
        ch = src[p]
        if ch == "{":
            return parse_object(p)
        if ch == "[":
            return parse_array(p)
        if ch in ('"', "'", "`"):
            v, p2 = parse_string(p)
            return _consume_binary(v, p2)
        if ch == "t" and src.startswith("true", p):
            return _consume_binary(True, p + 4)
        if ch == "f" and src.startswith("false", p):
            return _consume_binary(False, p + 5)
        if ch == "n" and src.startswith("null", p):
            return _consume_binary(None, p + 4)
        if ch == "u" and src.startswith("undefined", p):
            return _consume_binary(None, p + 9)
        if ch.isdigit() or ch == "-":
            v, p2 = parse_number(p)
            return _consume_binary(v, p2)
        # Variable reference or unknown: consume identifier + dotted path.
        m = _re.match(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*|\[[^\]]*\])*", src[p:])
        if m:
            return _consume_binary({"__var__": m.group(0)}, p + m.end())
        # Unknown token; consume one char and move on.
        return None, p + 1

    def _consume_binary(value: Any, p: int) -> tuple[Any, int]:
        """If the next non-space chars are a JS binary operator (|| && + etc.),
        consume the rest of the expression up to the next top-level ,/}/] and
        replace the value with an opaque marker. This avoids parser breakage
        on expressions like ``evt.foo || ''`` while still allowing the parent
        parser to advance past the value."""
        p = skip_ws(p)
        if p >= len(src):
            return value, p
        ch = src[p]
        # Only treat as binary if followed by another expression value.
        # Recognise the most common operators.
        ops = ("||", "&&", "==", "!=", "<=", ">=", "===", "!==")
        op = None
        for o in ops:
            if src.startswith(o, p):
                op = o
                break
        if op is None and ch in "+-":
            # Distinguish unary vs binary: binary only if previous value was a number.
            if isinstance(value, (int, float)):
                op = ch
        if op is None:
            return value, p
        # Consume operator + right-hand side value.
        p += len(op)
        # Skip whitespace and parse the right operand (one value).
        _rh, p = parse_value(p)
        # The whole expression is opaque; downstream code only cares about key
        # names, so wrap the value.
        if isinstance(value, dict) and "__var__" in value:
            return {"__expr__": f"{value['__var__']} {op} ..."}, p
        return {"__expr__": f"{value!r} {op} ..."}, p

    def parse_object(p: int) -> tuple[dict, int]:
        assert src[p] == "{"
        p += 1
        out: dict = {}
        p = skip_ws(p)
        if p < len(src) and src[p] == "}":
            return out, p + 1
        while True:
            p = skip_ws(p)
            if p >= len(src) or src[p] in ",}":
                break
            # Key
            key, p = _parse_key(p)
            p = skip_ws(p)
            if p < len(src) and src[p] == ":":
                p += 1
            value, p = parse_value(p)
            out[key] = value
            p = skip_ws(p)
            if p < len(src) and src[p] == ",":
                p += 1
                continue
            break
        if p < len(src) and src[p] == "}":
            p += 1
        return out, p

    def parse_array(p: int) -> tuple[list, int]:
        assert src[p] == "["
        p += 1
        out: list = []
        p = skip_ws(p)
        if p < len(src) and src[p] == "]":
            return out, p + 1
        while True:
            value, p = parse_value(p)
            out.append(value)
            p = skip_ws(p)
            if p < len(src) and src[p] == ",":
                p += 1
                continue
            break
        if p < len(src) and src[p] == "]":
            p += 1
        return out, p

    def parse_string(p: int) -> tuple[str, int]:
        quote = src[p]
        if quote == "`":
            quote = "`"
        p += 1
        buf: list[str] = []
        while p < len(src):
            ch = src[p]
            if ch == "\\" and p + 1 < len(src):
                buf.append(src[p : p + 2])
                p += 2
                continue
            if ch == quote:
                return "".join(buf), p + 1
            buf.append(ch)
            p += 1
        return "".join(buf), p

    def parse_number(p: int) -> tuple[Any, int]:
        m = _re.match(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", src[p:])
        if m:
            txt = m.group(0)
            try:
                if "." in txt or "e" in txt or "E" in txt:
                    return float(txt), p + m.end()
                return int(txt), p + m.end()
            except Exception:
                return txt, p + m.end()
        return None, p

    def _parse_key(p: int) -> tuple[str, int]:
        p = skip_ws(p)
        ch = src[p]
        if ch in ('"', "'", "`"):
            key, p = parse_string(p)
            return key, p
        # Identifier key.
        m = _re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", src[p:])
        if m:
            return m.group(0), p + m.end()
        return "", p

    value, _ = parse_value(0)
    return value


def _normalize_js_literal(src: str) -> str:
    """Make a JS object literal JSON-parseable. Best effort only.

    Handles unquoted object keys (e.g. ``{ action: "create_node" }``) by
    inserting quotes. Tracks object/array nesting depth to know when we're
    parsing a key vs a value.
    """
    out: list[str] = []
    in_str: str | None = None
    in_tmpl = False
    # State: ``expect_key`` is True right after ``{`` or ``,`` at object depth.
    obj_depth = 0
    expect_key = False
    i = 0
    while i < len(src):
        ch = src[i]
        if in_str is not None:
            if ch == "\\" and i + 1 < len(src):
                out.append(src[i : i + 2])
                i += 2
                continue
            if ch == in_str:
                out.append('"')
                in_str = None
                i += 1
                continue
            if ch == '"':
                out.append('\\"')
            else:
                out.append(ch)
            i += 1
            continue
        if in_tmpl:
            if ch == "`":
                out.append('"')
                in_tmpl = False
                i += 1
                continue
            if ch == "\\" and i + 1 < len(src):
                out.append(src[i : i + 2])
                i += 2
                continue
            if ch == '"':
                out.append('\\"')
            else:
                out.append(ch)
            i += 1
            continue
        # Outside any string.
        if ch in ('"', "'"):
            in_str_now = ch
            j = i + 1
            buf: list[str] = ['"']
            while j < len(src):
                cj = src[j]
                if cj == "\\" and j + 1 < len(src):
                    buf.append(src[j : j + 2])
                    j += 2
                    continue
                if cj == in_str_now:
                    buf.append('"')
                    break
                if cj == '"':
                    buf.append('\\"')
                else:
                    buf.append(cj)
                j += 1
            out.append("".join(buf))
            i = j + 1
            # After a value, expect either , } or ].
            if obj_depth > 0:
                expect_key = False
            continue
        if ch == "`":
            in_tmpl = True
            out.append('"')
            i += 1
            continue
        if src.startswith("true", i) and _is_word_boundary(src, i, 4):
            out.append("true")
            i += 4
            expect_key = False
            continue
        if src.startswith("false", i) and _is_word_boundary(src, i, 5):
            out.append("false")
            i += 5
            expect_key = False
            continue
        if src.startswith("null", i) and _is_word_boundary(src, i, 4):
            out.append("null")
            i += 4
            expect_key = False
            continue
        if src.startswith("undefined", i) and _is_word_boundary(src, i, 9):
            out.append("null")
            i += 9
            expect_key = False
            continue
        if src.startswith("//", i):
            while i < len(src) and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j == -1:
                break
            i = j + 2
            continue
        if ch == "{":
            obj_depth += 1
            out.append(ch)
            i += 1
            expect_key = True
            continue
        if ch == "}":
            obj_depth = max(0, obj_depth - 1)
            out.append(ch)
            i += 1
            expect_key = False
            continue
        if ch == "[":
            out.append(ch)
            i += 1
            continue
        if ch == "]":
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            out.append(ch)
            i += 1
            if obj_depth > 0:
                expect_key = True
            continue
        if ch == ":":
            out.append(ch)
            i += 1
            expect_key = False
            continue
        # At this point we have an unquoted word/number. If we're at object
        # key position, treat as quoted key.
        if expect_key and (ch.isalpha() or ch == "_" or ch == "$"):
            j = i
            while j < len(src) and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            key = src[i:j]
            out.append(f'"{key}"')
            i = j
            continue
        # Plain number.
        if ch.isdigit() or (ch == "-" and i + 1 < len(src) and src[i + 1].isdigit()):
            j = i
            if src[j] == "-":
                j += 1
            while j < len(src) and (
                src[j].isdigit()
                or src[j] == "."
                or src[j].lower() == "e"
                or (src[j] in "+-" and src[j - 1].lower() == "e")
            ):
                j += 1
            out.append(src[i:j])
            i = j
            expect_key = False
            continue
        out.append(ch)
        i += 1

    text = "".join(out)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def _is_word_boundary(src: str, i: int, length: int) -> bool:
    end = i + length
    if end < len(src):
        c = src[end]
        if c.isalnum() or c in "_$":
            return False
    if i > 0:
        c = src[i - 1]
        if c.isalnum() or c in "_$":
            return False
    return True


def _infer_js_type(value_repr: str) -> str:
    """Infer statically evident JS types without guessing dynamic expressions."""
    v = (value_repr or "").strip()
    if v in {"Number", "parseFloat"}:
        return "number"
    if v == "parseInt":
        return "integer"
    if v == "String":
        return "string"
    if v == "Boolean":
        return "boolean"
    if not v or v in ("null", "undefined"):
        return "unknown"
    if v.startswith('"') or v.startswith("'") or v.startswith("`"):
        return "string"
    if v in ("true", "false"):
        return "boolean"
    if v.startswith("["):
        return "array"
    if v.startswith("{"):
        return "object"
    try:
        int(v)
        return "integer"
    except Exception:
        pass
    try:
        float(v)
        return "number"
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# SchemaExtractor
# ---------------------------------------------------------------------------


class SchemaExtractor:
    """Extract every ``ambient.graph.mutate`` and ``ambient.graph.subscribe``
    call from a controller.js source string."""

    @staticmethod
    def extract_actions(js_source: str) -> list[dict[str, Any]]:
        return SchemaExtractor._extract_call_payloads(js_source, kind="mutate")

    @staticmethod
    def extract_subscriptions(js_source: str) -> list[dict[str, Any]]:
        return SchemaExtractor._extract_call_payloads(js_source, kind="subscribe")

    _NODE_ACTION_RE = re.compile(
        r"\{\s*(?:[^{}]|\{[^{}]*\})*?\baction\s*:\s*['\"](?P<action>create_node|update_node|update_node_property|delete_node)['\"]"
        r"(?:[^{}]|\{[^{}]*\})*\}",
        re.DOTALL,
    )

    @staticmethod
    def _extract_call_payloads(js_source: str, kind: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for m in _API_CALL_RE.finditer(js_source):
            if m.group(1) != kind:
                continue
            paren_idx = m.end() - 1  # the '(' character
            try:
                body, _ = _extract_balanced(js_source, paren_idx)
            except ValueError:
                logger.debug(f"unbalanced parens around {kind} call at {m.start()}")
                continue
            if kind == "mutate":
                items = SchemaExtractor._parse_mutate_body(body)
            else:
                items = SchemaExtractor._parse_subscribe_body(body)
            for it in items:
                if isinstance(it, dict):
                    results.append(it)

        # Second pass: action templates pushed into arrays and passed by
        # variable (e.g. ``mutations.push({action: 'create_node', ...})``).
        # We pick up any object literal that *looks like* a node action.
        if kind == "mutate":
            for tmpl in SchemaExtractor._extract_action_templates(js_source):
                results.append(tmpl)

        # Deduplicate by (action, type, frozenset of properties)).
        seen: set[tuple] = set()
        deduped: list[dict[str, Any]] = []
        for r in results:
            key = (
                r.get("action"),
                r.get("type"),
                tuple(sorted((r.get("properties") or {}).keys())),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        return deduped

    @staticmethod
    def _extract_action_templates(js_source: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in SchemaExtractor._NODE_ACTION_RE.finditer(js_source):
            raw = m.group(0)
            parsed = _js_literal_to_python(raw)
            if isinstance(parsed, dict):
                # Normalise update_node -> update_node_property for diff purposes.
                if parsed.get("action") == "update_node":
                    parsed = {**parsed, "action": "update_node_property"}
                out.append(parsed)
        return out

    @staticmethod
    def _parse_mutate_body(body: str) -> list[Any]:
        """The body is an array literal. We split it at the top level into
        individual action objects."""
        stripped = body.strip()
        if not stripped.startswith("["):
            # Some apps do ``mutate({...})`` instead of ``mutate([{...}])``;
            # accept the single-object form too.
            if stripped.startswith("{"):
                return [_js_literal_to_python(stripped)]
            return []

        # Find balanced item boundaries.
        items: list[str] = []
        depth = 0
        in_str: str | None = None
        in_tmpl = False
        start = 1  # skip '['
        i = start
        while i < len(stripped) - 1:  # skip trailing ']'
            ch = stripped[i]
            if in_str is not None:
                if ch == "\\" and i + 1 < len(stripped):
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif in_tmpl:
                if ch == "`":
                    in_tmpl = False
                elif ch == "\\" and i + 1 < len(stripped):
                    i += 2
                    continue
            else:
                if ch in ('"', "'"):
                    in_str = ch
                elif ch == "`":
                    in_tmpl = True
                elif ch in ("{", "["):
                    depth += 1
                elif ch in ("}", "]"):
                    depth -= 1
                elif ch == "," and depth == 0:
                    items.append(stripped[start:i].strip())
                    start = i + 1
            i += 1
        last = stripped[start : len(stripped) - 1].strip()
        if last:
            items.append(last)
        parsed = [_js_literal_to_python(it) for it in items]
        # Flatten in case the array is wrapped in a single dict.
        out: list[Any] = []
        for p in parsed:
            if isinstance(p, list):
                out.extend(p)
            else:
                out.append(p)
        return out

    @staticmethod
    def _parse_subscribe_body(body: str) -> list[dict[str, Any]]:
        """The body is ``(query, callback)``. Return the parsed query dict."""
        stripped = body.strip()
        if not stripped:
            return []
        # Split on the LAST top-level comma at depth 0 (to skip commas inside
        # nested structures).
        depth = 0
        in_str: str | None = None
        in_tmpl = False
        last_comma = -1
        for i, ch in enumerate(stripped):
            if in_str is not None:
                if ch == "\\" and i + 1 < len(stripped):
                    continue
                if ch == in_str:
                    in_str = None
                continue
            if in_tmpl:
                if ch == "`":
                    in_tmpl = False
                continue
            if ch in ('"', "'"):
                in_str = ch
                continue
            if ch == "`":
                in_tmpl = True
                continue
            if ch in ("{", "["):
                depth += 1
            elif ch in ("}", "]"):
                depth -= 1
            elif ch == "," and depth == 0:
                last_comma = i
        query_src = stripped[:last_comma].strip() if last_comma > 0 else stripped
        parsed = _js_literal_to_python(query_src)
        if isinstance(parsed, dict):
            return [parsed]
        return []


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------


_TYPE_NAME_TO_SCHEMA_TYPE = {
    "string": "string",
    "str": "string",
    "number": "number",
    "float": "number",
    "double": "number",
    "integer": "integer",
    "int": "integer",
    "boolean": "boolean",
    "bool": "boolean",
}


def compute_diff(
    actions: list[dict[str, Any]],
    subscriptions: list[dict[str, Any]],
    registered_schemas: list[dict[str, Any]],
    source_lines: list[str] | None = None,
) -> VerificationDiff:
    diff = VerificationDiff()

    schema_by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in registered_schemas}

    # Collect per-(type, prop) usage from code.
    usage: dict[tuple[str, str], dict[str, Any]] = {}
    unknown_type_uses: dict[str, int] = {}

    def _consume_props(type_name: str, props: dict[str, Any]) -> None:
        if type_name not in schema_by_id:
            unknown_type_uses[type_name] = unknown_type_uses.get(type_name, 0) + 1
            return
        for prop_name, prop_val in (props or {}).items():
            key = (type_name, prop_name)
            entry = usage.setdefault(
                key,
                {"occurrences": 0, "samples": [], "loc": set()},
            )
            entry["occurrences"] += 1
            if prop_val is not None and len(entry["samples"]) < 3:
                entry["samples"].append(_repr(prop_val))

    for action in actions:
        if not isinstance(action, dict):
            continue
        type_name = action.get("type")
        if not type_name:
            continue
        if action.get("action") == "create_edge":
            edge_props = action.get("properties") or {}
            for prop_name in edge_props:
                # Edges have no type-specific schema; treat as not-applicable.
                continue
            continue
        if action.get("action") == "delete_node":
            continue
        props = action.get("properties") or {}
        _consume_props(type_name, props)

    for sub in subscriptions:
        if not isinstance(sub, dict):
            continue
        type_name = sub.get("type")
        if not type_name:
            continue
        if type_name not in schema_by_id:
            unknown_type_uses[type_name] = unknown_type_uses.get(type_name, 0) + 1
        props = sub.get("properties") or {}
        if isinstance(props, dict):
            for prop_name in props:
                key = (type_name, prop_name)
                entry = usage.setdefault(
                    key,
                    {"occurrences": 0, "samples": [], "loc": set()},
                )
                entry["occurrences"] += 1

    for (type_name, prop_name), entry in usage.items():
        schema = schema_by_id.get(type_name)
        schema_props = (schema or {}).get("properties", {})
        if prop_name not in schema_props:
            sample = entry["samples"][0] if entry["samples"] else ""
            diff.unknown_props.append(
                UnknownProperty(
                    node_type=type_name,
                    property_name=prop_name,
                    sample_value_repr=sample,
                    occurrences=entry["occurrences"],
                )
            )
        else:
            expected = schema_props[prop_name].lower()
            for sample in entry["samples"]:
                observed = _infer_js_type(sample)
                if observed == "unknown":
                    continue
                if observed == "string" and expected == "string":
                    continue
                if observed == "object" or observed == "array":
                    continue
                mapped = _TYPE_NAME_TO_SCHEMA_TYPE.get(observed, observed)
                if mapped != expected:
                    diff.type_mismatches.append(
                        TypeMismatch(
                            node_type=type_name,
                            property_name=prop_name,
                            schema_type=expected,
                            observed_value_repr=sample,
                        )
                    )
                    break

    for t, count in unknown_type_uses.items():
        diff.unknown_types.append(UnknownType(type_name=t, occurrences=count))

    # Recompute is_clean after all lists populated (dataclass __post_init__
    # only sees the initial values).
    diff.is_clean = not (diff.unknown_props or diff.type_mismatches or diff.unknown_types)

    return diff


def _repr(value: Any) -> str:
    if isinstance(value, dict):
        if "__var__" in value:
            return value["__var__"]
        if "__expr__" in value:
            return value["__expr__"]
    if isinstance(value, str):
        return f'"{value}"'
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def diff_controller_js(
    controller_js: str,
    registered_schemas: list[dict[str, Any]],
) -> VerificationDiff:
    """Compute the diff from a controller.js source string."""
    actions = SchemaExtractor.extract_actions(controller_js)
    subs = SchemaExtractor.extract_subscriptions(controller_js)
    return compute_diff(actions, subs, registered_schemas)
