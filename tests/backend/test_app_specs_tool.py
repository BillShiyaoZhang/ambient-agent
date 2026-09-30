import pytest

from backend.agent.tools import registry
from backend.main import app_manager


@pytest.mark.asyncio
async def test_agent_reads_large_valid_specs_individually_within_output_limit():
    specification = {
        "spec_version": 1,
        "types": ["custom:journal"],
        "features": [
            {"id": f"custom:journal.feature-{index}", "status": "implemented", "surfaces": ["ui"], "notes": "x" * 2000}
            for index in range(25)
        ],
    }
    for app_id in ("journal-one", "journal-two"):
        app_manager.create_or_update_app(app_id, "Journal", js="original", app_spec=specification)

    context = {"scopes": ["workspace:read"]}
    for app_id in ("journal-one", "journal-two"):
        result = await registry.execute("list_app_specs", {"app_id": app_id}, context)
        assert len(result) == 1
        assert result[0]["id"] == app_id
        assert result[0]["app_spec"] == specification
    assert await registry.execute("list_app_specs", {"app_id": "missing"}, context) == []
