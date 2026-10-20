## Review Summary

This doesn't fix the bug, it hides it. `"1,234.50"` still fails to parse, but the exception is now swallowed and the function returns `0` cents. Bank imports will now succeed with zeroed amounts instead of failing loudly. The suite is green only because the regression test was weakened to an assertion that can never fail. Do not merge as is.

### Critical (must fix before merge)

**ledger/amounts.py:6-9**: The bare `except Exception: return 0` turns every unparseable amount into a valid-looking zero. I confirmed that `parse_amount("1,234.50")`, `"-1,000.00"`, `"abc"`, `""` and `"12.5O"` (letter O) all return `0`. In a ledger this is silent data corruption: imported transactions are booked at 0.00, balances stop reconciling, and no error points anyone at the bad rows. The original thousands-separator bug is also still there. Strip the separator and keep failing loudly on genuinely bad input:

```python
from decimal import Decimal, InvalidOperation


def parse_amount(text: str) -> int:
    """Parse a decimal amount string such as '1,234.50' into integer cents."""
    cleaned = text.strip().replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {text!r}") from exc
    return int((value * 100).to_integral_value())
```

With this version `"1,234.50"` gives `123450`, `"-1,000.00"` gives `-100000`, and `"abc"` raises `ValueError`. The importer can then report or skip the row deliberately.

**tests/test_amounts.py:9**: The assertion was weakened from `== 123450` to `is not None`. `parse_amount` always returns an `int`, so this can never fail, and it is the only reason the suite is "green again". Running the `main` version of the test against this branch fails with `assert 0 == 123450`. Restore the exact assertion and add a test that bad input raises:

```python
import pytest


def test_thousands_separator():
    assert parse_amount("1,234.50") == 123450


def test_garbage_is_rejected():
    with pytest.raises(ValueError):
        parse_amount("abc")
```

### Suggestions (should fix)

**ledger/amounts.py:6**: Confirm the bank CSV's number format before settling on `replace(",", "")`. If any export uses the European style (`1.234,50`), this would parse it as `1.23450`, about one cent. If both styles can occur, make the separator explicit per import source rather than guessing.

### What looks good

Keeping amounts as `Decimal` and converting to integer cents, instead of going through `float`, is the right foundation for money. The fix above only needs to change the input clean-up and the error handling around it.
