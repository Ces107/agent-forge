## Review Summary

The route is wired in and the happy-path test passes, but the new endpoint has two blocking problems. It skips the ownership check that the JSON route uses, so any logged-in user can download any invoice by id. And the body is plain text behind a `%PDF` header, which is not a valid PDF, so the feature as written does not give customers a usable file. Request changes.

### Critical (must fix before merge)

**src/routes.js:19-25 (`downloadInvoicePdf`)** — The handler calls `findInvoice(params.id)` directly and never checks `invoice.ownerId`. `loadOwnedInvoice` (line 11) exists for exactly this check and is not used here. Confirmed by running the handler: user `u_1` requests `inv_2`, which is owned by `u_2`, and receives an `application/pdf` response. This is an insecure direct object reference.

```javascript
function downloadInvoicePdf(user, params) {
  const invoice = loadOwnedInvoice(user, params.id);
  return { contentType: "application/pdf", body: renderInvoicePdf(invoice) };
}
```

**src/pdf.js:2** — `renderInvoicePdf` returns `%PDF-1.4` followed by free text. A PDF needs object definitions, a cross-reference table and a trailer. Viewers will reject the file, so the "download as PDF" request is not met. Use a PDF library (for example pdfkit or pdf-lib, if the project already allows one), or render the file properly. Also, `invoice.total` is rendered as raw cents with no currency format.

### Suggestions (should fix)

**test/pdf.test.js** — The test only checks the owner's happy path and a `%PDF` prefix. Add the same "other users get 404" case that `test/routes.test.js` already has for the JSON route. That test would have caught the ownership bug.

**src/routes.js:30** — The route key `"GET /invoices/:id/pdf"` is fine, but the PR says the endpoint sits "next to the existing invoice routes" and the existing routes use the shared `loadOwnedInvoice` path. Keeping one ownership check path for all invoice routes avoids this class of mistake.

### Nitpicks (optional)

**src/pdf.js** — `renderInvoicePdf` takes the full invoice object but only reads `id` and `total`. Passing those two fields makes the dependency explicit.

### What looks good

The `HttpError` 404 for a missing invoice matches the existing JSON route, so clients see the same error shape on both endpoints.
