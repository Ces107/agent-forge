"""Materialise the benchmark cases as git repositories: `main` holds the base, `feature` the PR.

Usage:  python bench/code-review/build.py <output-dir>

Each repository is left checked out on `feature`. The PR title and description are the commit
message of the feature commit and are also written to `<output-dir>/<case>.pr.md`, so a reviewer
gets what a real pull request would show. The answer key is never copied.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import CASES, Case  # noqa: E402

GIT_IDENTITY = ["-c", "user.name=Bench Author", "-c", "user.email=author@example.com"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_IDENTITY, *args], cwd=repo, check=True, capture_output=True)  # nosec B603 B607


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def build_case(case: Case, out: Path) -> Path:
    repo = out / case.case_id
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, case.base)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial import")
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, case.feature)
    for rel in case.removed:
        (repo / rel).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"{case.title}\n\n{case.body}")
    (out / f"{case.case_id}.pr.md").write_text(
        f"# {case.title}\n\n{case.body}\n\nBase: main · Head: feature · Tests: `{case.test_command}`\n",
        encoding="utf-8",
    )
    return repo


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    out = Path(argv[0])
    if out.exists() and any(out.iterdir()):
        print(f"{out} is not empty")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        print(f"built {build_case(case, out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
