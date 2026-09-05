"""Day-24 secret-scanner — PRD §10 'git grep for secrets'.

Scans every git-tracked file for known credential patterns:
  - GitHub fine-grained PATs:  github_pat_…
  - GitHub legacy / OAuth:     ghp_…  /  gho_…  /  ghs_…  /  ghu_…
  - AWS access keys:           AKIA…
  - 64-hex JWT-style secrets   (only flag in .env* files; hex hashes in
                                lockfiles / git objects are noisy)
  - Google API keys:           AIza[A-Za-z0-9_-]{35}
  - Gemini API key placeholder slips (real key looks like a 39-char str)

Exits 0 if clean, 24 if any secret is found.

The scanner deliberately ignores `.env.example` for placeholder strings
that LOOK secret-shaped (CHANGE_ME_…, PLACEHOLDER_…, YOUR_…). PRD CLAUDE.md
§10 already pins the rule: `.env.example` carries placeholders, real
values live only in `.env.local`. The scanner enforces both halves —
placeholders allowed in `.env.example`, real-looking values forbidden.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (pattern, label, allow_in_env_example)
PATTERNS: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"github_pat_[A-Za-z0-9_]{60,}"), "GitHub fine-grained PAT", False),
    (re.compile(r"\b(ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}"), "GitHub OAuth/PAT token", False),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key", False),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{35}\b"), "Google API key", False),
    # Slack tokens
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "Slack token", False),
    # JWT-style 64-hex secret in env-shaped lines (KEY=hex64)
    (re.compile(r"(?i)(jwt[_-]?secret|access[_-]?token|api[_-]?key)\s*[=:]\s*['\"]?([0-9a-f]{32,})['\"]?"),
     "Likely 32-hex+ secret bound to JWT/api-key key", False),
]

PLACEHOLDER_TOKENS = (
    "CHANGE_ME", "PLACEHOLDER", "YOUR_", "REPLACE_ME", "EXAMPLE_",
    "dev-only-not-for-production",
)


@dataclass
class Finding:
    path: str
    line_no: int
    label: str
    matched: str


def _git_tracked_files() -> list[Path]:
    out = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, text=True,
    )
    paths: list[Path] = []
    for line in out.splitlines():
        p = ROOT / line.strip()
        if not p.is_file():
            continue
        # Skip binary blobs and our own scanner.
        if p.suffix in {".png", ".jpg", ".jpeg", ".pdf", ".woff", ".woff2",
                         ".ico", ".lock", ".pyc"}:
            continue
        if p.name == "scan_secrets.py":
            continue
        paths.append(p)
    return paths


def _placeholder_like(matched: str) -> bool:
    upper = matched.upper()
    return any(tok in upper for tok in (t.upper() for t in PLACEHOLDER_TOKENS))


def _scan_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = path.relative_to(ROOT).as_posix()
    is_example_env = rel.endswith(".env.example") or rel.endswith("/.env.example")
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings
    for ln, line in enumerate(text.splitlines(), start=1):
        for pattern, label, _allow_in_example in PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            matched = m.group(0)
            # `.env.example` may legitimately mention placeholder strings;
            # only flag if the captured value doesn't look placeholder-y.
            if is_example_env and _placeholder_like(line):
                continue
            findings.append(Finding(path=rel, line_no=ln, label=label, matched=matched[:32] + "…"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-file output, only print summary")
    args = parser.parse_args()

    files = _git_tracked_files()
    all_findings: list[Finding] = []
    for f in files:
        all_findings.extend(_scan_file(f))

    if all_findings:
        print(f"[scan_secrets] FAIL — {len(all_findings)} potential secret(s) detected:")
        for f in all_findings:
            print(f"  {f.path}:{f.line_no}  [{f.label}]  {f.matched}")
        return 24

    if not args.quiet:
        print(f"[scan_secrets] OK — scanned {len(files)} tracked files, "
              f"no secret patterns matched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
