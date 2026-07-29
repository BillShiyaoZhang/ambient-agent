"""Domain contracts for least-authority Widget capabilities."""

from backend.capabilities.models import CapabilityGrant, RuntimeContract, grants_digest, normalize_grants
from backend.capabilities.ontology import CAPABILITY_ONTOLOGY, CapabilityCategory, capability_category_ids
from backend.capabilities.policy import CapabilityAuthorizer, CapabilityDenied

__all__ = [
    "CAPABILITY_ONTOLOGY",
    "CapabilityAuthorizer",
    "CapabilityCategory",
    "CapabilityDenied",
    "CapabilityGrant",
    "RuntimeContract",
    "capability_category_ids",
    "grants_digest",
    "normalize_grants",
]
