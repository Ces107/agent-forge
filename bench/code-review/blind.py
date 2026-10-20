"""Prepare a blind judging packet: per case, the answer key plus two anonymised reviews (X and Y).

Usage:  python bench/code-review/blind.py <runs-dir> <out-dir> [--seed N]

<runs-dir>/A/reviews/<case>.md and <runs-dir>/B/reviews/<case>.md are copied to
<out-dir>/<case>/X.md and Y.md in a random order per case; the mapping goes to
<out-dir>/../mapping.json, outside the folder the judge reads.
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    runs, out = Path(argv[0]), Path(argv[1])
    seed = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 20261020
    rng = random.Random(seed)
    answers = {}
    for name in ("answers.json", "answers_holdout.json"):
        key_file = json.loads((HERE / name).read_text(encoding="utf-8"))
        answers.update({k: v for k, v in key_file.items() if not k.startswith("_")})
    out.mkdir(parents=True, exist_ok=False)
    mapping: dict[str, dict[str, str]] = {}
    for case_id, key in sorted(answers.items()):
        a, b = runs / "A" / "reviews" / f"{case_id}.md", runs / "B" / "reviews" / f"{case_id}.md"
        if not (a.exists() and b.exists()):
            print(f"skip {case_id}: missing review")
            continue
        order = [("A", a), ("B", b)]
        rng.shuffle(order)
        case_dir = out / case_id
        case_dir.mkdir()
        for label, (_, path) in zip(("X", "Y"), order, strict=True):
            shutil.copyfile(path, case_dir / f"{label}.md")
        (case_dir / "answer_key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
        mapping[case_id] = {"X": order[0][0], "Y": order[1][0]}
    (out.parent / "mapping.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print(f"{len(mapping)} cases -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
