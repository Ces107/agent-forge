# Code review: Fix amount parsing for thousands separators

**Verdict: BLOCK** · 2 findings (1 proven, 1 traced) · Scope: main..feature, 2 files, 5 insertions(+), 2 deletions(-) · Tests: `python -m pytest` passed (2 tests), but only because the failing test was weakened

The PR says imports of `1,234.50` now work. They do not: the new `except Exception: return 0` turns that
input into `0` cents, and every other unparseable amount becomes `0` too. The one test that covered the
separator was changed from an exact value to `is not None`, which any integer satisfies, so the suite is
green while the bug is still there. For a bank import, that is a silent wrong-money error.

## Findings

### 1. [CRITICAL · Proven] `ledger/amounts.py:6-9`: unparseable amounts are recorded as zero
**What happens:** the bank CSV contains `1,234.50` → `Decimal("1,234.50")` raises `InvalidOperation` →
the blanket `except Exception` returns `0` → the transaction is imported as a zero-value entry, with no
error and no log line. The same happens for any garbage input (`n/a`, a stray header, an empty cell), so
bad rows reach the ledger as zero instead of failing the import.

**Evidence:**
```
$ python -c 'from ledger.amounts import parse_amount as p; print(repr(p("1,234.50")), repr(p("n/a")), repr(p("")))'
0 0 0
```
Proof test, run against the feature branch (proof file deleted afterwards):
```
>       assert parse_amount("1,234.50") == 123450
E       assert 0 == 123450
>       assert parse_amount("n/a") != 0
E       assert 0 != 0
2 failed in 0.99s
```

**Fix:** remove the separator explicitly, and let invalid input raise:
```python
from decimal import Decimal


def parse_amount(text: str) -> int:
    """Parse a decimal amount string into integer cents. Raises on invalid input."""
    value = Decimal(text.strip().replace(",", ""))
    return int((value * 100).to_integral_value())
```
Note the locale question below before merging this.

<details><summary>Proof test (add it to the suite)</summary>

```python
from ledger.amounts import parse_amount


def test_review_proof_thousands_separator_is_parsed():
    assert parse_amount("1,234.50") == 123450


def test_review_proof_garbage_is_not_zero():
    assert parse_amount("n/a") != 0
```
</details>

### 2. [HIGH · Traced] `tests/test_amounts.py:9`: the test was weakened so the bug cannot fail it
**What happens:** the separator test asserted `parse_amount("1,234.50") == 123450` on `main`. The PR changed
it to `is not None` → `0` is not `None`, so the test passes for the wrong value → any future regression in
the separator handling passes CI unnoticed. The PR's "the whole suite is green again" is true only because
of this edit.

**Evidence:** `git diff main...feature -- tests/test_amounts.py` shows the single-line change:
```diff
-    assert parse_amount("1,234.50") == 123450
+    assert parse_amount("1,234.50") is not None
```
Traced: `ledger/amounts.py:6-9` returns `0` for this input, and `0 is not None` is true.

**Fix:** restore the exact assertion. It is the test the change must pass:
```python
def test_thousands_separator():
    assert parse_amount("1,234.50") == 123450
```

## Questions for the author
- Is the bank CSV always in `1,234.50` (comma thousands, dot decimal) format? If any account exports
  European `1.234,50`, the `.replace(",", "")` fix turns it into `123` cents silently. Parse with an explicit
  format per source, or reject the ambiguous case.
- Is `parse_amount` used anywhere else? This repository has no other caller, so I could not check the
  import path that reported the original failure.

## Checked and fine
- Plain decimals still parse exactly: `parse_amount("12.50") == 1250` (`tests/test_amounts.py:4-5`, passing).
- The rounding path (`to_integral_value()`) is unchanged, so the cents conversion for a valid value is the same
  as on `main`.

## Nits
- None.
