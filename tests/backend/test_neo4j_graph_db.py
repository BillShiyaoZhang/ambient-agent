from __future__ import annotations

from typing import Any

import pytest
from neo4j.exceptions import ServiceUnavailable

from backend.graph_db import GraphSchemaReadError
from backend.neo4j_graph_db import Neo4jGraphDatabase
from backend.ontology import ONTOLOGY_ID


class _RecordingTransaction:
    def __init__(self, records: list[dict[str, Any]]):
        self.records = records
        self.query = ""
        self.parameters: dict[str, Any] = {}

    def run(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        self.query = " ".join(query.split())
        self.parameters = parameters
        return self.records


def _adapter_with_transaction(
    transaction: _RecordingTransaction,
) -> Neo4jGraphDatabase:
    adapter = object.__new__(Neo4jGraphDatabase)
    adapter._read = lambda callback, *args: callback(transaction, *args)
    return adapter


def test_neo4j_list_schema_ids_is_bounded_in_cypher() -> None:
    transaction = _RecordingTransaction(
        [
            {"id": "Document"},
            {"id": "Event"},
        ]
    )
    adapter = _adapter_with_transaction(transaction)

    assert adapter.list_schema_ids(limit=2, max_id_codepoints=129) == [
        "Document",
        "Event",
    ]

    assert transaction.parameters == {
        "ontology_id": ONTOLOGY_ID,
        "limit": 2,
        "max_id_codepoints": 129,
    }
    assert "MATCH (e:OntologyEntity {ontology_id: $ontology_id})" in transaction.query
    assert "ORDER BY e.id" in transaction.query
    assert "LIMIT $limit" in transaction.query
    assert "RETURN substring(toString(e.id), 0, $max_id_codepoints) AS id" in transaction.query
    assert transaction.query.index("ORDER BY e.id") < transaction.query.index("LIMIT $limit")
    assert transaction.query.index("LIMIT $limit") < transaction.query.index("RETURN substring")


@pytest.mark.parametrize(
    ("limit", "max_id_codepoints", "error_type"),
    [
        (True, 129, TypeError),
        (0, 129, ValueError),
        (1, True, TypeError),
        (1, 0, ValueError),
    ],
)
def test_neo4j_list_schema_ids_validates_explicit_bounds(
    limit: int,
    max_id_codepoints: int,
    error_type: type[Exception],
) -> None:
    adapter = _adapter_with_transaction(_RecordingTransaction([]))

    with pytest.raises(error_type, match="positive integer"):
        adapter.list_schema_ids(
            limit=limit,
            max_id_codepoints=max_id_codepoints,
        )


def test_neo4j_list_schema_ids_translates_adapter_failures() -> None:
    adapter = object.__new__(Neo4jGraphDatabase)

    def fail_read(*_args: Any, **_kwargs: Any) -> Any:
        raise ServiceUnavailable("sensitive driver detail")

    adapter._read = fail_read

    with pytest.raises(GraphSchemaReadError) as exc_info:
        adapter.list_schema_ids(limit=1, max_id_codepoints=129)

    assert str(exc_info.value) == ""


def test_neo4j_list_schema_ids_does_not_mask_programming_errors() -> None:
    adapter = _adapter_with_transaction(_RecordingTransaction([{}]))

    with pytest.raises(KeyError, match="id"):
        adapter.list_schema_ids(limit=1, max_id_codepoints=129)
