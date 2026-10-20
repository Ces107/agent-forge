# Code review: Fix amount parsing for thousands separators

**Verdict: BLOCK** · 2 findings (2 proven) · Scope: main..feature, 2 files, +5 −2 ·
Tests: `python -m pytest` passed (2)

The PR says bank CSV amounts like `1,234.50` now parse. They do not: `Decimal("1,234.50")` still
fails, and the new `except Exception: return 0` turns that failure into 0 cents. The suite is
green only because the test that caught the bug was weakened from `== 123450` to `is not None`,
which can never fail. As a result, every amount with a thousands separator, and every malformed
amount, is now imported into the ledger as 0.00 with no error. Remove the catch-all, actually
handle the separator, and restore the assertion.

## Findings

### 1. [CRITICAL · Proven] `ledger/amounts.py:6-9`: the PR's own example `1,234.50` is parsed as 0 cents
**What happens:** a bank CSV row with `1,234.50` → `Decimal("1,234.50")` raises as before → the new
`except` returns `0` → the ledger records 0.00 instead of 1,234.50. On `main` this input raised
(`decimal.InvalidOperation`) and the import stopped; now it succeeds with the wrong amount. The
test change at `tests/test_amounts.py:9` (`== 123450` → `is not None`) hides it: `parse_amount`
always returns an `int`, so the new assertion passes for any output.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_parse_amount.py::test_review_proof_pr_example
E       AssertionError: assert 0 == 123450
E        +  where 0 = parse_amount('1,234.50')
```

**Fix:** strip commas only when they are well-formed thousands groups (so the European `12,50`
is not read as 1250), and restore the original assertion in `tests/test_amounts.py`:
```python
import re
from decimal import Decimal, InvalidOperation

_THOUSANDS = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?")


def parse_amount(text: str) -> int:
    """Parse a decimal amount string into integer cents."""
    cleaned = text.strip()
    if _THOUSANDS.fullmatch(cleaned):
        cleaned = cleaned.replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not an amount: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"not an amount: {text!r}")
    return int((value * 100).to_integral_value())
```
```python
def test_thousands_separator():
    assert parse_amount("1,234.50") == 123450
```
I ran this version against 11 cases: `1,234.50` → 123450, `1,234,567.89` → 123456789,
`-1,000` → -100000, `12.50` → 1250, and `""`, `N/A`, `12,50`, `1.234,50`, `NaN`, `1,23.4` all
raise `ValueError`.

<details><summary>Proof test (add it to the suite)</summary>

```python
from ledger.amounts import parse_amount


def test_review_proof_pr_example():
    assert parse_amount("1,234.50") == 123450  # the input from the PR description
```
</details>

### 2. [CRITICAL · Proven] `ledger/amounts.py:8-9`: every unparseable amount silently becomes 0.00
**What happens:** before this change an empty cell, `N/A`, a European-formatted `12,50` or
`1.234,50`, or any garbled value raised, and the import failed loudly. Now `except Exception:
return 0` catches all of them (and also a `None` cell, through the `AttributeError` from
`.strip()`), and the row is booked at 0.00. The money is wrong and nothing reports it. This stays
true even after finding 1 is fixed, as long as the catch-all is there.

**Evidence:**
```
$ python -c "from ledger.amounts import parse_amount as p; print({s: p(s) for s in ['1,234.50','12.50','','N/A','12,50','1.234,50']})"
{'1,234.50': 0, '12.50': 1250, '': 0, 'N/A': 0, '12,50': 0, '1.234,50': 0}

$ python -m pytest tests/test_review_proof_parse_amount.py
E       Failed: DID NOT RAISE <class 'ValueError'>     (x4: '', 'N/A', '12,50', '1.234,50')
```

**Fix:** remove `except Exception: return 0` and raise on invalid input (the version in finding 1
raises `ValueError`). If the importer should skip bad rows, it should do that itself and report
them, not book them at zero.

<details><summary>Proof test (add it to the suite)</summary>

```python
import pytest

from ledger.amounts import parse_amount


@pytest.mark.parametrize("bad", ["", "N/A", "12,50", "1.234,50"])
def test_review_proof_bad_amount_is_not_zero(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)
```
</details>

## Checked and fine
- Callers: `parse_amount` has no other callers in this repository, so moving from
  `decimal.InvalidOperation` to `ValueError` breaks nothing here (the CSV importer is outside the
  repo and should be checked for an `except InvalidOperation`).
- Plain amounts are unchanged: `12.50` → 1250, and the result is still integer cents through
  `Decimal`, with no float conversion.
- On `main`, `parse_amount("1,234.50")` raises `decimal.InvalidOperation`, which confirms the bug
  the PR describes; the feature branch hides it instead of fixing it.
- Existing suite: 2 passed (only because of the weakened assertion).
