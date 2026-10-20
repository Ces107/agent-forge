## Review Summary

`search_customers` builds its SQL with an f-string from user input, which is a SQL injection. A search for `x' OR 1=1 --` returns every customer, confirmed by running it. The fix is a one-line parameter change. The tests pass only because they use a plain name. Do not merge.

### Critical (must fix before merge)

**crm/repo.py:23-24 (`search_customers`)** — `name.lower()` is interpolated straight into the query string. Confirmed: the payload `x' OR 1=1 --` returns all rows (`['Ada', 'Bob']`) when the table holds two customers and the search should return none. A plain quote in a name (`O'Brien`) makes the query fail with an `OperationalError`, so the injection and the crash come from the same line. The admin panel exposes this to anyone who can search.

```python
def search_customers(conn: sqlite3.Connection, name: str):
    return conn.execute(
        "SELECT id, name, email FROM customers WHERE lower(name) LIKE ? ORDER BY name",
        (f"%{name.lower()}%",),
    ).fetchall()
```

### Suggestions (should fix)

**crm/repo.py** — `%` and `_` in the search term act as LIKE wildcards. A search for `_` matches every single-character name. If that matters for support staff, escape them with an `ESCAPE` clause.

**tests/test_repo.py** — Add a test with a quote in the name (`O'Brien`) and an injection-shaped string, asserting the result is empty or the expected single row. The current test would not have caught the vulnerability.

### Nitpicks (optional)

None.

### What looks good

The case-insensitive match is done on both sides with `lower()`, and the result is ordered by name, which keeps the admin list predictable.
