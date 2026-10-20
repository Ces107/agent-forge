# Code review: Add customer search by name

**Verdict: BLOCK** · 2 findings (2 proven) · Scope: main..feature, 2 files, 13 insertions(+), 1 deletion(-) · Tests: `python -m pytest` passed (2 tests; neither covers the failure modes below)

`search_customers()` builds its SQL with an f-string around the user's search text. That is a SQL
injection in an admin search box, and it also makes an ordinary name with an apostrophe (`O'Brien`) fail
with a syntax error. The fix is one parameterized query.

## Findings

### 1. [CRITICAL · Proven] `crm/repo.py:21-22`: the search term is injected into the SQL
**What happens:** the admin types `zzz%' OR 1=1 --` → the f-string produces
`... LIKE '%zzz%' OR 1=1 --%' ORDER BY name` → the `OR 1=1` clause matches every row → the admin panel lists
every customer (names and emails), regardless of the search term. The same hole lets an attacker with
access to the search box read any other table in the SQLite file.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_sqli.py  (proof file, deleted afterwards)
>       assert search_customers(conn, "zzz%' OR 1=1 --") == []
E       AssertionError: assert [(1, 'Ada Lovelace', 'ada@example.com')] == []
E       Left contains 2 more items, first extra item: (1, 'Ada Lovelace', 'ada@example.com')
1 failed in 2.36s
```

**Fix:** bind the pattern as a parameter, and escape the LIKE wildcards in the user text:
```python
def search_customers(conn: sqlite3.Connection, name: str):
    escaped = name.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return conn.execute(
        "SELECT id, name, email FROM customers WHERE lower(name) LIKE ? ESCAPE '\\' ORDER BY name",
        (f"%{escaped}%",),
    ).fetchall()
```

<details><summary>Proof test (add it to the suite)</summary>

```python
from crm.repo import add_customer, connect, search_customers


def test_review_proof_search_term_cannot_widen_query(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Ada Lovelace", "ada@example.com")
    add_customer(conn, "Alan Turing", "alan@example.com")
    assert search_customers(conn, "zzz%' OR 1=1 --") == []
```
</details>

### 2. [HIGH · Proven] `crm/repo.py:21`: a name with an apostrophe crashes the search
**What happens:** an agent searches for `O'Brien` (a real customer name) → the f-string becomes
`LIKE '%o'brien%'` → SQLite sees the quote as the end of the string → `OperationalError` → the admin
panel returns an error for a normal search. The same statement will also break for any name containing `'`.

**Evidence:**
```
$ python -m pytest tests/test_review_proof_sqli.py  (proof file, deleted afterwards)
E       sqlite3.OperationalError: near "brien": syntax error
FAILED tests/test_review_proof_sqli.py::test_review_proof_apostrophe_name_searchable
```

**Fix:** the parameterized query in finding 1 fixes this too; no separate change is needed.

<details><summary>Proof test (add it to the suite)</summary>

```python
from crm.repo import add_customer, connect, search_customers


def test_review_proof_apostrophe_name_searchable(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    add_customer(conn, "Sean O'Brien", "sob@example.com")
    assert len(search_customers(conn, "o'brien")) == 1
```
</details>

## Questions for the author
- Is the admin search the only caller? I could not find any other use of `search_customers` in this
  repository; the admin panel that calls it is outside it.

## Checked and fine
- `get_customer` and `add_customer` use bound parameters (`crm/repo.py:11`, `15`), so the injection is limited
  to the new function.
- The case-insensitive match itself works: `search_customers(conn, "ADA")` returns `Ada Lovelace` only
  (`tests/test_repo.py:14`, passing).

## Nits
- None beyond the two findings above; the LIKE wildcard escaping in the fix is needed for finding 1, not a
  separate nit.
