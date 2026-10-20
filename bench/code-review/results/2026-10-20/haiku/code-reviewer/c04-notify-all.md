## Review Summary

`notifyAll` does not do what its description says. The `async` callback passed to `forEach` is never awaited, so the function returns before any reminder is sent, and the count it reports is wrong. A user without an email also produces an unhandled rejection that will crash the nightly job process. The test passes only because it uses two valid users and checks `sent` against the input length. Request changes.

### Critical (must fix before merge)

**src/jobs.js:4-9 (`notifyAll`)** — `users.forEach(async ...)` does not wait for the promises. Confirmed by running it: `notifyAll([{id:1,email:'a@x'},{id:2}])` resolves with `{ sent: 2, attempted: 0 }` immediately, before any send finishes. `attempted` is always 0 at return time, and `sent` is `users.length` whether or not the sends succeed. The nightly job would log success for reminders that never went out.

**src/jobs.js:6** — `sendReminder` throws for a user with no email (`src/mailer.js:4`). Inside the un-awaited `forEach` callback that rejection is unhandled, and Node terminated the process with `Error: user 2 has no email` in the probe. One bad record now kills the job.

Sequential fix that keeps the count honest and isolates failures:

```javascript
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

### Suggestions (should fix)

**test/jobs.test.js** — Add a case with one user lacking an email. Assert `sent === 1` and that `failed` contains that user. The current test would not catch the bug above.

**src/jobs.js** — If throughput matters, use `Promise.allSettled` over a `users.map(...)` instead of the sequential loop. That keeps the failure handling and removes the serial wait.

### Nitpicks (optional)

None beyond the points above.

### What looks good

Keeping the sender in `mailer.js` and only orchestrating in `jobs.js` keeps the job testable without a real mail server.
