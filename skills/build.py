"""Build marketplace-ready skill bundles (SKILL.md folders + zips) and pre-flight them.

Each skill is a folder ``<name>/`` with a ``SKILL.md`` at its root, following the open SKILL.md
standard (Claude Code, Codex CLI, Cursor, Copilot, Gemini CLI...). This script:

1. **Syncs shared files** into the skill folders from their single source of truth (the gate scripts
   live in ``forge/``; a skill ships a copy). ``--check`` fails instead of writing, so CI catches drift.
2. **Pre-flights** every folder against the structural rules marketplace scanners apply (Agensi's
   8-point scan): valid frontmatter whose ``name`` matches the folder, a real instruction body, no
   symlinks, no unexpected binaries, no obfuscated blobs, no credential shapes, no prompt-injection
   phrasing, no destructive shell idioms.
3. **Zips** each skill as a single top-level folder with ``SKILL.md`` directly inside it.

Usage:  python skills/build.py                      # sync + validate + zip into dist/skills/
        python skills/build.py --check              # validate only, fail on drift (CI)
        python skills/build.py --src ../other/skills --out dist/skills
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = REPO_ROOT / "skills"
DEFAULT_OUT = REPO_ROOT / "dist" / "skills"

# Destination inside a skill -> source path relative to this repository. One source, many copies.
SYNC: dict[str, dict[str, str]] = {
    "spec-trace-gate": {
        "scripts/trace_gate.py": "forge/traceability.py",
    },
    "agent-forge-pipeline": {
        "scripts/trace_gate.py": "forge/traceability.py",
        "scripts/attest.py": "forge/attestation.py",
        "assets/spec-template.md": "skills/spec-trace-gate/assets/spec-template.md",
        "assets/tasks-template.md": "skills/spec-trace-gate/assets/tasks-template.md",
        "assets/REVIEW-template.md": "skills/adversarial-review-gate/assets/REVIEW-template.md",
        "assets/VERIFY-template.md": "skills/verify-gate/assets/VERIFY-template.md",
        "references/ears.md": "skills/spec-trace-gate/references/ears.md",
        "references/attack-surfaces.md": "skills/adversarial-review-gate/references/attack-surfaces.md",
        "references/stack-commands.md": "skills/verify-gate/references/stack-commands.md",
    },
}

TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".py", ".sh", ".yml", ".yaml", ".json", ".toml", ".sql", ".ts", ".js", ".ini", ".cfg"}
)
SKIPPED_NAMES = frozenset({"__pycache__", ".DS_Store", "Thumbs.db", ".pytest_cache", ".hypothesis"})
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MIN_BODY_LINES = 10

# (label, pattern). Shapes only: a match is reported by file:line, never echoed.
FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("obfuscated blob (long base64-like run)", re.compile(r"[A-Za-z0-9+/=]{120,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("cloud access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "API token literal",
        re.compile(r"\b(?:ghp|gho|github_pat|xox[bap]|sk-(?:ant|live|proj))[-_][A-Za-z0-9_-]{16,}"),
    ),
    ("prompt-injection phrasing", re.compile(r"ignore (?:all )?(?:previous|prior) instructions", re.I)),
    ("destructive delete", re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", re.I)),
    ("pipe-to-shell download", re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b")),
    ("privilege escalation", re.compile(r"(?:^|\s)sudo\s")),
)


@dataclass
class SkillReport:
    name: str
    problems: list[str] = field(default_factory=list)
    synced: list[str] = field(default_factory=list)


def discover(src_dirs: Sequence[Path]) -> list[Path]:
    return sorted(
        child for src in src_dirs for child in src.iterdir() if (child / "SKILL.md").is_file()
    )


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (top-level scalar fields, body). Nested YAML values are kept as their raw first line."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if match:
            fields[match.group(1)] = match.group(2).strip().strip("\"'")
    return fields, text[end + 4 :]


def sync(skill: Path, check: bool, report: SkillReport) -> None:
    for dest_rel, source_rel in SYNC.get(skill.name, {}).items():
        source = REPO_ROOT / source_rel
        dest = skill / dest_rel
        wanted = source.read_bytes()
        if dest.exists() and dest.read_bytes() == wanted:
            continue
        if check:
            report.problems.append(f"{dest_rel} is out of sync with {source_rel} (run skills/build.py)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(wanted)
        report.synced.append(dest_rel)


def bundle_files(skill: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(skill.rglob("*")):
        if any(part in SKIPPED_NAMES for part in path.relative_to(skill).parts):
            continue
        if path.is_symlink() or path.is_file():
            files.append(path)
    return files


def validate(skill: Path, report: SkillReport) -> None:
    fields, body = parse_frontmatter((skill / "SKILL.md").read_text(encoding="utf-8"))
    name = fields.get("name", "")
    if name != skill.name:
        report.problems.append(f"frontmatter name {name!r} must equal the folder name {skill.name!r}")
    if not NAME_PATTERN.match(skill.name):
        report.problems.append("folder name must be lowercase letters, digits and hyphens")
    if not fields.get("description"):
        report.problems.append("frontmatter needs a non-empty description")
    if sum(1 for line in body.splitlines() if line.strip()) < MIN_BODY_LINES:
        report.problems.append(f"SKILL.md body has fewer than {MIN_BODY_LINES} instruction lines")

    files = bundle_files(skill)
    if len(files) < 2:
        report.problems.append("ship at least one supporting file (references/, assets/ or scripts/)")
    for path in files:
        rel = path.relative_to(skill).as_posix()
        if path.is_symlink():
            report.problems.append(f"{rel}: symlinks are rejected by marketplace scanners")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            report.problems.append(f"{rel}: unexpected file type (only readable text files ship)")
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for label, pattern in FORBIDDEN:
                if pattern.search(line):
                    report.problems.append(f"{rel}:{number}: {label}")


def write_zip(skill: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{skill.name}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in bundle_files(skill):
            arcname = f"{skill.name}/{path.relative_to(skill).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix in {".py", ".sh"} else 0o644) << 16
            archive.writestr(info, path.read_bytes())
    return target


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync, pre-flight and zip SKILL.md bundles.")
    parser.add_argument("--src", type=Path, action="append", help="folder of skill folders (repeatable)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="validate only; fail on drift, write nothing")
    args = parser.parse_args(list(argv) if argv is not None else None)

    skills = discover(args.src or [DEFAULT_SRC])
    if not skills:
        print("SKILLS: FAIL — no skill folders (with a SKILL.md) found")
        return 1

    failed = 0
    for skill in skills:
        report = SkillReport(skill.name)
        sync(skill, args.check, report)
        validate(skill, report)
        if report.problems:
            failed += 1
            print(f"FAIL  {skill.name}")
            for problem in report.problems:
                print(f"      - {problem}")
            continue
        detail = f" (synced {', '.join(report.synced)})" if report.synced else ""
        if args.check:
            print(f"ok    {skill.name}{detail}")
        else:
            target = write_zip(skill, args.out)
            print(f"ok    {skill.name} -> {target}{detail}")

    verdict = "PASS" if failed == 0 else "FAIL"
    print(f"\nSKILLS: {verdict} — {len(skills) - failed}/{len(skills)} bundles clean")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
