# Code review: Send renewal reminders to all expiring users

**Verdict: BLOCK** · 3 findings (2 proven, 1 traced in the count) · Scope: main..feature, 2 files, 20 insertions(+) · Tests: `node --test` passed (2 tests, but see finding 3)

`notifyAll()` is meant to send a reminder to each user and return how many were sent. It does not do
that. `users.forEach` does not wait for its async callbacks, so the function returns before any reminder
is sent, and the count it returns is the input length, not the number sent. A user without an email also
raises inside an un-awaited callback, which takes down the nightly process.

## Findings

### 1. [CRITICAL · Proven] `src/jobs.js:5-8`: the nightly job crashes on a user without an email
**What happens:** the job passes a user with no `email` → `sendReminder` throws at `src/mailer.js:4-5` →
the throw rejects the promise returned by the `async` callback at `jobs.js:5`, which nothing awaits or
catches → Node treats it as an unhandled rejection and terminates the process → the nightly job dies, and
the reminders that were still in flight are cut short.

**Evidence:**
```
$ node -e 'const {notifyAll}=require("./src/jobs"); notifyAll([{id:1,email:"a@x"},{id:2}])
           .then(r=>console.log("returned", JSON.stringify(r)));
           setTimeout(()=>console.log("timer after 50ms"),50)'
returned {"sent":2,"attempted":0}
C:\...\src\mailer.js:5
    throw new Error(`user ${user.id} has no email`);
Error: user 2 has no email
    at ...\src\jobs.js:6:11
    at Array.forEach (<anonymous>)
```
The `timer after 50ms` line never printed: the process ended on the rejection.

**Fix:** await the work in order, or in bounded parallel, and collect failures instead of throwing them:
```js
async function notifyAll(users) {
  let sent = 0;
  const failed = [];
  for (const user of users) {
    try {
      await sendReminder(user);
      sent += 1;
    } catch (err) {
      failed.push({ id: user.id, error: err.message });
    }
  }
  return { sent, failed };
}
```

### 2. [HIGH · Proven] `src/jobs.js:9`: the result is returned before any reminder is sent, and reports the wrong count
**What happens:** `notifyAll` returns synchronously after `forEach` has only started the callbacks → the
caller logs `sent: users.length` and `attempted: 0`, while the reminders are still being sent, or never
sent at all after a crash. The count the PR promises ("how many reminders were sent") is not the number
sent: it is the input length.

**Evidence:**
```
$ node --test test/review-proof-notify.test.js  (proof file, deleted afterwards)
✖ review proof: reported count matches reminders actually sent
  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:
    actual: 0,
    expected: 2,
```
Two users were passed; the mailer recorded 0 sends at the point the function returned.

**Fix:** the loop in finding 1 fixes both the early return and the count, because `sent` is incremented
only after `sendReminder` resolves.

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { notifyAll } = require("../src/jobs");
const { sent } = require("../src/mailer");

test("review proof: reported count matches reminders actually sent", async () => {
  const before = sent.length;
  const result = await notifyAll([{ id: 10, email: "x@example.com" }, { id: 11, email: "y@example.com" }]);
  assert.strictEqual(sent.length - before, 2);
  assert.strictEqual(result.attempted, 2);
});
```
</details>

### 3. [MEDIUM · Traced] `test/jobs.test.js:5-8`: the test cannot fail for the bug it claims to test
**What happens:** the test asserts `result.sent === 2`, but `sent` is `users.length`, which is 2 whatever
the function does → the test passes even when zero reminders are sent, which is exactly the state
findings 1 and 2 describe. The test also never reads `sent` from `src/mailer.js`.

**Evidence:** `test/jobs.test.js:7` is `assert.strictEqual(result.sent, 2);`, and `src/jobs.js:9` builds
`sent: users.length`. The suite passes (`node --test`: 2 pass, 0 fail) with the broken implementation.

**Fix:** assert on the side effect, not the return value:
```js
test("sends a reminder to every user", async () => {
  const before = sent.length;
  await notifyAll([{ id: 1, email: "a@example.com" }, { id: 2, email: "b@example.com" }]);
  assert.strictEqual(sent.length - before, 2);
});
```

## Questions for the author
- Should one bad user stop the job, or should it finish the others and report failures? The fix above
  assumes the second, which needs a caller that can read `failed`.
- Is the nightly caller outside this repository? I did not find one here; `notifyAll` is only used by its test.

## Checked and fine
- `sendReminder` rejects a missing email before any side effect (`src/mailer.js:4-6`).
- `src/mailer.js` records each send only after its delay resolves, so the `sent` array is accurate for
  `sendReminder` on its own (`test/mailer.test.js`, passing).

## Nits
- None worth the author's time beyond the three findings above.
