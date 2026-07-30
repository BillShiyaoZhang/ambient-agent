from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.skill_catalog import (
    GitHubSkillCatalogProvider,
    SkillCatalog,
    build_skill_catalog,
    load_skill_catalog_config,
)
from backend.skill_manager import SkillManager
from backend.skill_market import SkillMarket, SkillMarketError
from backend.skill_store import SkillRevisionConflict, SkillVersionConflict


def _skill_bytes(name: str, body: str = "Follow the pinned procedure.") -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: Use {name} for a pinned review workflow.\n"
        "license: MIT\n"
        "compatibility: ambient-agent >= 0.1\n"
        "---\n"
        f"{body}\n"
    ).encode()


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _github_entry(
    content: bytes,
    *,
    commit: str = "a" * 40,
    files: list[str] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "repository": "anthropics/skills",
        "commit": commit,
        "path": "skills/remote-review",
        "sha256": _sha256(content),
        "files": files if files is not None else ["SKILL.md"],
        "namespace": "github-anthropics-skills",
        "title": "Remote Review",
        "provider": "Anthropic",
        "tags": ["review"],
        "triggers": ["remote review"],
        "ontology_refs": [],
    }
    return value


def _github_provider(
    tmp_path: Path,
    content: bytes,
    *,
    commit: str = "a" * 40,
    fetcher=None,
) -> GitHubSkillCatalogProvider:
    return GitHubSkillCatalogProvider(
        source_id="anthropic-official",
        entries=[_github_entry(content, commit=commit)],
        cache_dir=tmp_path / "catalog-cache",
        fetcher=fetcher or (lambda _url: content),
    )


def _write_local_skill(market_dir: Path, name: str, namespace: str) -> None:
    entry = market_dir / name
    entry.mkdir(parents=True)
    (entry / "SKILL.md").write_bytes(_skill_bytes(name))
    (entry / "market.json").write_text(
        json.dumps(
            {
                "market_id": f"{namespace}/{name}",
                "catalog_id": f"agent-skill:{namespace}:{name}",
                "title": name.replace("-", " ").title(),
                "provider": namespace,
                "version": "1.0.0",
                "tags": [],
                "ontology_refs": [],
                "triggers": [name.replace("-", " ")],
                "surfaces": ["agent_context"],
                "provenance": {
                    "source": f"local://{namespace}/{name}",
                    "verified": False,
                },
            }
        ),
        encoding="utf-8",
    )


def test_catalog_aggregates_bundled_and_legacy_local_market(tmp_path: Path) -> None:
    market_dir = tmp_path / "local-market"
    market_dir.mkdir()
    _write_local_skill(market_dir, "local-review", "local-test")

    catalog = build_skill_catalog(
        tmp_path / "workspace",
        local_market_dir=market_dir,
    )
    manager = SkillManager(tmp_path / "workspace", catalog=catalog)

    listing = manager.list_market()

    assert [item["market_id"] for item in listing["items"]] == [
        "ambient-agent/daily-planning",
        "local-test/local-review",
    ]
    assert listing["sources"] == [
        {
            "id": "bundled",
            "kind": "bundled",
            "required": True,
            "enabled": True,
            "status": "available",
            "entry_count": 1,
        },
        {
            "id": "local-admin",
            "kind": "local",
            "required": False,
            "enabled": True,
            "status": "available",
            "entry_count": 1,
        },
    ]


def test_catalog_isolates_optional_provider_failure_but_fails_duplicate_ids(
    tmp_path: Path,
) -> None:
    class FailingProvider:
        source_id = "community-search"
        kind = "registry"
        required = False

        @staticmethod
        def list_entries():
            raise SkillMarketError("registry is offline")

    bundled = SkillMarket(source_id="bundled", kind="bundled", required=True)
    manager = SkillManager(
        tmp_path / "workspace",
        catalog=SkillCatalog([bundled, FailingProvider()]),
    )

    listing = manager.list_market()
    assert [item["market_id"] for item in listing["items"]] == [
        "ambient-agent/daily-planning"
    ]
    assert listing["sources"][1] == {
        "id": "community-search",
        "kind": "registry",
        "required": False,
        "enabled": True,
        "status": "unavailable",
        "entry_count": 0,
        "error": "registry is offline",
    }

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_local_skill(first, "same-name", "duplicate")
    _write_local_skill(second, "same-name", "duplicate")
    duplicate_catalog = SkillCatalog(
        [
            SkillMarket(first, source_id="first", required=True),
            SkillMarket(second, source_id="second", required=True),
        ]
    )
    with pytest.raises(SkillMarketError, match="Duplicate market_id across catalog sources"):
        duplicate_catalog.list_entries()


def test_source_preferences_default_enabled_persist_and_skip_provider_reads(
    tmp_path: Path,
) -> None:
    content = _skill_bytes("remote-review")
    delegate = _github_provider(tmp_path, content)

    class CountingProvider:
        source_id = delegate.source_id
        kind = delegate.kind
        required = delegate.required
        calls = 0

        def list_entries(self):
            self.calls += 1
            return delegate.list_entries()

    provider = CountingProvider()
    workspace = tmp_path / "workspace"
    manager = SkillManager(
        workspace,
        catalog=SkillCatalog([provider]),
    )

    initial = manager.list_market()
    assert initial["sources"][0]["enabled"] is True
    assert initial["sources"][0]["status"] == "available"
    assert len(initial["items"]) == 1
    assert provider.calls == 1

    disabled = manager.set_source_enabled(
        "anthropic-official",
        False,
        expected_revision=0,
    )
    assert disabled == {
        "source_id": "anthropic-official",
        "enabled": False,
        "revision": 1,
    }
    listing = manager.list_market()
    assert listing["sources"] == [
        {
            "id": "anthropic-official",
            "kind": "github",
            "required": False,
            "enabled": False,
            "status": "disabled",
            "entry_count": 0,
        }
    ]
    assert listing["items"] == []
    assert provider.calls == 1
    with pytest.raises(KeyError):
        manager.install("github-anthropics-skills/remote-review")

    reopened = SkillManager(
        workspace,
        catalog=SkillCatalog([provider]),
    )
    assert reopened.list_market()["sources"][0]["enabled"] is False
    assert provider.calls == 1
    assert reopened.set_source_enabled(
        "anthropic-official",
        True,
        expected_revision=1,
    ) == {
        "source_id": "anthropic-official",
        "enabled": True,
        "revision": 2,
    }
    assert len(reopened.list_market()["items"]) == 1
    assert provider.calls == 2


def test_source_preference_rejects_unknown_source_and_stale_revision(
    tmp_path: Path,
) -> None:
    manager = SkillManager(tmp_path / "workspace")

    with pytest.raises(KeyError):
        manager.set_source_enabled(
            "missing-source",
            False,
            expected_revision=0,
        )
    manager.set_source_enabled("bundled", False, expected_revision=0)
    with pytest.raises(SkillRevisionConflict):
        manager.set_source_enabled("bundled", True, expected_revision=0)


def test_disabling_source_does_not_remove_installed_snapshot(tmp_path: Path) -> None:
    manager = SkillManager(tmp_path / "workspace")
    installed = manager.install(
        "ambient-agent/daily-planning",
        expected_revision=0,
    )

    manager.set_source_enabled("bundled", False, expected_revision=1)

    assert manager.list_market()["items"] == []
    installed_items = manager.list_catalog_items()
    assert [item["catalog_id"] for item in installed_items] == [
        installed["catalog_id"]
    ]
    assert installed_items[0]["available"] is True


def test_github_provider_verifies_pin_exposes_origin_and_uses_verified_cache(
    tmp_path: Path,
) -> None:
    content = _skill_bytes("remote-review")
    fetched_urls: list[str] = []

    def fetch(url: str) -> bytes:
        fetched_urls.append(url)
        return content

    provider = _github_provider(tmp_path, content, fetcher=fetch)
    entry = provider.list_entries()[0]

    assert entry.version == "a" * 40
    assert fetched_urls == [
        "https://raw.githubusercontent.com/anthropics/skills/"
        f"{'a' * 40}/skills/remote-review/SKILL.md"
    ]
    item = entry.as_market_item()
    assert item["provenance"]["verified"] is False
    assert item["provenance"]["trust"] == "local"
    assert item["catalog_source"] == {
        "id": "anthropic-official",
        "kind": "github",
        "source_uri": (
            "https://github.com/anthropics/skills/tree/"
            f"{'a' * 40}/skills/remote-review"
        ),
        "source_revision": "a" * 40,
        "upstream_hash": _sha256(content),
        "update_strategy": "content_hash",
    }
    assert item["package_compatibility"] == {
        "profile": "context-only-v1",
        "status": "compatible",
        "reasons": [],
    }

    cached = _github_provider(
        tmp_path,
        content,
        fetcher=lambda _url: (_ for _ in ()).throw(OSError("offline")),
    )
    assert cached.list_entries()[0].digest == entry.digest


def test_github_provider_rejects_mutable_or_non_standalone_sources(
    tmp_path: Path,
) -> None:
    content = _skill_bytes("remote-review")
    with pytest.raises(SkillMarketError, match="40-character lowercase commit"):
        GitHubSkillCatalogProvider(
            source_id="bad-ref",
            entries=[_github_entry(content, commit="main")],
            cache_dir=tmp_path / "cache-ref",
            fetcher=lambda _url: content,
        )
    with pytest.raises(SkillMarketError, match="normalized relative path"):
        GitHubSkillCatalogProvider(
            source_id="bad-path",
            entries=[
                {
                    **_github_entry(content),
                    "path": "skills/remote-review?raw=1",
                }
            ],
            cache_dir=tmp_path / "cache-path",
            fetcher=lambda _url: content,
        )
    with pytest.raises(SkillMarketError, match=r"only SKILL\.md"):
        GitHubSkillCatalogProvider(
            source_id="executable",
            entries=[
                _github_entry(
                    content,
                    files=["SKILL.md", "scripts/run.py"],
                )
            ],
            cache_dir=tmp_path / "cache-files",
            fetcher=lambda _url: content,
        )
    with pytest.raises(SkillMarketError, match="hash mismatch"):
        GitHubSkillCatalogProvider(
            source_id="wrong-hash",
            entries=[
                {
                    **_github_entry(content),
                    "sha256": f"sha256:{'0' * 64}",
                }
            ],
            cache_dir=tmp_path / "cache-hash",
            fetcher=lambda _url: content,
        ).list_entries()


def test_github_provider_rejects_symlinked_cache_ancestor(tmp_path: Path) -> None:
    content = _skill_bytes("remote-review")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkillMarketError, match="cache path contains a symbolic link"):
        GitHubSkillCatalogProvider(
            source_id="unsafe-cache",
            entries=[_github_entry(content)],
            cache_dir=linked / "catalog-cache",
            fetcher=lambda _url: content,
        )
    assert not (outside / "catalog-cache").exists()


def test_content_hash_update_is_explicit_and_revokes_previous_authorization(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    first_content = _skill_bytes("remote-review", "First pinned procedure.")
    first = SkillManager(
        workspace,
        catalog=SkillCatalog([_github_provider(tmp_path, first_content)]),
    )
    installed = first.install("github-anthropics-skills/remote-review")
    first.set_authorization(
        installed["catalog_id"],
        "explicit_only",
        expected_digest=installed["digest"],
        expected_revision=1,
    )

    second_content = _skill_bytes("remote-review", "Second pinned procedure.")
    second = SkillManager(
        workspace,
        catalog=SkillCatalog(
            [
                _github_provider(
                    tmp_path,
                    second_content,
                    commit="b" * 40,
                )
            ]
        ),
    )
    listing = second.list_market()["items"][0]
    assert listing["install_state"] == "update_available"

    updated = second.install(
        "github-anthropics-skills/remote-review",
        expected_revision=2,
    )
    assert updated["digest"] != installed["digest"]
    assert updated["enabled"] is False
    assert updated["authorization"]["state"] == "quarantined"
    assert updated["authorization"]["requires_reauthorization"] is True


def test_content_change_under_same_github_revision_is_integrity_conflict(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    first_content = _skill_bytes("remote-review", "First pinned procedure.")
    first = SkillManager(
        workspace,
        catalog=SkillCatalog([_github_provider(tmp_path, first_content)]),
    )
    first.install("github-anthropics-skills/remote-review")

    changed_content = _skill_bytes("remote-review", "Mutated procedure.")
    changed = SkillManager(
        workspace,
        catalog=SkillCatalog([_github_provider(tmp_path, changed_content)]),
    )

    assert changed.list_market()["items"][0]["install_state"] == "integrity_conflict"
    with pytest.raises(
        SkillVersionConflict,
        match="already pinned to a different digest",
    ):
        changed.install(
            "github-anthropics-skills/remote-review",
            expected_revision=1,
        )


def test_catalog_config_loads_only_declared_github_providers(tmp_path: Path) -> None:
    content = _skill_bytes("remote-review")
    config_path = tmp_path / "skill-catalog.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": [
                    {
                        "id": "anthropic-official",
                        "kind": "github",
                        "required": False,
                        "entries": [_github_entry(content)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    providers = load_skill_catalog_config(
        config_path,
        cache_dir=tmp_path / "cache",
        fetcher=lambda _url: content,
    )

    assert len(providers) == 1
    assert providers[0].source_id == "anthropic-official"
    assert providers[0].kind == "github"


def test_shipped_anthropic_catalog_is_immutable_and_standalone(
    tmp_path: Path,
) -> None:
    config_path = (
        Path(__file__).parents[2]
        / "backend"
        / "catalogs"
        / "anthropic.json"
    )
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    entry = raw["providers"][0]["entries"][0]

    assert entry["repository"] == "anthropics/skills"
    assert entry["commit"] == "b29e7cf65e5cb78a5ac33d582270551bc74a14eb"
    assert entry["sha256"] == (
        "sha256:2e47d78846faeea4a56e9809c5270008"
        "7a15a2155a3f293a3efbaded81398ef4"
    )
    assert entry["files"] == ["SKILL.md"]
    providers = load_skill_catalog_config(
        config_path,
        cache_dir=tmp_path / "cache",
        fetcher=lambda _url: b"",
    )
    assert [provider.source_id for provider in providers] == [
        "anthropic-official"
    ]
