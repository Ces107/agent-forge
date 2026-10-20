# Code review: Add customer search by name

**Verdict: BLOCK** · 3 findings (3 proven) · Scope: main..feature, 2 files, +13 −1 ·
Tests: `python -m pytest` passed (2)

The change adds `search_customers()` for the admin panel's name search. It works for the one
ASCII example in the test, but it builds the SQL by pasting the search text into an f-string. Any
quote in the input breaks the query, and a crafted input can read any table in the database.
The fix is a bound parameter, and that has to land before merging.

## Findings

### 1. [CRITICAL · Proven] `crm/repo.py:21`: the search text is pasted into the SQL (injection, and a crash on names like O'Brien)
**What happens:** the search term goes straight into the SQL string:
`f"... WHERE lower(name) LIKE '%{name.lower()}%' ORDER BY name"`.
- An ordinary search for `o'brien` raises `sqlite3.OperationalError` (the admin panel errors on
  any name with an apostrophe).
- A search for `zzz' UNION SELECT 1, sql, 3 FROM sqlite_master --` returns the database schema.
  The same technique reads any other table. `sqlite3` refuses stacked statements, so this is
  read access rather than `DROP TABLE`, but it is still a data breach.

**Evidence:**
```
$ python -m pytest -p no:cacheprovider tests/test_review_proof_search.py -q --tb=short
E   sqlite3.OperationalError: near "brien": syntax error
E   AssertionError: assert [(1, 'CREATE ...il TEXT)', 3)] == []
E     Left contains one more item: (1, 'CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT)', 3)
FAILED tests/test_review_proof_search.py::test_review_proof_apostrophe_in_name
FAILED tests/test_review_proof_search.py::test_review_proof_search_injection
```
The other two functions in the same file (`get_customer`, `add_customer`) already use `?`
parameters. This is the only query built by string formatting.

**Fix** (this also covers findings 2 and 3; I checked it against all four proof inputs):
```python
def search_customers(conn: sqlite3.Connection, name: str):
    conn.create_function("casefold", 1, lambda s: s.casefold() if s is not None else None, deterministic=True)
    term = name.casefold().replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return conn.execute(
        "SELECT id, name, email FROM customers WHERE casefold(name) LIKE ? ESCAPE '!' ORDER BY name",
        (f"%{term}%",),
    ).fetchall()
```

<details><summary>Proof tests (add them to the suite)</summary>

```python
from crm.repo import add_customer, connect, search_customers


def test_review_proof_apostrophe_in_name(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Conan O'Brien", "conan@example.com")
    assert [row[1] for row in search_customers(conn, "o'brien")] == ["Conan O'Brien"]


def test_review_proof_search_injection(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Ada Lovelace", "ada@example.com")
    add_customer(conn, "Alan Turing", "alan@example.com")
    rows = search_customers(conn, "zzz' UNION SELECT 1, sql, 3 FROM sqlite_master --")
    assert rows == []                     # a search for a name no customer has
```
</details>

### 2. [MEDIUM · Proven] `crm/repo.py:21`: "case-insensitive" fails for accented names
**What happens:** the customer `Álvaro Núñez` searched as `álvaro` gets no results. Python's
`name.lower()` folds the search term, but SQLite's built-in `lower()` and `LIKE` fold only ASCII
letters, so `lower('Álvaro')` stays `Álvaro` and never matches `álvaro`. The PR promises a
case-insensitive match, and that only holds for ASCII names.

**Evidence:**
```
$ python -m pytest -p no:cacheprovider tests/test_review_proof_search.py -q --tb=short
E   AssertionError: assert [] == ['Álvaro Núñez']
FAILED tests/test_review_proof_search.py::test_review_proof_non_ascii_case
```

**Fix:** fold both sides in Python, using the `casefold` SQL function registered in the fix for
finding 1. With that fix, both `álvaro` and `ÁLVARO` return `Álvaro Núñez`.

<details><summary>Proof test</summary>

```python
def test_review_proof_non_ascii_case(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Álvaro Núñez", "alvaro@example.com")
    assert [row[1] for row in search_customers(conn, "álvaro")] == ["Álvaro Núñez"]
```
</details>

### 3. [LOW · Proven] `crm/repo.py:21`: `%` and `_` in the search act as wildcards
**What happens:** searching for `_` returns every customer, because `_` is a `LIKE` wildcard and
is not escaped. The same goes for `%`.

**Evidence:**
```
$ python -m pytest -p no:cacheprovider tests/test_review_proof_search.py -q --tb=short
E   AssertionError: assert [(1, 'Ada Lov...example.com')] == []
E     Left contains 2 more items, first extra item: (1, 'Ada Lovelace', 'ada@example.com')
FAILED tests/test_review_proof_search.py::test_review_proof_search_wildcard
```

**Fix:** escape `%`, `_` and the escape character, then add `ESCAPE '!'`. The fix for finding 1
already does this.

<details><summary>Proof test</summary>

```python
def test_review_proof_search_wildcard(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Ada Lovelace", "ada@example.com")
    add_customer(conn, "Alan Turing", "alan@example.com")
    assert search_customers(conn, "_") == []    # no customer name contains "_"
```
</details>

## Questions for the author
- An empty search (`""`) matches every row, and the query has no `LIMIT`. Is the admin panel
  meant to list the whole customer table in that case, or should it require a minimum length or
  page the results?

## Checked and fine
- Callers: `search_customers` is new, and the only caller in the repository is the new test. No
  existing behaviour changed.
- `get_customer` and `add_customer` are untouched and already use bound parameters.
- The returned row shape `(id, name, email)` matches `get_customer`, and `ORDER BY name` gives a
  stable order for the admin list.
- The existing test `test_roundtrip` is unchanged and passes. The new test asserts an exact
  result, not a weakened one.
