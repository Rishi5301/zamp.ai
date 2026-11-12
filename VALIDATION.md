# Validation record · 2026-11-06

Results measured on 2026-11-06 for this revision (policy AP-1.1).

## Automated tests

- Local development host (socket-enabled), `.venv/bin/python -m pytest -q`: **102 passed**, with no skips. This includes both HTTP transport integration tests.
- Deployment VM (Python 3.12, headless OpenCV), same suite against a throwaway data directory: **102 passed**.
- `node --check static/app.js`: **passed**.

## Browser acceptance

`.venv/bin/python dev.py --check` runs headless Chrome against an isolated server. **13 of 13 checks passed:**

- Empty dashboard and local assets
- Sample intake, real approval, source evidence and timeline
- Duplicate identity and original-run link
- Human review, current-balance check and audit
- Cumulative PO overrun
- Real neural OCR and missing-date hold
- Real neural OCR approval
- Repeatable samples, inclusive tax, bundles and itemised arithmetic
- Genuine file uploads, duplicate detection and authorization isolation
- Search, history, CSV and persisted PO balances
- Mobile layout, mobile navigation and zero JavaScript errors
- Immutable review evidence, SSE replay, invalid upload and missing-run errors
- Access gate, login cookies, restart persistence, origin checks and HTTPS proxy headers

## Deployment

- **Procedure:** the existing single AWS VM was used (systemd + Caddy).
  - The SQLite database, uploads, source and environment were backed up first.
  - The code was synced and the service restarted.
  - The additive migration added the `demo_pos` table without altering existing rows.
  - `/api/health` reports `{"status":"ok","policy_version":"AP-1.1"}`.
- **Public HTTPS end-to-end check** at https://ledgerflow.expediva.in (Let's Encrypt certificate; HTTP redirects to HTTPS):
  - Without the access code, the API returns 401.
  - Sign-in sets a `Secure` cookie.
  - All 9 samples produced their expected decision:

    | Sample | Expected | Result |
    |---|---|---|
    | Happy path | approved | approved |
    | Re-exported duplicate | rejected | rejected |
    | Split-billing overrun | review | review |
    | Inclusive bundle | approved | approved |
    | Ambiguous PO | review | review |
    | Incomplete scan | review | review |
    | Complete scan | approved | approved |
    | Unfamiliar layout, read by Gemini | approved | approved (8/8 AI fields quote-verified) |
    | Itemised with separate tax | approved | approved |

  - The live event stream returned events.
  - The CSV export returned 200.
  - A cross-origin POST returned 403.
- **Workspace:** test history was cleared after the checks, so evaluators start with an empty dashboard.

## Real Gemini provider

- **Keys:** the live service uses a free-tier key with the model fallback list `gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-3.5-flash`.
- **What was observed:** on the unfamiliar layout, each of the two lite models was run twice. All 4 runs returned 8 of 8 fields that passed quote, value and field-role grounding, in about 1.5 s. The live sample was approved through the normal policy.
- **What shaped the design:** the free tier allows about 5 requests per minute and 20 per day per model, and `gemini-3.8-flash` returned 503 "high demand".
  - Gemini is called only when the label rules leave required fields unread.
  - The next model is tried on a 429, a 503 or a timeout.
  - The prompt requires the quote to include the printed label, because bare-number quotes were correctly discarded by the field-role check.
- **With the key active, all 9 samples produced their expected decisions over public HTTPS.** The 7 fully rule-read samples made no Gemini calls. The missing-date scan asked Gemini, which found no date, so it correctly stayed in review instead of guessing.

## Not verified

- **Gemini extraction accuracy:** not measured beyond the synthetic samples. Provider-failure tests simulate network responses.
- **Docker image:** not built for this deployment.
- **Old screenshots:** the files in `artifacts/` come from an earlier revision.

## Coverage

The tests cover:

- **Reading and extraction:** actual PDF/OCR; invalid model dates.
- **Tax:** tax-equivalent examples; inclusive derivation provenance; malformed or ambiguous tax.
- **Line items:** line arithmetic, tax and sums; PO line mapping and quantities.
- **Matching and spending limits:**
  - Percentage and absolute matching boundaries.
  - Hard authorization limit with a matching allowance configured.
  - Parallel spending and cumulative quantity limits.
- **AI candidates:**
  - Contradictions, including against fields the rules already filled.
  - Quotes taken from the wrong part of the document.
  - Provider failures, timeouts and malformed payloads.
- **Duplicates:** exact, re-exported and parallel duplicate protection.
- **Review:** reviewer corrections that keep the prior fields.
- **Samples:** repeated samples in separate scopes.
- **Durability:** restart persistence; event failures after the decision is committed.
