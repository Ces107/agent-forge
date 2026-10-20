## Review Summary

This needs a fix before merge. The new PDF route skips the ownership check that the existing invoice route applies, so any logged-in user can download any customer's invoice by guessing or iterating ids. The fix is a one-line reuse of the helper that is already in the file.

### Critical (must fix before merge)

**src/routes.js:19-25**: Broken access control (IDOR). `downloadInvoicePdf` looks the invoice up by id and only checks that it exists. It never compares `invoice.ownerId` with `user.id`, although `loadOwnedInvoice` (lines 11-17) does exactly that for `GET /invoices/:id`. I confirmed it: calling the route as `u_1` for `inv_2`, which belongs to `u_2`, returns a PDF containing `Invoice inv_2 / Total 450`. Invoices carry customer billing data, so this exposes every customer's invoices to every other customer. Reuse the existing helper. It also keeps the 404-for-not-yours behaviour consistent, so ids aren't enumerable through differing errors:

```javascript
function downloadInvoicePdf(user, params) {
  const invoice = loadOwnedInvoice(user, params.id);
  return { contentType: "application/pdf", body: renderInvoicePdf(invoice) };
}
```

### Suggestions (should fix)

**test/pdf.test.js:5-9**: Only the happy path is tested. Add the same negative case that `routes.test.js` has for the JSON route, so this regression can't come back:

```javascript
test("other users get 404 on pdf", () => {
  assert.throws(() => routes["GET /invoices/:id/pdf"]({ id: "u_1" }, { id: "inv_2" }), HttpError);
});
```

**src/pdf.js:1-3**: The output is not a valid PDF. It has the `%PDF-1.4` header followed by plain text, but no objects, no xref table, no trailer and no `%%EOF`. Acrobat and browser viewers will report the file as damaged, so the feature customers asked for won't work even after the access fix. If this is a deliberate placeholder, say so in the PR and keep the route unreleased until a real renderer (for example `pdfkit`) is in place. The test's `startsWith("%PDF")` check can't tell the difference, so it gives false confidence.

### Nitpicks (optional)

**src/pdf.js:2**: `Total ${invoice.total}` prints the raw stored number (`1200`). If totals are stored in cents, as the store data suggests, the PDF will show 100x the amount. Format it with the currency the rest of the app uses.

### What looks good

The route is registered next to the existing invoice routes and reuses `HttpError`, so the 404 path is consistent with the JSON endpoint. The renderer lives in its own `pdf.js` module, which keeps it easy to swap for a real implementation.
