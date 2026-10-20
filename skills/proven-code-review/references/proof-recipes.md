# Proof recipes: make the bug happen in under five minutes

Write each proof in a new file, run it with the project's test command, keep the failing output,
then delete the file and paste its code into the report. The names below are illustrations;
use the real functions of the code under review.

## Wrong value at a boundary (off-by-one, rounding, counts)
Call the function with the boundary values: empty, one, exactly the limit, limit plus one.
```python
def test_review_proof_batch_count_includes_remainder():
    batches = split_into_batches(list(range(101)), size=50)
    assert len(batches) == 3                     # fails if the last partial batch is dropped
```

## A claimed fix that does not fix
Use the exact input from the PR description or the bug report.
```python
def test_review_proof_reported_input():
    assert normalise_phone("+34 600-123-456") == "+34600123456"   # the input from the ticket
```

## Broken caller contract (a change outside the diff breaks)
Call the **unchanged caller**, not the changed function.
```python
def test_review_proof_caller_still_handles_missing_order():
    response = order_status_view(order_id=999_999)  # caller written for the old contract
    assert response.status_code == 404
```

## Missing authorization (IDOR)
Create two users or pick two fixtures; call the new route as user A for user B's object.
```js
test("review proof: a user cannot export someone else's report", async () => {
  const res = await request(app).get("/reports/rep_of_user_b/export").set(authAs("user_a"));
  assert.strictEqual(res.status, 404);
});
```

## Injection
Pass a payload that changes the query's meaning and assert on the result set or the error.
```python
def test_review_proof_filter_injection(db):
    seed_products(db, ["lamp", "desk"])
    assert find_products(db, category="none' OR '1'='1") == []    # fails if every row returns
```

## Race, check-then-act, double side effect
Do not loop and hope. Make the window wide and release callers together:
- slow the side effect with a monkeypatch (`time.sleep(0.05)` inside a wrapped external call),
- start N threads behind a `threading.Barrier(N)` (or N promises created before any is awaited),
- use a file-backed database and one connection per thread (in-memory databases serialise),
- assert the side effect happened once.
```python
def test_review_proof_coupon_redeemed_once(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(rewards, "issue_credit", lambda user, amount: (time.sleep(0.05), sent.append(user)))
    barrier = threading.Barrier(4)
    def worker():
        conn = open_db(tmp_path / "db.sqlite"); barrier.wait()
        redeem_coupon(conn, "user_1", "WELCOME10")
    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(sent) == 1                        # fails if every thread passed the check
```

## Async work that is not awaited (JS/TS)
Await the function, then check the side effect immediately; feed one failing input and listen
for `unhandledRejection`.
```js
test("review proof: syncAll waits for every upload", async () => {
  await syncAll([fileA, fileB]);
  assert.strictEqual(uploaded.length, 2);      // fails if uploads are still pending
});
```

## Time zones and datetimes
Build inputs the way production builds them (call the real factory or load a real row), never
by hand in the test, then exercise the comparison or the arithmetic.
```python
def test_review_proof_real_session_expiry():
    session = start_session(user_id=1)           # production factory, not a hand-made object
    assert not session_expired(session)
```

## Resource leaks
Count open handles before and after the error path (a wrapped `open`, `psutil`, `lsof`), or
assert the cleanup function was called when the operation raises.

## When you cannot prove it locally
Needs a real external service, production data, a migration on a shared database, or a package
that is not installed: report as **Traced**, quote the decisive lines, and write the test the
author should run.
