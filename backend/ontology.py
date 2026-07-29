from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ONTOLOGY_ID = "ambient-context"
ONTOLOGY_VERSION = "1.0.0"
USER_CONTEXT_SCOPE = "user_context"
ALLOWED_PROPERTY_TYPES = frozenset({"string", "integer", "number", "boolean"})
SYSTEM_PROPERTIES = {"namespace": "string"}
LEGACY_ENTITY_ALIASES: dict[str, tuple[str, dict[str, str]]] = {
    "CalendarEvent": ("Event", {"summary": "title", "time": "start_time"}),
    "User": ("Person", {}),
}


@dataclass(frozen=True, slots=True)
class OntologyEntity:
    id: str
    name: str
    description: str
    properties: dict[str, str] = field(default_factory=dict)
    ontology_iri: str = ""
    source: str = "Schema.org"
    equivalent_to: tuple[str, ...] = ()
    subclass_of: str | None = "Thing"
    is_core: bool = True
    abstract: bool = False
    data_scope: str = USER_CONTEXT_SCOPE

    def as_schema(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "properties": dict(self.properties),
            "is_core": self.is_core,
            "ontology_id": ONTOLOGY_ID,
            "ontology_iri": self.ontology_iri,
            "source": self.source,
            "equivalent_to": list(self.equivalent_to),
            "subclass_of": self.subclass_of,
            "abstract": self.abstract,
            "data_scope": self.data_scope,
        }


PREBUILT_ONTOLOGY: tuple[OntologyEntity, ...] = (
    OntologyEntity(
        id="Thing",
        name="Thing",
        description="Abstract root for concepts that may contain user-context records",
        ontology_iri="https://schema.org/Thing",
        subclass_of=None,
        abstract=True,
    ),
    OntologyEntity(
        id="Task",
        name="Task",
        description="An action or todo item the user intends to complete",
        properties={
            "title": "string",
            "description": "string",
            "status": "string",
            "due_date": "string",
            "priority": "string",
        },
        ontology_iri="https://schema.org/Action",
    ),
    OntologyEntity(
        id="Event",
        name="Event",
        description="A calendar entry or scheduled block of time",
        properties={
            "title": "string",
            "description": "string",
            "start_time": "string",
            "end_time": "string",
            "location": "string",
        },
        ontology_iri="https://schema.org/Event",
    ),
    OntologyEntity(
        id="Note",
        name="Note",
        description="A freeform note or short piece of user-authored content",
        properties={"title": "string", "content": "string", "tags": "string"},
        ontology_iri="https://schema.org/CreativeWork",
    ),
    OntologyEntity(
        id="Person",
        name="Person",
        description="A person relevant to the user's context",
        properties={"name": "string", "email": "string", "phone": "string"},
        ontology_iri="https://schema.org/Person",
    ),
    OntologyEntity(
        id="Organization",
        name="Organization",
        description="An organization relevant to the user's context",
        properties={"name": "string", "description": "string", "url": "string"},
        ontology_iri="https://schema.org/Organization",
    ),
    OntologyEntity(
        id="Project",
        name="Project",
        description="A project grouping related goals, tasks, documents, and people",
        properties={"name": "string", "description": "string", "status": "string"},
        ontology_iri="https://schema.org/Project",
    ),
    OntologyEntity(
        id="Document",
        name="Document",
        description="A document or reference whose existence or content aids user understanding",
        properties={
            "title": "string",
            "content": "string",
            "uri": "string",
            "mime_type": "string",
            "summary": "string",
        },
        ontology_iri="https://schema.org/DigitalDocument",
    ),
    OntologyEntity(
        id="Place",
        name="Place",
        description="A physical or virtual place relevant to the user",
        properties={"name": "string", "address": "string", "url": "string"},
        ontology_iri="https://schema.org/Place",
    ),
    OntologyEntity(
        id="Message",
        name="Message",
        description="A communication that contributes to the user's context",
        properties={"text": "string", "sent_at": "string", "channel": "string", "subject": "string"},
        ontology_iri="https://schema.org/Message",
    ),
    OntologyEntity(
        id="SoftwareApplication",
        name="Software Application",
        description="An App reference, including where App-private runtime data is kept",
        properties={
            "name": "string",
            "app_id": "string",
            "description": "string",
            "data_uri": "string",
            "data_summary": "string",
        },
        ontology_iri="https://schema.org/SoftwareApplication",
    ),
)


def validate_property_definition(properties: Any, *, entity_id: str) -> dict[str, str]:
    if not isinstance(properties, dict):
        raise ValueError(f"Ontology entity '{entity_id}' properties must be an object")
    normalized: dict[str, str] = {}
    for key, value in properties.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Ontology entity '{entity_id}' has an invalid property name")
        if value not in ALLOWED_PROPERTY_TYPES:
            raise ValueError(f"Ontology entity '{entity_id}' property '{key}' has unsupported type '{value}'")
        normalized[key] = value
    return normalized


def validate_correspondences(value: Any, *, entity_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"Ontology entity '{entity_id}' equivalent_to must be an array of non-empty IRIs")
    return list(dict.fromkeys(item.strip() for item in value))


def coerce_entity_properties(schema: dict[str, Any] | None, properties: dict[str, Any]) -> dict[str, Any]:
    if schema is None:
        raise ValueError("Entity is not registered in ontology")
    entity_id = str(schema["id"])
    if schema.get("ontology_id") != ONTOLOGY_ID:
        raise ValueError(f"Entity '{entity_id}' is not registered in ontology '{ONTOLOGY_ID}'")
    if schema.get("abstract"):
        raise ValueError(f"Ontology entity '{entity_id}' is abstract and cannot classify records")
    if schema.get("data_scope", USER_CONTEXT_SCOPE) != USER_CONTEXT_SCOPE:
        raise ValueError(f"Ontology entity '{entity_id}' is not user-context data")
    if not isinstance(properties, dict):
        raise ValueError(f"Properties for ontology entity '{entity_id}' must be an object")

    schema_properties = {**SYSTEM_PROPERTIES, **(schema.get("properties") or {})}
    unknown = set(properties) - set(schema_properties)
    if unknown:
        key = sorted(unknown)[0]
        raise ValueError(f"Unknown property '{key}' on ontology entity '{entity_id}'; grow the ontology first")

    validated = dict(properties)
    for key, value in properties.items():
        expected = schema_properties[key]
        if expected == "string" and not isinstance(value, str):
            validated[key] = str(value)
        elif expected == "integer":
            try:
                validated[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Property '{key}' on ontology entity '{entity_id}' must be an integer") from exc
        elif expected == "number":
            try:
                validated[key] = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Property '{key}' on ontology entity '{entity_id}' must be a number") from exc
        elif expected == "boolean":
            if isinstance(value, bool):
                validated[key] = value
            elif str(value).lower() in {"true", "1", "yes"}:
                validated[key] = True
            elif str(value).lower() in {"false", "0", "no"}:
                validated[key] = False
            else:
                raise ValueError(f"Property '{key}' on ontology entity '{entity_id}' must be a boolean")
    return validated
