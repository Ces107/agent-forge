# Code review: Add invoice PDF download endpoint

**Verdict: BLOCK** · 1 finding (1 proven) · Scope: main..feature, 3 files, +24 −0 ·
Tests: `node --test` passed (3)

The change adds `GET /invoices/:id/pdf`, which loads an invoice and returns it rendered as a PDF.
The sibling route `GET /invoices/:id` only returns an invoice to its owner, but the new handler
looks the invoice up by id alone, so any signed-in user can download any customer's invoice by
changing the id. Reuse the existing ownership check before merging.

## Findings

### 1. [CRITICAL · Proven] `src/routes.js:19-24`: any user can download any other customer's invoice
**What happens:** user `u_1` requests `GET /invoices/inv_2/pdf`, where `inv_2` belongs to `u_2` →
the handler returns `u_2`'s invoice (id and total) as a PDF. The same request on the JSON route
`GET /invoices/inv_2` returns 404. Invoice ids are guessable or enumerable, so this exposes every
customer's billing data.

**Evidence:**
```
$ node --test test/review-proof-pdf-idor.test.js
✖ review proof: a user cannot download someone else's invoice PDF (4.7749ms)
  AssertionError [ERR_ASSERTION]: Missing expected exception (HttpError).

$ node -e 'const {routes}=require("./src/routes"); console.log(routes["GET /invoices/:id/pdf"]({id:"u_1"},{id:"inv_2"}).body.toString())'
%PDF-1.4
Invoice inv_2
Total 450
```
`downloadInvoicePdf` calls `findInvoice(params.id)` and only checks `!invoice` (`:20-21`); the
sibling `loadOwnedInvoice` (`:11-16`) also checks `invoice.ownerId !== user.id`. The new test
(`test/pdf.test.js`) only requests the caller's own invoice, so it cannot catch this.

**Fix:**
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

test("review proof: a user cannot download someone else's invoice PDF", () => {
  // u_1 asks for inv_2, which belongs to u_2; the JSON route answers 404 for the same request
  assert.throws(() => routes["GET /invoices/:id/pdf"]({ id: "u_1" }, { id: "inv_2" }), HttpError);
});
```
</details>

## Questions for the author
- Is `renderInvoicePdf` a placeholder? Its output is a `%PDF-1.4` header followed by plain text
  lines, with no objects, cross-reference table, trailer or `%%EOF`, so PDF viewers are unlikely
  to open it. The test only checks that the body starts with `%PDF`. If it is meant to ship, it
  needs a real PDF writer and a test that parses the output.

## Checked and fine
- Unknown id: returns `HttpError(404)`, the same status and message as the sibling route, so it
  does not reveal whether an id exists (once the ownership check is added, both cases look the same).
- `GET /invoices` and `GET /invoices/:id` are unchanged; `test/routes.test.js` still passes.
- The new module `src/pdf.js` has no dependencies and is only imported by `src/routes.js`.
- Existing suite: 3 passed.
