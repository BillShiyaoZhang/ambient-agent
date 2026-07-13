"""Unit tests for prompt variants."""

from backend.experiments.variants import (
    BASELINE_CHOICES,
    all_ofat_variants,
    variant_by_id,
)


def test_baseline_choices_present():
    for k in ("c1", "c2", "c3", "c4", "c5", "c6", "c7"):
        assert k in BASELINE_CHOICES


def test_ofat_variants_count():
    """Should produce 1 baseline + 7 choices × up to 3 candidates."""
    variants = all_ofat_variants()
    # 1 baseline + (3 for C1) + (3 for C2) + (3 for C3) + (3 for C4) +
    # (3 for C5) + (3 for C6) + (3 for C7) = 22
    assert len(variants) >= 22


def test_ofat_variants_have_unique_ids():
    variants = all_ofat_variants()
    ids = [v.id for v in variants]
    assert len(ids) == len(set(ids))


def test_variant_by_id():
    v = variant_by_id("V_baseline")
    assert v.id == "V_baseline"


def test_all_variants_have_nonempty_prompt():
    for v in all_ofat_variants():
        assert v.system_prompt.strip(), f"{v.id} has empty system_prompt"
        # Prompt should mention classify_intent OR mention the routing decision list.
        assert "kind" in v.system_prompt or "Routing" in v.system_prompt, (
            f"{v.id} system_prompt doesn't reference routing kind"
        )


def test_router_context_placeholder_present():
    """All variants should include {{ router_context }} for the runner to substitute."""
    for v in all_ofat_variants():
        assert "{{ router_context }}" in v.system_prompt, f"{v.id} missing router_context placeholder"


def test_choice_keys_are_valid():
    """All variants should only use c1..c7 as choice keys."""
    expected_keys = {"c1", "c2", "c3", "c4", "c5", "c6", "c7"}
    for v in all_ofat_variants():
        assert set(v.choices.keys()) == expected_keys, (
            f"{v.id} has unexpected choice keys: {set(v.choices.keys())}"
        )


def test_baseline_choices_all_a():
    """Baseline (current router_v2.md) should be A/A/A/A/A/C/A."""
    assert BASELINE_CHOICES == {"c1": "A", "c2": "A", "c3": "A", "c4": "A", "c5": "A", "c6": "C", "c7": "A"}


def test_c1_vary_only_c1():
    variants = all_ofat_variants()
    c1_variants = [v for v in variants if v.id.startswith("V_C1")]
    assert len(c1_variants) == 3
    for v in c1_variants:
        assert v.choices["c1"] in ("A", "B", "C")
        # All other choices must equal baseline
        for k in ("c2", "c3", "c4", "c5", "c6", "c7"):
            assert v.choices[k] == BASELINE_CHOICES[k], f"{v.id} not OFAT-isolated on {k}"
