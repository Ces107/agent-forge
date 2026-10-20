## Review Summary

The PR does not fix the bug it describes. `parse_amount("1,234.50")` still returns `0`, not `123450`, because the thousands separator is never removed. The fix instead wraps the parse in a bare `except Exception: return 0`, so any unparseable bank amount is imported as zero, which is silent ledger corruption. The test was loosened from an exact value to `is not None`, and that is why the suite is green. Request changes, and do not merge this as "suite green".

### Critical (must fix before merge)

**ledger/amounts.py:6-9 (`parse_amount`)** — The `except Exception: return 0` swallows every parse error. Confirmed: `parse_amount("abc")` returns `0`. A malformed line in a bank CSV becomes a zero-value transaction with no error, and the failure cannot be seen downstream. Errors must propagate, or the caller must receive an explicit failure.

**ledger/amounts.py:7** — The thousands separator is not handled, so the reported bug remains: `"1,234.50"` raises inside `Decimal`, the handler returns `0`, and the import still records zero. Confirmed by running it.

**tests/test_amounts.py:9** — The assertion was changed from `== 123450` to `is not None`. Any return value passes that, including the `0` the bug produces. The test was weakened to make it pass; restore the exact value.

Fix: strip the separator explicitly and let real errors raise.

```python
def parse_amount(text: str) -> int:
    """Parse a decimal amount string, allowing comma thousands separators, into integer cents."""
    value = Decimal(text.strip().replace(",", ""))
    return int((value * 100).to_integral_value())
```

```python
def test_thousands_separator():
    assert parse_amount("1,234.50") == 123450
```

### Suggestions (should fix)

**ledger/amounts.py** — Stripping every comma assumes US-style formatting. If the bank exports European amounts (`1.234,50`), this will misparse them without an error. Confirm the source format and add a test for the bank's real sample line.

**tests/test_amounts.py** — Add a test that malformed input raises (for example `parse_amount("abc")` raises `decimal.InvalidOperation`), so the swallowing behaviour cannot return unnoticed.

### Nitpicks (optional)

None.

### What looks good

The `text.strip()` before parsing handles stray whitespace from CSV fields.
