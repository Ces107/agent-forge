# Code review: Add invoice PDF download endpoint

**Verdict: BLOCK** · 2 findings (1 proven, 1 traced) · Scope: main..feature, 3 files, 24 insertions(+) · Tests: `node --test` passed (3 tests, 1 new)

The change adds `GET /invoices/:id/pdf`. The new handler looks the invoice up by id and never checks
who owns it, unlike the sibling route `GET /invoices/:id`, so any authenticated user can download any
invoice. The rendered "PDF" is also a four-line text stub that is not a valid PDF file. Fix the
authorization before anything else.

## Findings

### 1. [CRITICAL · Proven] `src/routes.js:19-25`: any user can download any invoice PDF
**What happens:** user `u_2` requests `GET /invoices/inv_1/pdf` → `downloadInvoicePdf` calls
`findInvoice("inv_1")` and returns the body without comparing `invoice.ownerId` ("u_1") to `user.id`
("u_2") → `u_2` receives `u_1`'s invoice. The `user` argument is never read in the function, which is the
tell that the ownership check was dropped when the handler was copied from `loadOwnedInvoice` (lines 11-17).

**Evidence:**
```
$ node --test test/review-proof-idor.test.js  (proof file, deleted afterwards)
✖ review proof: user cannot download another user's invoice PDF
  AssertionError [ERR_ASSERTION]: Missing expected exception (HttpError).
ℹ pass 0
ℹ fail 1
```
Fixture check: `src/store.js:2` maps `inv_1` to `ownerId: "u_1"`.

**Fix:** reuse the owned-invoice loader, so the new route gets the same 404 as its sibling:
```js
function downloadInvoicePdf(user, params) {
  const invoice = loadOwnedInvoice(user, params.id);
  return { contentType: "application/pdf", body: renderInvoicePdf(invoice) };
}
```

<details><summary>Proof test (add it to the suite)</summary>

```js
const test = require("node:test");
const assert = require("node:assert");
const { routes, HttpError } = require("../src/routes");

test("review proof: user cannot download another user's invoice PDF", () => {
  assert.throws(
    () => routes["GET /invoices/:id/pdf"]({ id: "u_2" }, { id: "inv_1" }),
    HttpError
  );
});
```
</details>

### 2. [HIGH · Traced] `src/pdf.js:2`: the output is not a valid PDF
**What happens:** the customer downloads the file with `Content-Type: application/pdf` → the body is
the plain text `%PDF-1.4\nInvoice inv_1\nTotal 1200\n` → a PDF reader rejects it because there are no
objects, no cross-reference table, no trailer and no `%%EOF`. The feature the PR was asked for ("download
invoices as PDF") does not produce a usable document.

**Evidence:** `src/pdf.js:1-3` builds the whole file as one template string:
```js
return Buffer.from(`%PDF-1.4\nInvoice ${invoice.id}\nTotal ${invoice.total}\n`);
```
Traced, not run: no PDF parser is installed and I was not allowed to install one. The header alone is what
the existing test checks (`test/pdf.test.js:8`), so the test passes on a file no reader can open.

**Fix:** generate a real document with a PDF library (the project has none yet; see question 1). Hand-
writing objects and `xref` offsets is error-prone, so do not do it by hand:
```js
// Replace renderInvoicePdf with a real renderer, e.g. pdfkit or pdf-lib, and keep the same signature.
// Then strengthen test/pdf.test.js to parse the output with that library, not just check the prefix.
```

## Questions for the author
- Is a PDF library already allowed in this project? `package.json` has no dependencies, so adding one is a
  decision for you, not something I should assume.
- `invoice.total` is rendered as a bare number (`Total 1200`). Is it cents, and should the PDF show a
  currency?

## Checked and fine
- The 404 for a missing invoice is correct and matches `loadOwnedInvoice` (`src/routes.js:13-15`).
- The existing tests (`test/routes.test.js`) still pass unchanged; `routes.js` exports are unchanged.

## Nits
- `test/pdf.test.js` is happy-path only and never exercises another user. Add the ownership case from
  finding 1 next to it, so the gap cannot come back unnoticed.
