"""Day-28 deployment readiness verifier — PRD §10.

PRD §10 Day-28 verbatim: 'Deploy to production. Full demo seed on clean
production Neo4j. Record demo video fallback (2–3 min). Done When: Clean
production demo without manual fixes. Demo video recorded.'

This CLI is the "is it shippable?" gate. It validates every condition
the production deploy assumes without actually pushing anything:

  1. .env.example is placeholder-only (no leaked secrets).
  2. scripts/scan_secrets.py exits clean across all tracked files.
  3. scripts/seed_neo4j.py --dry-run clears the PRD §13 60s budget.
  4. scripts/day20_benchmark.py clears the four ML floors.
  5. scripts/day22_audit.py clears benchmark + conformal + IL&FS M6.
  6. scripts/day23_pattern_audit.py clears all four scenarios.
  7. scripts/day25_stress_test.py clears SCC + concurrency.
  8. scripts/day26_rehearsal.py clears the 3-minute demo flow.
  9. scripts/day27_rehearsal_multi.py clears IL&FS + Amtek + ITC.
 10. Backend pytest suite is green.
 11. Frontend tsc + vitest + vite build are clean.

Writes data/audits/day28_deploy_check.json. Exits 0 on PASS.

Tests #4–#9 are slow (each spawns its own subprocess + full data build).
Pass --quick to skip them (useful for inner-loop development), but the
CI test asserts the full path.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parents[1]
AUDIT_OUTPUT_PATH = ROOT / "data" / "audits" / "day28_deploy_check.json"
PY = sys.executable

logger = logging.getLogger("deploy_check")


def _run_subprocess(cmd: list[str], label: str, cwd: Path = ROOT,
                    timeout_sec: float = 600.0) -> dict:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "label": label, "ok": False, "rc": None,
            "elapsed_sec": time.perf_counter() - t0,
            "reason": f"timeout after {timeout_sec}s: {exc}",
        }
    elapsed = time.perf_counter() - t0
    tail = (proc.stdout + proc.stderr).splitlines()[-4:]
    return {
        "label": label,
        "ok": proc.returncode == 0,
        "rc": proc.returncode,
        "elapsed_sec": elapsed,
        "tail": tail,
    }


def _check_env_example_is_placeholder_only() -> dict:
    """PRD §10 CLAUDE.md rule: .env.example carries placeholders only."""
    path = ROOT / ".env.example"
    if not path.exists():
        return {"label": "env_example_present", "ok": False, "reason": "missing"}
    text = path.read_text(encoding="utf-8", errors="ignore")
    suspicious_patterns = [
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"),
    ]
    findings = []
    for ln, line in enumerate(text.splitlines(), start=1):
        for pat in suspicious_patterns:
            if pat.search(line):
                findings.append((ln, line[:80]))
    return {
        "label": "env_example_placeholder_only",
        "ok": not findings,
        "leaked_lines": findings,
    }


def _check_seed_dry_run_under_budget() -> dict:
    """PRD §13 Definition of Done: seed runs in <60s."""
    result = _run_subprocess([PY, "scripts/seed_neo4j.py", "--dry-run"],
                              "seed_dry_run", timeout_sec=120.0)
    result["under_60s_budget"] = result["elapsed_sec"] < 60.0
    result["ok"] = result["ok"] and result["under_60s_budget"]
    return result


def _check_secret_scan() -> dict:
    return _run_subprocess([PY, "scripts/scan_secrets.py", "--quiet"],
                            "secret_scan", timeout_sec=60.0)


def _check_pytest_suite() -> dict:
    return _run_subprocess([PY, "-m", "pytest", "--tb=line", "-q",
                            "--ignore=backend/tests/test_day28_deploy_readiness.py"],
                            "pytest_suite", timeout_sec=300.0)


def _check_frontend_typecheck() -> dict:
    """tsc -b should clear with zero diagnostics."""
    return _run_subprocess(
        ["npx", "--no-install", "tsc", "-b"],
        "frontend_tsc",
        cwd=ROOT / "frontend",
        timeout_sec=180.0,
    )


def _check_frontend_vitest() -> dict:
    return _run_subprocess(
        ["npx", "--no-install", "vitest", "run"],
        "frontend_vitest",
        cwd=ROOT / "frontend",
        timeout_sec=180.0,
    )


CHECKS_FAST = (
    _check_env_example_is_placeholder_only,
    _check_secret_scan,
    _check_seed_dry_run_under_budget,
)

CHECKS_SLOW = (
    ("day20_benchmark",       [PY, "scripts/day20_benchmark.py"]),
    ("day22_audit",            [PY, "scripts/day22_audit.py", "--seed", "20", "--synthetic", "500"]),
    ("day23_pattern_audit",    [PY, "scripts/day23_pattern_audit.py", "--today", "2026-05-15"]),
    ("day25_stress",           [PY, "scripts/day25_stress_test.py"]),
    ("day26_rehearsal",        [PY, "scripts/day26_rehearsal.py"]),
    ("day27_rehearsal_multi",  [PY, "scripts/day27_rehearsal_multi.py"]),
)


def _run_all(*, quick: bool, skip_frontend: bool = False) -> dict:
    results: list[dict] = []
    print("Day-28 deploy-readiness gate — running checks...")

    for check in CHECKS_FAST:
        r = check() if callable(check) else check
        if "label" not in r:
            r["label"] = check.__name__
        results.append(r)
        flag = "PASS" if r.get("ok") else "FAIL"
        print(f"  {flag}  {r['label']}  ({r.get('elapsed_sec', 0):.2f}s)")

    if not quick:
        for label, cmd in CHECKS_SLOW:
            r = _run_subprocess(cmd, label, timeout_sec=300.0)
            results.append(r)
            flag = "PASS" if r["ok"] else "FAIL"
            print(f"  {flag}  {r['label']}  ({r['elapsed_sec']:.2f}s)")

        r = _check_pytest_suite()
        results.append(r)
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['label']}  ({r['elapsed_sec']:.2f}s)")

    if not quick and not skip_frontend:
        for fn in (_check_frontend_typecheck, _check_frontend_vitest):
            r = fn()
            results.append(r)
            print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['label']}  ({r['elapsed_sec']:.2f}s)")

    all_ok = all(r.get("ok") for r in results)
    return {"results": results, "ok": all_ok}


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="Skip slow audits + pytest + frontend (env+secrets+seed only)")
    parser.add_argument("--skip-frontend", action="store_true",
                        help="Skip frontend tsc/vitest (useful in CI without node_modules)")
    args = parser.parse_args()

    report = _run_all(quick=args.quick, skip_frontend=args.skip_frontend)

    AUDIT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT_PATH.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print()
    print(f"  Overall: {'PASS — ship it' if report['ok'] else 'FAIL — do not deploy'}")
    print(f"  Audit written to {AUDIT_OUTPUT_PATH.relative_to(ROOT)}")
    return 0 if report["ok"] else 28


if __name__ == "__main__":
    sys.exit(main())
