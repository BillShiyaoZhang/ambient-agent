"""Apply the winning variant to the real source files.

After OFAT picks a winner per choice, this script:

1. Reads the winner recommendations from the latest OFAT report.
2. Renders the winning prompt via the variant's assembly logic.
3. Writes it to backend/agent/prompts/router_v2.md and agent_system.md.
4. Optionally updates router.py for C7 fallback keywords.

Usage:
    python -m scripts.apply_routing_winner --report reports/ofat_phase2.md --apply
    python -m scripts.apply_routing_winner --dry-run  # preview only
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.experiments.variants import all_ofat_variants, variant_by_id


def parse_winners_from_report(report_path: Path) -> dict[str, str]:
    """Parse the OFAT report's 'Apply the winning letter for each choice' section.

    Returns ``{"c1": "B", "c2": "A", ...}``.
    """
    text = report_path.read_text()
    winners: dict[str, str] = {}
    # Pattern: "- **C1** → `B` (score 1.234)"
    for m in re.finditer(r"\*\*C(\d)\*\*\s*→\s*`([ABC])`", text):
        winners[f"c{m.group(1)}"] = m.group(2)
    return winners


def assemble_winner_prompt(winners: dict[str, str]) -> str:
    """Build the prompt for the chosen combination of choices."""
    # The all_ofat_variants uses baseline except for the changed choice.
    # We synthesize the full combination by overriding each choice in turn.
    # Easiest: find an existing OFAT variant that matches exactly, else compose.
    candidates = all_ofat_variants()
    for v in candidates:
        if v.choices == winners:
            return v.system_prompt
    # If no exact match (mix of choices from different OFAT rows), compose
    # by reusing the assembly helper.
    from backend.experiments.variants import _assemble  # type: ignore
    return _assemble(
        c1=winners.get("c1", "A"),
        c4=winners.get("c4", "A"),
        c5=winners.get("c5", "A"),
        c2=winners.get("c2", "A"),
    )


def assemble_winner_agent_system(winners: dict[str, str]) -> str:
    from backend.experiments.variants import _agent_system_for  # type: ignore
    return _agent_system_for(winners.get("c6", "C"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--report", type=Path, default=PROJECT_ROOT / "reports" / "ofat_phase2.md",
                   help="Path to OFAT report markdown (used only if --from-report)")
    p.add_argument("--apply", action="store_true",
                   help="Write changes to disk (default: dry-run)")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview the changes without writing (default behavior)")
    p.add_argument("--from-report", action="store_true",
                   help="Parse winners from --report instead of using DEFAULT_WINNER_CHOICES")
    args = p.parse_args()

    if args.from_report:
        if not args.report.exists():
            print(f"ERROR: report not found: {args.report}", file=sys.stderr)
            sys.exit(1)
        winners = parse_winners_from_report(args.report)
        if not winners:
            print("No winners parsed from report (is OFAT complete?)", file=sys.stderr)
            sys.exit(2)
    else:
        from backend.experiments.variants import DEFAULT_WINNER_CHOICES
        winners = dict(DEFAULT_WINNER_CHOICES)
        print(f"Using DEFAULT_WINNER_CHOICES from variants.py: {winners}")

    print(f"Winner combination: {winners}")
    prompt = assemble_winner_prompt(winners)
    agent_system = assemble_winner_agent_system(winners)

    print(f"\n--- router_v2.md ({len(prompt)} chars) ---")
    print(prompt[:500] + ("..." if len(prompt) > 500 else ""))
    print(f"\n--- agent_system.md ({len(agent_system)} chars) ---")
    print(agent_system[:500] + ("..." if len(agent_system) > 500 else ""))

    if not args.apply:
        print("\nDry-run; pass --apply to write changes.")
        return

    # Backup originals first
    backup_dir = PROJECT_ROOT / "reports" / "routing_winner_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    from datetime import datetime

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    # Write router_v2.md
    router_path = PROJECT_ROOT / "backend" / "agent" / "prompts" / "router_v2.md"
    if router_path.exists():
        shutil.copy2(router_path, backup_dir / f"router_v2.md.{stamp}.bak")
    router_path.write_text(prompt + "\n")
    print(f"\nWrote {router_path} (backup at {backup_dir / f'router_v2.md.{stamp}.bak'})")

    # Write agent_system.md
    agent_path = PROJECT_ROOT / "backend" / "agent" / "prompts" / "agent_system.md"
    if agent_path.exists():
        shutil.copy2(agent_path, backup_dir / f"agent_system.md.{stamp}.bak")
    agent_path.write_text(agent_system + "\n")
    print(f"Wrote {agent_path} (backup at {backup_dir / f'agent_system.md.{stamp}.bak'})")

    print("\nTo revert: cp reports/routing_winner_backup/router_v2.md.<stamp>.bak backend/agent/prompts/router_v2.md")


if __name__ == "__main__":
    main()