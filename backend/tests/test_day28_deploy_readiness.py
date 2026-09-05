"""Day-28 deployment-readiness acceptance tests — PRD §10.

PRD §10 Day-28 Done When: 'Clean production demo without manual fixes.
Demo video recorded.'

The actual video recording is a human gate. What this test locks is the
'clean production demo without manual fixes' half: the quick deploy-check
must pass, and the artefacts the storyboard depends on must be in place.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_deploy_check_quick_path_passes() -> None:
    """env.example clean + secret-scan clean + seed_dry_run < 60s.

    This is the inner-loop gate. The full slow path (all scripts, pytest,
    frontend) is exercised separately by `scripts/deploy_check.py`
    without --quick — too slow for CI per-run but required before a
    production push."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "deploy_check.py"),
         "--quick", "--skip-frontend"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, (
        f"deploy_check --quick failed (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_demo_storyboard_present_and_non_empty() -> None:
    """The fallback demo script must exist and be substantive — judges read
    this if the live demo fails."""
    path = ROOT / "docs" / "DEMO_SCRIPT.md"
    assert path.exists(), "docs/DEMO_SCRIPT.md missing — PRD §10 Day-28 deliverable"
    text = path.read_text(encoding="utf-8")
    # All seven demo phases per PRD §14 must be marked in the storyboard.
    for marker in (
        "Company Search",
        "Analysis Dashboard",
        "Graph Explorer",
        "Evidence Provenance",
        "ITC Carousel",
        "Evergreening",
        "Report Export",
    ):
        assert marker in text, f"Demo storyboard missing phase: {marker!r}"
    # Pre-flight commands must reference --clean (the Day-28 clean-slate path).
    assert "--clean" in text, "Storyboard must call out --clean reseed path"


def test_seed_neo4j_exposes_clean_flag() -> None:
    """PRD §10 Day-28: 'Full demo seed on clean production Neo4j.'"""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed_neo4j.py"), "--help"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "--clean" in result.stdout, (
        f"seed_neo4j.py --help must advertise --clean; got:\n{result.stdout}"
    )


def test_deploy_check_audit_json_is_structured() -> None:
    """The deploy-check writes an audit JSON; production operators read it
    before pushing. Verify it parses + records each fast check."""
    audit_path = ROOT / "data" / "audits" / "day28_deploy_check.json"
    if not audit_path.exists():
        pytest.skip("deploy_check audit not yet generated; "
                    "run scripts/deploy_check.py --quick first")
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert "results" in payload
    labels = {r["label"] for r in payload["results"]}
    for required in ("env_example_placeholder_only", "secret_scan", "seed_dry_run"):
        assert required in labels, f"deploy_check audit missing label {required!r}"
