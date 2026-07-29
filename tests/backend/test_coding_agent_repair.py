from backend.coding_agent_repair import (
    RepairFinding,
    decide_widget_repair,
    finding_from_exception,
    repair_finding_from_dict,
)


class VerifierError(RuntimeError):
    code = "widget_verification_failed"
    stage = "static_verify"


def finding(message: str, *, attempt: int, artifact: str) -> RepairFinding:
    return finding_from_exception(
        VerifierError(message),
        attempt=attempt,
        artifact_revision=artifact,
    )


def test_finding_signature_only_ignores_whitespace_not_source_locations() -> None:
    first = finding("Unexpected token (980:3)\n  980 | `;", attempt=1, artifact="one")
    whitespace_variant = finding("Unexpected   token (980:3) 980 | `;", attempt=2, artifact="two")
    moved_finding = finding("Unexpected token (1100:3)\n  1100 | `;", attempt=3, artifact="three")

    assert first.signature == whitespace_variant.signature
    assert first.signature != moved_finding.signature


def test_distinct_findings_keep_repairing_beyond_the_old_three_turn_limit() -> None:
    history = tuple(
        finding(f"Verifier issue at line {index}", attempt=index, artifact=f"artifact-{index}") for index in range(1, 8)
    )
    current = finding("Verifier issue at line 8", attempt=8, artifact="artifact-8")

    assert decide_widget_repair(current, history).action == "repair"


def test_consecutive_identical_finding_stops_automatic_repair() -> None:
    previous = finding("Unexpected token (980:3)", attempt=1, artifact="artifact-one")
    current = finding("Unexpected token (980:3)", attempt=2, artifact="artifact-two")

    directive = decide_widget_repair(current, (previous,))

    assert directive.action == "human"
    assert "same verifier finding" in directive.reason


def test_persisted_finding_round_trips_for_cross_run_stall_detection() -> None:
    original = finding("Unexpected token (980:3)", attempt=4, artifact="artifact-four")

    restored = repair_finding_from_dict(original.to_dict())

    assert restored == original
