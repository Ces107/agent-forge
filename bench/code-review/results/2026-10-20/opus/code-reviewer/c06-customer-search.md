## Review Summary

Do not merge yet. `search_customers()` builds its SQL by pasting the search term into an f-string, so anyone who can type in the admin search box can run arbitrary SQL. The same bug crashes the search on everyday names like "O'Brien". The fix is small: use a bound parameter, as the other two functions in this file already do. The tests pass (2 passed), but they only cover the happy path.

### Critical (must fix before merge)

**crm/repo.py:20-22: SQL injection in `search_customers`.** `name` goes straight into the SQL text:

```python
query = f"SELECT id, name, email FROM customers WHERE lower(name) LIKE '%{name.lower()}%' ORDER BY name"
```

Why it matters: I confirmed this against the branch.
- `search_customers(conn, "zzz' OR 1=1 --")` returns every customer.
- `search_customers(conn, "x' UNION SELECT 1, sqlite_version(), 3 --")` returns `[(1, '3.50.4', 3)]`, so an attacker can read any table in the database (password hashes, tokens and so on) through the search results.
- Ordinary input breaks too: `search_customers(conn, "O'Brien")` raises `sqlite3.OperationalError: near "brien": syntax error`. Support staff will hit this the first time they search for an Irish surname.

This also breaks the pattern the file already follows: `get_customer` (line 11) and `add_customer` (line 15) both use `?` placeholders. Bind the pattern as a parameter instead. While you are at it, escape the `LIKE` wildcards so a search for `%` or `_` matches those characters literally. Right now `search_customers(conn, "%")` returns the whole table.

```python
def search_customers(conn: sqlite3.Connection, name: str):
    escaped = name.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return conn.execute(
        "SELECT id, name, email FROM customers WHERE lower(name) LIKE ? ESCAPE '\\' ORDER BY name",
        (f"%{escaped}%",),
    ).fetchall()
```

Please add regression tests for a name with an apostrophe and for an injection payload:

```python
def test_search_handles_quotes_and_injection(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Miles O'Brien", "m@example.com")
    add_customer(conn, "Alan Turing", "alan@example.com")
    assert [r[1] for r in search_customers(conn, "o'brien")] == ["Miles O'Brien"]
    assert search_customers(conn, "zzz' OR 1=1 --") == []
```

### Suggestions (should fix)

**crm/repo.py:21: case-insensitivity only works for ASCII names.** Python's `str.lower()` folds the search term for all of Unicode. SQLite's built-in `lower()` and `LIKE` only fold A-Z. So a customer stored as "Ángel Pérez" is not found by `search_customers(conn, "ÁNGEL")` (I confirmed it returns `[]`). The PR promises a case-insensitive search, and for a CRM with Spanish or other accented names this will look like missing data. One option is to register a Unicode-aware function on the connection in `connect()` and use it on both sides:

```python
conn.create_function("ulower", 1, lambda s: s.lower() if s is not None else None, deterministic=True)
# ... WHERE ulower(name) LIKE ? ESCAPE '\'
```

Another option is to store a pre-normalised `name_search` column.

**crm/repo.py:22: no limit on the result set.** An empty or one-letter search returns the whole `customers` table to the admin panel. Add a `LIMIT` (and an offset, if the panel will page through results) so the query stays cheap as the table grows.

### What looks good

The new test does check the core requirement: it searches for `"ADA"` with different case and asserts that only the matching row comes back, not just that something did. And the function keeps the explicit `SELECT id, name, email` column list used by `get_customer`, so callers get the same row shape from both functions.
