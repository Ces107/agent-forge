# Code review: Send renewal reminders to all expiring users

**Verdict: REQUEST CHANGES** · 2 findings (2 proven) · Scope: main..feature, 2 files, +20 −0 ·
Tests: `node --test` passed (2)

The change adds `notifyAll(users)` for the nightly job and returns counts for the job log. It
uses `users.forEach(async ...)`, which does not wait for the callbacks, so `notifyAll` resolves
before any reminder is sent, and the counts it returns are made up (`sent` is always the number of
users, `attempted` is always 0). Because nothing awaits the callbacks, one user without an email
also becomes an unhandled rejection that kills the job process. Replace the `forEach` with an
awaited loop that catches each failure.

## Findings

### 1. [HIGH · Proven] `src/jobs.js:5-9`: `notifyAll` returns before any reminder is sent, with wrong counts
**What happens:** the nightly job calls `await notifyAll(users)` → it resolves at once with
`{ sent: users.length, attempted: 0 }`, while every `sendReminder` is still pending. The job logs
"all sent" whatever happens, and if the job exits or closes the mail connection after the call
returns, the reminders are lost. `Array.prototype.forEach` ignores the promises its callback
returns; `count` is read on `:9` before any callback has passed its `await`. The new test only
checks `result.sent`, which is `users.length` by construction, so it cannot fail.

**Evidence:**
```
$ node --test test/review-proof-notify-all.test.js
✖ review proof: notifyAll waits for every reminder before returning (4.9234ms)
  AssertionError [ERR_ASSERTION]: sent so far: []

$ node -e 'require("./src/jobs").notifyAll([{id:1,email:"a@example.com"},{id:2}]).then(r=>console.log("job logged:", JSON.stringify(r)))'
job logged: {"sent":2,"attempted":0}
```

**Fix** (also fixes finding 2; I ran it against both proof tests and they pass):
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
  return { sent, attempted: users.length, failed };
}
```
The loop sends one reminder at a time, which also keeps the mailer from being hit by thousands of
parallel sends. If throughput matters, use `Promise.allSettled` over small batches instead.

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { notifyAll } = require("../src/jobs");
const { sent } = require("../src/mailer");

test("review proof: notifyAll waits for every reminder before returning", async () => {
  const users = [{ id: 10, email: "x@example.com" }, { id: 11, email: "y@example.com" }];
  const result = await notifyAll(users);
  assert.ok(sent.includes("x@example.com") && sent.includes("y@example.com"), `sent so far: ${JSON.stringify(sent)}`);
  assert.strictEqual(result.attempted, 2);
});
```
</details>

### 2. [HIGH · Proven] `src/jobs.js:5-8`: one user without an email crashes the nightly job
**What happens:** `sendReminder` throws for a user with no email (`src/mailer.js:4-6`). Inside
the un-awaited `forEach` callback that becomes an unhandled promise rejection, and Node (v15+,
here v24.11.0) terminates the process on it. One expiring user with a missing email address stops
the whole job and every reminder still in flight, after the job has already logged them as sent.

**Evidence:**
```
$ node -e 'require("./src/jobs").notifyAll([{id:1,email:"a@example.com"},{id:2}]).then(r=>console.log("job logged:", JSON.stringify(r)))'
job logged: {"sent":2,"attempted":0}
.../src/mailer.js:5
    throw new Error(`user ${user.id} has no email`);
Error: user 2 has no email
    at sendReminder (.../src/mailer.js:5:11)
    at .../src/jobs.js:6:11
    at Array.forEach (<anonymous>)
exit code: 1

$ node --test test/review-proof-notify-all.test.js
✖ review proof: a user without email is reported, not sent and not a crash
  Error: user 21 has no email
```

**Fix:** the loop in finding 1, which catches each failure and reports it in `failed` instead of
crashing.

<details><summary>Proof test (add it to the suite)</summary>

```js
test("review proof: a user without email is reported, not sent and not a crash", async () => {
  const rejections = [];
  const onRejection = (err) => rejections.push(err.message);
  process.on("unhandledRejection", onRejection);
  const result = await notifyAll([{ id: 20, email: "z@example.com" }, { id: 21 }]);
  await new Promise((resolve) => setTimeout(resolve, 50));
  process.off("unhandledRejection", onRejection);
  assert.deepStrictEqual(rejections, []);
  assert.strictEqual(result.sent, 1);
});
```
</details>

## Checked and fine
- `sendReminder` (`src/mailer.js`) is unchanged, and its own test still passes.
- `notifyAll` has no other callers in the repository; the nightly job that uses it is outside
  this repo, so adding a `failed` field to the result breaks nothing here.
- Existing suite: 2 passed.
