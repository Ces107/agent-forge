# Code-review benchmark

18 small pull requests, each a real git repository with a `main` base and a `feature` branch.
14 carry seeded production defects of the kinds that pass a green test suite; 4 are clean changes
that a precise reviewer should approve. Every seeded defect was confirmed at runtime, and every
feature branch passes its own tests.

| Set | Cases | Written by |
|---|---|---|
| dev (`cases.py`) | 10: idempotency race, IDOR, page count, unawaited async, swallowed error + weakened test, SQL injection, naive vs aware datetime, broken caller contract, 2 clean | the skill's author |
| held-out (`cases_holdout.py`) | 8: cross-tenant cache leak, allocation rounding, connection leak, open redirect, inclusive date range, falsy zero override, 2 clean | a separate agent that never saw the skill |

## Method

1. `build.py` materialises each case. Every reviewer gets a fresh copy, the PR title and
   description, and nothing else (no answer key, no other folders).
2. Each skill reviews every case on the same model, following its own instructions.
3. `blind.py` anonymises each pair of reviews as X and Y (random per case) next to the answer key.
4. A separate judge scores every review: seeded defects found, findings reported, real findings,
   false positives, nits, findings backed by a command that was actually run, verdict correctness
   and severity calibration.
5. `score.py` un-blinds and aggregates.

## Results (2026-10-20)

![Same bugs found, far less noise, far more proof](results/2026-10-20/chart.png)

Reviewers: `proven-code-review` (this repo) and `code-reviewer`, the most-installed code-review
skill on Agensi at the time (prompt-only), run verbatim.

**Claude Opus 5.5**

| Reviewer | Seeded bugs found | Findings (real) | False positives | Nits | Real findings proven by a run | Correct verdicts | False alarms on clean PRs |
|---|---|---|---|---|---|---|---|
| proven-code-review | 15/15 | 32 (32) | 0 | 9 | 78% (25/32); Critical+High 19/19 | 18/18 | 0 |
| code-reviewer | 15/15 | 47 (46) | 1 | 18 | 43% (20/46) | 18/18 | 0 |

**Claude Haiku 5.5** (the fast, cheap tier)

| Reviewer | Seeded bugs found | Findings (real) | False positives | Nits | Real findings proven by a run | Correct verdicts | False alarms on clean PRs |
|---|---|---|---|---|---|---|---|
| proven-code-review | 15/15 | 31 (29), precision 94% | 1 | 5 | 76% (22/29); Critical+High 20/22 | 18/18 | 0 |
| code-reviewer | 15/15 | 47 (35), precision 74% | 0 | 25 | 14% (5/35) | 18/18 | 0 |

Raw reviews, blind scores and the X/Y mapping for both rounds: `results/2026-10-20/`.

## What this does and does not show

- **Detection is saturated on these cases.** Current models find all of these seeded defects with
  either skill, on Opus and on Haiku. This benchmark does not show that one skill finds more bugs.
- **What differs is precision and proof.** On Opus: no false positives, half the nits, and every
  Critical and High finding backed by a failing test or command the reviewer ran. On Haiku the gap
  widens: 5x fewer nits, 94% vs 74% precision, and 5x more findings backed by a run. That is what
  lets a human act on a review without re-checking it.
- **Where proven-code-review was wrong:** on Haiku it reported one false positive (deliberate,
  already-tested cent rounding called a Critical under-billing bug) and inflated one severity.
  Version 1.1 tightens both rules (behaviour a test asserts is intended; when torn, pick the lower
  severity). The numbers above are for version 1.0.
- 18 cases is small. Cases are short, single-file-ish PRs in Python and JavaScript. The judge is a
  model, scoring blind against a written answer key; its scores and the raw reviews are kept so the
  numbers can be audited. Add cases and re-run: the scripts are here.
