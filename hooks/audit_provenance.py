"""Provenance audit — machine-enforced proof that the inner project was agent-built.

Asserts that every commit which touches application code under ``workspaces/ledger/src``
carries agent authorship: either an author whose email is under ``@agent-forge.bot`` or a
``Co-Authored-By: Claude`` trailer in the commit message. Commits that touch ONLY scaffold /
docs / harness files are allowed to be human-authored.

This runs in CI (see .github/workflows/ci.yml) and can be wired as a local git hook. It makes
the "100% built by Claude Code" claim falsifiable from ``git log`` alone — run it and it either
exits 0 or names the offending commits.

Usage:  python hooks/audit_provenance.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_CODE_PREFIX = "workspaces/ledger/src"
AGENT_EMAIL_DOMAIN = "@agent-forge.bot"
COAUTHOR_MARKER = "Co-Authored-By: Claude"
CHAIN_PATH = REPO_ROOT / "forge" / "attestations" / "chain.ndjson"
HEAD_PATH = REPO_ROOT / "forge" / "attestations" / "HEAD"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def commits_touching_app_code() -> list[str]:
    out = _git("log", "--format=%H", "--", APP_CODE_PREFIX)
    return [line for line in out.splitlines() if line]


def commit_is_agent_authored(sha: str) -> bool:
    author_email = _git("show", "-s", "--format=%ae", sha).strip()
    body = _git("show", "-s", "--format=%B", sha)
    return author_email.endswith(AGENT_EMAIL_DOMAIN) or COAUTHOR_MARKER in body


def audit_attestation_chain() -> tuple[bool, str]:
    """Defence in depth: the email convention above is spoofable in one line of `git config`; the
    attestation chain is not. Re-derive every artifact digest from the tree, walk the hash chain,
    and confirm the head matches the anchor committed to git. See ``forge/attestation.py``.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from forge.attestation import filesystem_resolver, parse, verify

    if not CHAIN_PATH.exists():
        return True, "no attestation chain present (skipped)"

    result = verify(parse(CHAIN_PATH.read_text(encoding="utf-8")), filesystem_resolver(REPO_ROOT))
    if not result.ok:
        return False, result.render()
    if HEAD_PATH.exists():
        anchored = HEAD_PATH.read_text(encoding="utf-8").strip()
        if anchored != result.head:
            return False, f"chain head {result.head[:12]}… != committed anchor {anchored[:12]}…"
    return True, result.render()


def main() -> int:
    offenders: list[str] = []
    shas = commits_touching_app_code()
    for sha in shas:
        if not commit_is_agent_authored(sha):
            subject = _git("show", "-s", "--format=%s", sha).strip()
            offenders.append(f"{sha[:10]}  {subject}")

    if offenders:
        print("PROVENANCE: FAIL — application-code commits without agent authorship:")
        for line in offenders:
            print(f"  {line}")
        return 1

    chain_ok, chain_msg = audit_attestation_chain()
    if not chain_ok:
        print(f"PROVENANCE: FAIL — tamper-evident attestation chain broken: {chain_msg}")
        return 1

    print(f"PROVENANCE: PASS — all {len(shas)} app-code commits carry agent authorship.")
    print(f"  attestation chain: {chain_msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
