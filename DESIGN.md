# Ledgerflow · PS-1 design and assumptions

The AP operator needs a decision they can explain and act on. The original assignment allows us to define policy; it does not require approving budget overruns. This implementation preserves a hard cumulative authorization cap.

1. Receive a real PDF; record SHA-256 and store its bytes.
2. Read text with PyMuPDF or use local RapidOCR for image-heavy pages. Retain page boundaries and OCR confidence.
3. Extract labelled fields and bounded structured tables. Derive inclusive net/tax only from explicit total + rate evidence. Record method, page, source text and derivation.
4. If configured and the rules left required fields unread, ask Gemini for field candidates from untrusted document text. Optional `GEMINI_CROSSCHECK=1` also double-reads complete invoices. Check source occurrence, value and field role, then compare against populated rule values. Preserve uncertainty and conflicting candidates. No model output controls policy or tools.
5. Validate required fields, date, arithmetic, tax basis/rates, lines and extraction uncertainty. Retain all checks, including the passing ones.
6. Under a SQLite write lock, match an approved vendor and an explicit/unique PO, check duplicate file/identity, currency, line mapping/prices/quantities, and remaining gross authorization.
7. Commit the decision and reserve only an approved invoice's gross amount. For line POs, quantities in committed approved invoices are the allocation ledger; opening quantity must be explicitly supplied. Result, invoice and reservation share the transaction.
8. Record live timeline events. On failure, show a terminal outcome; post-commit timeline failure cannot erase a financial decision. On restart, interrupted processing becomes failed.
9. For review, require a name and reason. Read current saved fields under the lock, apply permitted corrections, retain prior fields/results, and rerun policy. Saving corrections does not reserve money. Approval cannot waive duplicate, arithmetic, mapping or budget checks.

## Operational choices

- Approved means authorization reserved, with no payment sent. Pending invoices reserve nothing; later approvals recheck current capacity.
- Money uses integer minor units; `Decimal` provides half-up calculations. The one-cent arithmetic allowance, configurable matching thresholds and zero authorization allowance are distinct. Full definitions and boundaries are in README.
- Amount-only POs cannot support line-price/delivery verification. Exact unique description matches are required for line POs; no invented SKU, quantity or bundle allocation. Ordered amount is prorated only when ordered and invoiced quantities are explicit. Unknown opening quantity requires review. There is no goods-receipt dataset.
- Semantic quote checks are bounded heuristics. They reduce some errors; neither quote presence nor correct arithmetic proves the selected economic fields are correct. Prior-payment/balance documents are conservatively held.
- The AI reader is a fallback, not a dependency. Free-tier quota is about five requests per minute, so a happy-path invoice must not fail because of a provider outage. Gemini is therefore only asked about fields the rules could not read. If that read fails, the invoice is held for review; a human may explicitly verify the retained evidence. In cross-check mode, a failed read holds labelled invoices too. Without a key, supported deterministic invoices still approve. Unsupported missing data remains missing.
- Reviewer corrections allow date, currency and amounts only. Vendor/identity/explicit PO changes need a new corrected document. Before/after audit records retain original extraction. User-entered reviewer identities are not authenticated.
- A sample invocation creates an isolated duplicate/commitment scope. The duplicate story seeds an original through the same engine in that scope. Uploads use the shared real-upload scope. No shared reset endpoint is exposed; no synthetic sample can consume a real-upload PO balance.

## Four primary exceptions

| Exception | Why automated behavior differs | Resolution |
|---|---|---|
| Re-exported duplicate | Byte hash differs; vendor/invoice identity matches an accepted original. Reject without reserving again. | Open original; resolve invoice identity with AP/vendor. Reviewer cannot waive. |
| Partially consumed PO | Gross amount fits original PO but exceeds remaining authorization by $450. Hold. | Procurement update/corrected invoice required. No budget override. |
| Inclusive service bundles | Gross charges need explicit 18% tax derivation; no printed quantities. | Derive evidenced net/tax, reconcile bundles to gross, approve within amount-only authorization. Explain matching limitation. |
| Two valid POs | Missing reference makes unique allocation impossible. Hold. | Human selects PO with reason; fresh policy/balance check approves or stays blocked. |

## Deployment and validation boundary

FastAPI + plain JavaScript, SQLite/WAL, PyMuPDF, RapidOCR, optional Gemini. The existing VM/systemd/Caddy architecture is retained. Docker includes all imported application modules. No new hosting service or unrelated server configuration was introduced. See VALIDATION.md for measured test and deployment results. Demo data is synthetic; extraction accuracy and time savings have not been measured. The recorded dashboard duration is per-run elapsed time, not a performance benchmark.
