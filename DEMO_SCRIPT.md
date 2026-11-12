# Interview / narrated recording · target 4:40

Use this repository's script, not the older copy in `../prep`. Start the local app or the verified updated live instance. Do not reset shared data. Each sample is independently repeatable. Rehearse once and keep narration under five minutes. Confirm whether Gemini is genuinely configured before recording; use the honest alternate wording below if it is not.

## 0:00–0:20 · The AP problem

Show Overview.

“AP needs to know which invoice can move forward, which purchase order it consumes, and why. Ledgerflow reads an actual PDF, checks procurement authorization, and keeps the decision and evidence. The example vendors and procurement records are synthetic.”

## 0:20–1:00 · Successful invoice

Try a sample → **A clean invoice** → Run invoice. Show timeline and PO snapshot, then Extracted fields.

“This $4,950 invoice matches an approved vendor and an explicit PO. The PO authorizes $12,000; $4,000 was already committed. The approval reserves $4,950 of the $8,000 remaining, leaving $3,050. No payment was sent. Here are the source page, field evidence, original PDF, and actual recorded stages.”

## 1:00–1:30 · Duplicate with different bytes

Run **Same invoice. Different PDF.** Show Duplicate protection and Open original invoice.

“This scenario processes an original and a re-export. The bytes differ, but vendor and invoice identity match. The second submission is rejected, with no second reservation. Each sample has independent balances, so an evaluator can repeat it safely. Actual repeated uploads still get blocked.”

## 1:30–2:00 · Remaining authorization

Run **The balance tells a different story**. Show the $450 shortfall.

“The $2,450 invoice fits the original $10,000 order, but $8,000 is already committed. Only $2,000 remains. It is held for procurement authorization or vendor correction. A reviewer cannot waive that cap. Matching tolerance is separate: it never authorizes overspending.”

## 2:00–2:40 · Inclusive bundles

Run **Inclusive tax, reconciled charges** → Extracted fields, then Decision checks.

“These bundles total $1,180, explicitly inclusive of 18% tax. Net is $1,180 divided by 1.18: $1,000. Tax is $180. The derived fields show the calculation and evidence. The charges reconcile without inventing quantities. This PO is amount-only, so the interface explains that line prices and delivery are not verified. The supporting itemised sample demonstrates the same net plus separately stated tax.”

## 2:40–3:30 · Exception resolution with audit

Run **Two possible purchase orders**. Select `PO-2026-1048`, enter your name and the note:

`Verified the PDF and confirmed the cloud infrastructure purchase order.`

Click **Verify & approve**, then Audit trail.

“No reference identifies which of two valid orders to charge. A person chooses, records why, and the process checks every rule and the latest authorization again. This scenario now reserves $990, leaving $7,010. Both the original automated review decision and the human action remain available. Date, currency and amount corrections also need a reason and rerun these checks.”

## 3:30–4:15 · AI reads a layout the rules can't

Run **A layout the labels miss**. Watch the timeline say the label rules left 7 fields unread and Gemini is reading the layout. Then open Extracted fields.

“This vendor uses none of the standard labels: ‘Issued by’, ‘Document ref’, ‘Payable’. The label rules read nothing. So Gemini reads it, but it has to quote the exact line each value came from. The code checks that every quote really appears in the PDF and contains that value, otherwise the value is thrown away. These AI tags show what it contributed. Then the same deterministic rules decide: arithmetic, approved vendor, duplicate, currency, remaining PO balance. The model never decides the money. And Gemini is only called when rules fail, so normal invoices never wait on it or break if it is down.”

Verified live on 2026-11-06: `gemini-3.5-flash-lite` read all 8 fields with quotes in about 1.5 s, and the invoice was approved. The free tier is small: about 5 calls per minute and 20 per day per model. The app falls back across three models, and only this sample and the missing-date scan use Gemini. Still, don’t rerun this sample over and over on the day you record.

Fallback if Gemini is unavailable while recording: run **A scan with a missing date** and show OCR evidence.

“Local neural OCR reads this image-only PDF. The missing invoice date still forces review instead of a guess.”

## 4:15–4:40 · Operational value and boundary

Open All invoices, then Purchase orders.

“The operating view shows status, amount, exception reason and the decision trail. Synthetic sample balances stay separate from uploaded invoices. The value is a repeatable decision with a clear handoff, rather than an unexplained approval. This is a bounded take-home: synthetic procurement, limited English table layouts and self-reported reviewer names. Next I would measure field accuracy on a labelled set and import authoritative procurement data.”

Stop before 5:00. Do not state accuracy, savings, production readiness or deployment verification that has not been measured. Record and test the video link separately before submission.
