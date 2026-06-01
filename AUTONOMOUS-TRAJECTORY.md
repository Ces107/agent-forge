# Autonomous trajectory — how the ledger was built

This repo's inner project (`workspaces/ledger/`) was built end-to-end by a Claude Code
multi-agent pipeline. A human (operator) wrote only the scaffold and the one-line mandate, and
reviewed every diff; **zero lines of the ledger's application code were written by hand.** This
document is the audit trail, and every claim below links to a commit you can check with `git show`.

## The headline: the adversarial agent caught a real concurrency bug

The most important thing a multi-agent pipeline must prove is that the review stage **adds value**
— that it catches what the implementer missed, rather than rubber-stamping. Here is that moment,
end to end:

1. The **Implementer** built the ledger with a `BEGIN IMMEDIATE` transaction that correctly
   serialises writes and handles duplicate idempotency keys — commit
   [`443bbbd`](. "git show 443bbbd"). Its own test layer (20 tests) passed.
2. The **AdversarialReviewer** attacked the concurrency surface and reproduced **TD-001**: under
   write-lock contention beyond `busy_timeout`, `BEGIN IMMEDIATE` raises
   `sqlite3.OperationalError: database is locked`, which leaked unhandled (HTTP 500) — directly
   violating the spec's guarantee of *"exactly-once money movement under concurrent retry storms."*
   It committed a **failing** regression test as proof — commit
   [`708ee99`](. "git show 708ee99"), `GATE: FAIL`. (That commit is red on purpose; the red is the
   evidence.) See [`forge/reviews/REVIEW-001.md`](forge/reviews/REVIEW-001.md).
3. The **Implementer** fixed it — bounded retry-with-backoff on the lock, a typed
   `ServiceUnavailable` mapped to HTTP 503 — without weakening the reviewer's test. Commit
   [`49fa176`](. "git show 49fa176").
4. The **Verifier** ran the full gate itself and only then passed it — commit
   [`998e7f4`](. "git show 998e7f4"), `VERIFY: PASS`.

```
git show 708ee99   # the caught bug: failing test + REVIEW-001
git show 49fa176   # the fix: retry/backoff, the reviewer's test now green
```

The reproducing test — `test_busy_timeout_does_not_leak_operational_error` in
`workspaces/ledger/tests/test_concurrency.py` — now guards the fix permanently.

## The pipeline, stage by stage

| # | Stage | Model | Output | Commit |
|---|-------|-------|--------|--------|
| 0 | Operator scaffold | (human) | harness, gates, CI, spec | `5dd7fea`, `2abc995` |
| 1 | Architect | opus | ADR-001..003 (schema, idempotency, concurrency) | `8524691` |
| 2 | Implementer | sonnet | ledger core + FastAPI + first tests (20 passed) | `443bbbd` |
| 3 | AdversarialReviewer | sonnet | **GATE: FAIL** — TD-001 reproduced | `708ee99` |
| 4 | Implementer (fix) | sonnet | TD-001/002/003 resolved | `49fa176` |
| 5 | Verifier | sonnet | **VERIFY: PASS** — 45 tests, 97.9% cov | `998e7f4` |

The per-stage spawn audit (model + ROI rationale per agent) is in
[`forge/spawn-log.jsonl`](forge/spawn-log.jsonl).

## The gates have teeth (this is the part that is usually faked)

- **AdversarialReview gate** did not return "looks fine" — it returned a reproduced defect with a
  file:line root cause and a failing test. See `forge/reviews/REVIEW-001.md`.
- **Verify gate** *halted the pipeline twice* before passing: first on coverage 68% < 80
  (`api.py` had no tests) and 11 `ruff` findings, then green after a hardening pass. The gate
  measured substance — coverage, lint, types, security — not shape. See
  [`forge/reviews/VERIFY-001.md`](forge/reviews/VERIFY-001.md).
- All defects are tracked in [`state/tech-debt-backlog.md`](state/tech-debt-backlog.md)
  (TD-001 HIGH, TD-002/003 LOW), each RESOLVED with a verifiable fix.

## How deep was the bug, honestly?

Owning this matters more than overselling it. The caught defect (an unhandled `OperationalError` on
lock-timeout leaking as HTTP 500) is a **code-review-level find**, not a subtle invariant violation. A
careful human reviewer would also catch it. What makes it worth showing is not its depth but the
*loop*: an adversarial agent reproduced it with a committed red test, the implementer fixed it without
weakening that test, and the verifier proved it green, all auditable commit-by-commit.

The harder correctness problems live one level down, and the property model is what guards them:
a TOCTOU race on the idempotency check (two duplicates both seeing "key absent" and both posting),
a lost update on a shared balance under interleaving, and a partial transfer surviving a crash. Those
cannot be caught by reading the code; they are caught by the Hypothesis `RuleBasedStateMachine`
asserting the five double-entry invariants after every step, and by the crash-recovery test that kills
the process between the two postings. That is the real correctness signal; the leaked-`OperationalError`
story is just the most *legible* one.

One limitation is documented rather than hidden, as **F-002** in `forge/reviews/REVIEW-001.md`: a
transfer that fails validation (for example `AccountNotFound`) rolls back its already-claimed
idempotency key, so a retry with the same key re-executes and re-raises rather than replaying the
original error. Stripe caches error responses under an idempotency key; this ledger deliberately does
not. It is a defended design choice, not a leak.

Since this run, the suite was hardened from 45 to **50 tests** (the crash-recovery test the spec
promised, plus a `POST /accounts` endpoint that makes the HTTP demo runnable end to end), still at
~98% coverage with `ruff` + `mypy --strict` + `bandit` clean.

## How to verify this yourself in 5 minutes

```bash
# 1. Provenance — every app-code commit is agent-authored (machine-enforced, not a claim)
python hooks/audit_provenance.py
git log --format='%an  %s' -- workspaces/ledger/src

# 2. It actually runs and the invariants hold
cd workspaces/ledger && pip install -e ".[dev]" && python -m pytest

# 3. Walk the caught-bug chain
git show 708ee99      # bug caught (failing test)
git show 49fa176      # bug fixed (test green, not weakened)
```

## What the human did vs. what the agents did

- **Human (operator):** wrote the harness scaffold, the one-line mandate, and the acceptance spec
  (the *what*); ran the pipeline; reviewed every diff as engineer-of-record. Authored only the two
  `chore:` scaffold commits — verifiable via `git log`.
- **Agents:** every ADR, every line of ledger application code, every test, the bug that was
  caught, and the fix. Authored under per-role identities (`*@agent-forge.bot`) with a
  `Co-Authored-By: Claude` trailer, never squashed into a flat history.
