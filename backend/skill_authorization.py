from __future__ import annotations

import hashlib
import json
import re


_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
GRANTED_ACTIVATION_POLICIES = frozenset({"explicit_only", "implicit"})
SKILL_CONTEXT_INJECTION_CAPABILITY = "agent.context.inject"


def skill_principal_id(catalog_id: str, skill_digest: str) -> str:
    """Return the immutable principal represented by exact installed bytes."""

    _validate_identity(catalog_id, skill_digest)
    return f"{catalog_id}@{skill_digest}"


def compute_skill_grant_digest(
    catalog_id: str,
    skill_digest: str,
    activation_policy: str,
) -> str:
    """Hash the complete context-injection grant using canonical JSON."""

    _validate_identity(catalog_id, skill_digest)
    if activation_policy not in GRANTED_ACTIVATION_POLICIES:
        raise ValueError("activation_policy must be explicit_only or implicit")
    payload = {
        "activation_policy": activation_policy,
        "capability": SKILL_CONTEXT_INJECTION_CAPABILITY,
        "catalog_id": catalog_id,
        "skill_digest": skill_digest,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _validate_identity(catalog_id: str, skill_digest: str) -> None:
    if not isinstance(catalog_id, str) or not catalog_id or len(catalog_id) > 192:
        raise ValueError("catalog_id must be a non-empty string of at most 192 characters")
    if not isinstance(skill_digest, str) or _DIGEST_PATTERN.fullmatch(skill_digest) is None:
        raise ValueError(
            "skill_digest must use the form sha256:<64 lowercase hex characters>"
        )
