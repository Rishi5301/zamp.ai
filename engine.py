"""Document perception and deterministic, transactional invoice decisions."""
import hashlib
import io
import json
import re
import threading
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf

import ai_extract
import db
import finance

POLICY = 'AP-1.1'
AI_CURRENCIES = {'USD', 'INR', 'EUR', 'GBP'}
OCR_LOCK = threading.Lock()
_ocr = None
REQUIRED = ['vendor', 'invoice_number', 'invoice_date', 'currency', 'subtotal', 'tax', 'total']


def normalize(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def money(value):
    cleaned = re.sub(r'[$€£₹,\s]', '', str(value)).strip()
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', cleaned):
        raise ValueError('Invalid amount format')
    return int(Decimal(cleaned) * 100)


def amount_text(value, currency='USD'):
    return f'{currency} {value / 100:,.2f}'


def read_document(path, notify=lambda *a: None):
    global _ocr
    pages, methods, confidence = [], [], []
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError('This PDF is password protected. Upload an unlocked copy.')
        if not 1 <= len(doc) <= 12:
            raise ValueError('Upload a PDF with between 1 and 12 pages.')
        for index, page in enumerate(doc):
            text = page.get_text(sort=True).strip()
            # A partial text layer must not hide an image of the actual invoice.
            coverage = sum(abs(r.get('bbox', (0, 0, 0, 0))[2] - r.get('bbox', (0, 0, 0, 0))[0]) *
                           abs(r.get('bbox', (0, 0, 0, 0))[3] - r.get('bbox', (0, 0, 0, 0))[1])
                           for r in page.get_image_info()) / max(page.rect.get_area(), 1)
            if len(text) >= 100 and coverage < 0.65:
                pages.append(text)
                methods.append('PDF text')
                continue
            notify('read', 'running', f'Page {index + 1}: image content detected. Running neural OCR locally.')
            from rapidocr_onnxruntime import RapidOCR
            with OCR_LOCK:
                if _ocr is None:
                    _ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)
                if page.rect.width * page.rect.height * (160/72)**2 > 16000000:
                    raise ValueError('A page is too large to process safely. Export it at normal A4/Letter size.')
                pix = page.get_pixmap(dpi=160, alpha=False)
                if pix.width * pix.height > 16000000:
                    raise ValueError('A page is too large to process safely. Export it at normal A4/Letter size.')
                results, _ = _ocr(pix.tobytes('png'))
            lines = []
            for box, content, score in results or []:
                confidence.append(float(score))
                cy = sum(p[1] for p in box) / 4
                x = min(p[0] for p in box)
                target = next((line for line in lines if abs(line['y'] - cy) < 12), None)
                if target is None:
                    target = {'y': cy, 'parts': []}
                    lines.append(target)
                target['parts'].append((x, content))
            ocr_text = '\n'.join('  '.join(t for _, t in sorted(line['parts']))
                                 for line in sorted(lines, key=lambda line: line['y']))
            pages.append(ocr_text or text)
            methods.append('Neural OCR')
    return '\n\f\n'.join(pages), {
        'pages': len(pages), 'methods': methods,
        'ocr_min_confidence': round(min(confidence), 3) if confidence else None,
        'ocr_mean_confidence': round(sum(confidence) / len(confidence), 3) if confidence else None,
    }


def extract(text, source):
    fields, evidence, conflicts, candidates = {}, {}, [], {}
    patterns = {
        'vendor': r'^\s*(?:vendor|supplier|from)\s*:\s*([^\n]+)',
        'invoice_number': r'^\s*(?:invoice\s*(?:number|no\.?|#|id)|inv\s*(?:no\.?|#))\s*:?\s*([A-Z0-9][A-Z0-9/_-]*)',
        'invoice_date': r'^\s*(?:invoice\s*date|issue\s*date|date)\s*:\s*([^\n]+)',
        'po_number': r'^\s*(?:purchase\s*order(?:\s*(?:number|no\.?|#))?|po(?:\s*(?:number|no\.?|#|reference))?)\s*:\s*([A-Z0-9][A-Z0-9/_-]*)',
        'currency': r'^\s*currency\s*:\s*(USD|INR|EUR|GBP)\b',
        'subtotal': r'^\s*(?:subtotal|sub\s*total|net\s*(?:amount|total))\s*:?\s*(?:USD|INR|EUR|GBP)?\s*([$€£₹]?\s*[0-9][0-9,]*(?:\.\d{1,2})?)\s*$',
        'tax': r'^\s*(?:tax(?:\s*amount)?|vat(?:\s*amount)?|gst(?:\s*amount)?)\s*:?\s*(?:USD|INR|EUR|GBP)?\s*([$€£₹]?\s*[0-9][0-9,]*(?:\.\d{1,2})?)\s*$',
        'total': r'^\s*(?:grand\s*total|invoice\s*total|total\s*(?:amount|due)|amount\s*due|total)\s*:?\s*(?:USD|INR|EUR|GBP)?\s*([$€£₹]?\s*[0-9][0-9,]*(?:\.\d{1,2})?)\s*$',
    }
    for key, pattern in patterns.items():
        matches = list(re.finditer(pattern, text, re.I | re.M))
        if not matches:
            continue
        values = [m.group(1).strip() for m in matches]
        if len({normalize(v) for v in values}) > 1:
            conflicts.append(key)
            candidates[key] = [{'value': v, 'quote': m.group(0).strip(), 'method': 'label rules'} for v, m in zip(values, matches)]
            continue
        fields[key] = values[0]
        evidence[key] = matches[0].group(0).strip()
        if key in ('subtotal', 'tax', 'total'):
            try:
                fields[key] = money(values[0])
            except (ValueError, InvalidOperation):
                fields.pop(key)
    # Header fallback only matches exact known vendor names, never fuzzy guesses.
    if not fields.get('vendor'):
        with db.connection() as con:
            vendors = con.execute('SELECT * FROM vendors').fetchall()
        found = []
        for vendor in vendors:
            names = [vendor['name']] + json.loads(vendor['aliases'])
            for line in text.splitlines():
                if any(normalize(line) == normalize(name) for name in names):
                    found.append((vendor['name'], line.strip()))
        if len({v[0] for v in found}) == 1:
            fields['vendor'], evidence['vendor'] = found[0]
    fields['evidence'] = evidence
    fields['conflicts'] = conflicts
    if candidates:
        fields['conflict_candidates'] = candidates
    fields['source'] = source
    fields['raw_text'] = text[:60000]
    return finance.enrich(fields)


CURRENCY_WORDS = {'USD': ('usd', '$', 'us dollar'), 'INR': ('inr', '₹', 'rupee'),
                  'EUR': ('eur', '€', 'euro'), 'GBP': ('gbp', '£', 'pound sterling', 'sterling')}
MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']


def _squash(value):
    return re.sub(r'\s+', ' ', str(value)).strip().lower()


def ungrounded(key, value, quote, text):
    """Return None when the model's quote is verbatim in the document and actually
    contains the proposed value; otherwise the reason the value is discarded."""
    q = _squash(quote)
    if not q or q not in _squash(text):
        return 'its quote is not in the document'
    if key in ('subtotal', 'tax', 'total'):
        amounts = set()
        for token in re.findall(r'\d[\d,]*(?:\.\d{1,2})?', quote):
            try:
                amounts.add(money(token))
            except (ValueError, InvalidOperation):
                pass
        if money(value) not in amounts:
            return 'the amount is not in its quote'
        roles = {
            'subtotal': r'\b(?:subtotal|sub\s+total|net(?:\s+amount|\s+total)?)\b',
            'tax': r'\b(?:tax|vat|gst)\b',
            'total': r'\b(?:grand\s+total|invoice\s+total|total|payable|amount\s+due)\b',
        }
        if re.search(r'\b(?:previous balance|amount paid|payments received|credit applied|balance brought forward)\b', q):
            return 'the quote describes a payment or previous balance, not this invoice field'
        if not re.search(roles[key], q):
            return 'the quote does not identify the requested financial field'
        # Multiple amounts/roles in one quote are not field-specific support.
        if len(amounts) > 1 or (key == 'total' and re.search(roles['subtotal'], q)):
            return 'the quote contains ambiguous amounts or financial roles'
        return None
    if key == 'invoice_date':
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            return 'the proposed date is not a valid calendar date'
        year, month, day = value.split('-')
        numbers = {int(n) for n in re.findall(r'\d+', quote)}
        month_ok = int(month) in numbers or MONTHS[int(month) - 1] in q
        return None if year in quote and int(day) in numbers and month_ok else 'the date is not in its quote'
    if key == 'currency':
        return None if any(word in q for word in CURRENCY_WORDS[value]) else 'the currency is not in its quote'
    roles = {'vendor': r'\b(?:vendor|supplier|from|issued by)\b',
             'invoice_number': r'\b(?:invoice|inv|document ref)\b',
             'po_number': r'\b(?:purchase order|po|against order)\b'}
    if key in roles and (not re.search(roles[key], q) or re.search(r'\b(?:bill(?:ed)? to|recipient)\b', q)):
        return 'the quote does not identify the requested field role'
    return None if normalize(value) and normalize(value) in normalize(quote) else 'the value is not in its quote'


def merge_ai_fields(invoice, proposed, text):
    """Validate field-specific evidence; compare ALL candidates, retaining conflicts.

    Quotes and arithmetic reduce errors but cannot guarantee extraction correctness.
    """
    if not proposed:
        return []
    filled, rejected = [], []
    conflicts = set(invoice.get('conflicts', []))
    evidence = invoice.setdefault('evidence', {})
    for key in ai_extract.FIELDS:
        item = proposed.get(key)
        if not isinstance(item, dict) or key in conflicts:
            continue
        value, quote = item.get('value'), item.get('quote')
        if not isinstance(value, str) or not isinstance(quote, str) or not value or not quote:
            rejected.append({'field': key, 'reason': 'invalid value/quote response'})
            continue
        if key in ('subtotal', 'tax', 'total'):
            try:
                parsed = money(value)
            except (ValueError, InvalidOperation):
                rejected.append({'field': key, 'reason': 'it is not a valid amount'})
                continue
        elif key == 'currency':
            value = parsed = value.upper()
            if value not in AI_CURRENCIES:
                rejected.append({'field': key, 'reason': 'the currency is not supported'})
                continue
        elif key == 'invoice_date':
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                rejected.append({'field': key, 'reason': 'the date is not ISO YYYY-MM-DD'})
                continue
            parsed = value
        else:
            parsed = value
        reason = ungrounded(key, value, quote, text)
        if reason:
            rejected.append({'field': key, 'reason': reason})
            continue
        if invoice.get(key) is not None:
            existing = invoice[key]
            same = (existing == parsed if key in ('subtotal', 'tax', 'total') else normalize(existing) == normalize(parsed))
            if not same:
                invoice.setdefault('conflicts', []).append(key)
                invoice.setdefault('conflict_candidates', {})[key] = [
                    {'value': existing, 'quote': evidence.get(key), 'method': 'label rules'},
                    {'value': parsed, 'quote': quote, 'method': 'AI reader'}]
            continue
        invoice[key] = parsed
        evidence[key] = f'AI reader · “{" ".join(quote.split())[:160]}”'
        finance.evidence(invoice, key, 'AI reader', quote)
        filled.append(key)
    if filled:
        invoice['ai_fields'] = sorted(set(invoice.get('ai_fields', []) + filled))
    if rejected:
        invoice['ai_rejected'] = rejected
    if filled or rejected:
        invoice.setdefault('source', {})['ai_model'] = proposed.get('_model') or ai_extract.MODEL
    return filled


def validate_fields(invoice, allow_ocr_confirmation=False):
    checks = []
    def check(key, label, status, detail):
        checks.append({'key': key, 'label': label, 'status': status, 'detail': detail})
    missing = [k.replace('_', ' ') for k in REQUIRED if invoice.get(k) is None]
    conflict = invoice.get('conflicts', [])
    if missing or conflict:
        detail = ('Missing: ' + ', '.join(missing) + '. ') if missing else ''
        detail += ('Conflicting values: ' + ', '.join(conflict) + '.') if conflict else ''
        check('complete', 'Required information', 'review', detail.strip())
    else:
        check('complete', 'Required information', 'pass', 'All seven required fields have source evidence.')
    if invoice.get('ai_failure'):
        check('ai-provider', 'AI reader availability', 'review', invoice['ai_failure'])
    if invoice.get('ai_rejected'):
        check('ai-evidence', 'AI field evidence', 'review', 'Unverifiable or semantically unsupported AI candidates need document review: ' + ', '.join(r['field'] for r in invoice['ai_rejected']))
    if invoice.get('invoice_date'):
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', invoice['invoice_date']):
                raise ValueError('unsupported date format')
            parsed_date = date.fromisoformat(invoice['invoice_date'])
            if parsed_date > date.today():
                raise ValueError('future date')
            check('date', 'Invoice date', 'pass', f"Valid invoice date: {invoice['invoice_date']}.")
        except ValueError:
            check('date', 'Invoice date', 'review', 'Date is invalid, future-dated, or not ISO YYYY-MM-DD. Request a corrected date.')
    ocr_conf = invoice.get('source', {}).get('ocr_min_confidence')
    if ocr_conf is not None and ocr_conf < .90 and not allow_ocr_confirmation:
        check('ocr', 'OCR certainty', 'review', f'Lowest OCR token confidence is {ocr_conf:.0%}. A person must verify the original PDF.')
    else:
        check('ocr', 'Extraction evidence', 'pass', 'Read from document text.' if ocr_conf is None else 'OCR evidence passed the confidence gate or was explicitly verified by a reviewer.')
    amounts = [invoice.get(k) for k in ('subtotal', 'tax', 'total')]
    if all(v is not None for v in amounts):
        subtotal, tax, total = amounts
        if total <= 0 or subtotal < 0 or tax < 0 or abs(subtotal + tax - total) > 1:
            check('math', 'Amounts reconcile', 'review', f'Subtotal + tax does not reconcile to a positive invoice total: {subtotal/100:.2f} + {tax/100:.2f} vs {total/100:.2f}.')
        else:
            check('math', 'Amounts reconcile', 'pass', f'{subtotal/100:,.2f} + {tax/100:,.2f} = {total/100:,.2f}; tax-inclusive PO basis.')
    else:
        check('math', 'Amounts reconcile', 'review', 'Subtotal, tax, and total need explicit evidence or an evidenced inclusive-tax derivation; missing values are not guessed.')
    checks.extend(finance.checks(invoice))
    return checks


def evaluate(con, run, invoice, selected_po=None, allow_ocr_confirmation=False):
    checks = validate_fields(invoice, allow_ocr_confirmation)
    issues = [c['detail'] for c in checks if c['status'] == 'review']
    blocks = [c['detail'] for c in checks if c['status'] == 'fail']
    vendor, po, candidates = None, None, []

    def check(key, label, status, detail):
        checks.append({'key': key, 'label': label, 'status': status, 'detail': detail})
        if status == 'review':
            issues.append(detail)
        elif status == 'fail':
            blocks.append(detail)

    for row in con.execute('SELECT * FROM vendors'):
        if normalize(invoice.get('vendor', '')) in [normalize(row['name'])] + [normalize(n) for n in json.loads(row['aliases'])]:
            vendor = dict(row)
            break
    if not vendor:
        check('vendor', 'Approved vendor', 'review', 'Vendor could not be matched exactly to the approved vendor register.')
    elif not vendor['approved']:
        check('vendor', 'Approved vendor', 'fail', f"{vendor['name']} is not approved for procurement.")
    else:
        check('vendor', 'Approved vendor', 'pass', f"{vendor['name']} · {vendor['id']} is approved.")
    identity = normalize(invoice.get('invoice_number', ''))
    scope = run['demo_scope']
    duplicate = con.execute("SELECT id,filename FROM runs WHERE id<>? AND demo_scope=? AND status IN ('approved','review') AND (sha256=? OR (vendor_id=? AND invoice_key=? AND invoice_key<>'')) ORDER BY created_at LIMIT 1",
                            (run['id'], scope, run['sha256'], vendor['id'] if vendor else None, identity)).fetchone()
    if duplicate:
        check('duplicate', 'Duplicate protection', 'fail', f"This file or vendor/invoice identity already exists in run {duplicate['id'][:8]}. No funds reserved.")
    else:
        check('duplicate', 'Duplicate protection', 'pass', 'No matching file hash or vendor + normalized invoice number in prior accepted submissions.')
    po_ref = invoice.get('po_number')
    if selected_po and po_ref and normalize(selected_po) != normalize(po_ref):
        check('po', 'Purchase order', 'fail', 'A reviewer cannot replace an explicit PO reference with a different purchase order.')
    lookup = po_ref or selected_po
    available_pos = db.scoped_pos(con, scope)
    if lookup:
        rows = available_pos
        match = next((r for r in rows if normalize(r['id']) == normalize(lookup)), None)
        if match:
            po = dict(match)
        else:
            check('po', 'Purchase order', 'review', f'Purchase order {lookup} does not exist in the procurement register.')
    elif vendor and invoice.get('currency') and invoice.get('total') is not None:
        candidates = [r for r in available_pos if r['vendor_id'] == vendor['id'] and r['currency'] == invoice['currency']
                      and r['status'] == 'open' and r['amount']-r['opening']-r['committed'] >= invoice['total']]
        if len(candidates) == 1:
            po = candidates[0]
        elif len(candidates) > 1:
            check('po', 'Purchase order', 'review', f'{len(candidates)} eligible POs match this vendor, currency, and amount. Select the correct PO; the system will not guess.')
        else:
            check('po', 'Purchase order', 'review', 'No explicit PO reference and no uniquely eligible open purchase order.')
    else:
        check('po', 'Purchase order', 'review', 'A PO cannot be matched until vendor, currency, and total are readable.')
    if po:
        po_lines = json.loads(po.get('line_items', '[]'))
        prior_lines = []
        if po_lines:
            for prior in con.execute("SELECT invoice FROM runs WHERE po_id=? AND demo_scope=? AND status='approved' AND id<>?", (po['id'], scope, run['id'])):
                prior_lines.extend(json.loads(prior['invoice']).get('line_items', []))
            for line in po_lines:
                line['committed_quantity'] = str(sum((finance.decimal(item['quantity']) for item in prior_lines
                    if item.get('quantity') is not None and item['description'].casefold().strip() == line['description'].casefold().strip()), Decimal(0)))
        for item in finance.po_checks(invoice, po_lines):
            checks.append(item)
            if item['status'] == 'review':
                issues.append(item['detail'])
        if po['status'] != 'open' or (vendor and po['vendor_id'] != vendor['id']):
            check('po', 'Purchase order', 'fail', 'PO is closed or belongs to a different vendor.')
        else:
            method = 'explicit reference' if po_ref else ('confirmed by reviewer' if selected_po else 'only eligible PO')
            check('po', 'Purchase order', 'pass', f"{po['id']} matched by {method}.")
        if invoice.get('currency') and po['currency'] != invoice['currency']:
            check('currency', 'Currency match', 'fail', f"Invoice currency {invoice['currency']} differs from PO currency {po['currency']}. No FX assumptions are allowed.")
        elif invoice.get('currency'):
            check('currency', 'Currency match', 'pass', f"Invoice and PO are both {po['currency']}.")
        remaining = po['amount'] - po['opening'] - po['committed']
        po['remaining'] = remaining
        po['invoice_amount'] = invoice.get('total')
        po['resulting_available'] = remaining - (invoice.get('total') or 0)
        total = invoice.get('total')
        if total is not None:
            if total > remaining:
                check('balance', 'Cumulative PO balance', 'review', f"{amount_text(total,po['currency'])} exceeds the remaining {amount_text(remaining,po['currency'])} by {amount_text(total-remaining,po['currency'])}. Prior commitments are included.")
            else:
                check('balance', 'Cumulative PO balance', 'pass', f"{amount_text(total,po['currency'])} fits within {amount_text(remaining,po['currency'])} remaining; {amount_text(remaining-total,po['currency'])} will remain.")
    status = 'rejected' if blocks else ('review' if issues else 'approved')
    reasons = blocks if blocks else issues
    summary = ('All policy checks passed. Approval reserves PO authorization; no payment has been made.'
               if status == 'approved' else ' '.join(reasons))
    next_action = ('Send to the AP payment queue.' if status == 'approved' else
                   ('Do not process this invoice. Resolve the blocking issue with procurement.' if status == 'rejected' else
                    'Verify the highlighted evidence and request corrected information from the vendor when necessary.'))
    return {'status': status, 'summary': summary, 'next_action': next_action, 'checks': checks,
            'vendor': vendor, 'po': po, 'candidates': candidates,
            'duplicate_run_id': duplicate['id'] if duplicate else None,
            'policy_version': POLICY, 'policy': finance.settings(), 'demo': bool(scope), 'decided_at': db.now()}


def commit_decision(run_id, invoice, reviewer=None, note=None, selected_po=None, reject=False, corrections=None, save_only=False):
    with db.connection() as con:
        con.execute('BEGIN IMMEDIATE')
        run = con.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if not run:
            raise ValueError('Run does not exist.')
        if reviewer and run['status'] != 'review':
            raise ValueError('Only invoices currently awaiting review can be reviewed.')
        if reviewer and (len(reviewer.strip()) < 2 or len((note or '').strip()) < 10):
            raise ValueError('A reviewer name and reason of at least ten characters are required.')
        if not reviewer and run['status'] != 'processing':
            if run['result']:
                return json.loads(run['result'])
            raise ValueError('This run is no longer processing.')
        if reviewer:
            # Read current fields under the same lock as revalidation/reservation.
            invoice = json.loads(run['invoice'])
            if not selected_po:
                selected_po = (json.loads(run['result']).get('po') or {}).get('id')
        if corrections:
            allowed = {'invoice_date', 'currency', 'subtotal', 'tax', 'total'}
            if not reviewer or set(corrections) - allowed:
                raise ValueError('Corrections support date, currency and document amounts only. Vendor/identity/explicit PO changes require a corrected document.')
            for key, value in corrections.items():
                if not isinstance(value, str) or not value.strip():
                    raise ValueError('Correction values must be nonempty text.')
                invoice[key] = money(value) if key in ('subtotal', 'tax', 'total') else value.strip()
                if key == 'currency' and invoice[key] not in AI_CURRENCIES:
                    raise ValueError('Unsupported correction currency.')
                invoice['conflicts'] = [k for k in invoice.get('conflicts', []) if k != key]
                invoice['evidence'][key] = f'Human correction by {reviewer}: {note}'
                finance.evidence(invoice, key, 'human correction', note)
                invoice['provenance'][key]['reviewer'] = reviewer
            # Recompute tax uncertainty using the original document and new amounts.
            invoice['financial_uncertainty'] = []
            finance.enrich(invoice)
        if reviewer:
            # A recorded human evidence verification resolves provider/reading uncertainty,
            # never arithmetic, duplicates, tax ambiguity, or spending authorization.
            invoice.pop('ai_failure', None)
            invoice.pop('ai_rejected', None)
        result = evaluate(con, run, invoice, selected_po=selected_po, allow_ocr_confirmation=bool(reviewer))
        if save_only and result['status'] == 'approved':
            result.update(status='review', summary='Corrections saved and checks passed. Confirm approval to reserve authorization.', next_action='Verify and approve with a review reason.')
        if reviewer and not reject and not save_only and result['status'] != 'approved':
            raise ValueError('Approval is blocked: ' + result['summary'])
        if reject:
            result.update(status='rejected', summary=f'Rejected by {reviewer}: {note}', next_action='Request a corrected invoice from the vendor.')
        if result['status'] == 'approved':
            if run['demo_scope']:
                con.execute('INSERT INTO demo_pos(scope,po_id,committed) VALUES (?,?,?) ON CONFLICT(scope,po_id) DO UPDATE SET committed=committed+excluded.committed',
                            (run['demo_scope'], result['po']['id'], invoice['total']))
            else:
                con.execute('UPDATE pos SET committed=committed+? WHERE id=?', (invoice['total'], result['po']['id']))
        if reviewer:
            con.execute('INSERT INTO reviews(run_id,reviewer,note,action,created_at,original_result,original_invoice) VALUES (?,?,?,?,?,?,?)',
                        (run_id, reviewer, note, 'reject' if reject else ('correct' if save_only else 'approve'), db.now(), run['result'], run['invoice']))
        con.execute('UPDATE runs SET status=?,invoice=?,result=?,vendor_id=?,invoice_key=?,po_id=?,updated_at=? WHERE id=?',
                    (result['status'], json.dumps(invoice), json.dumps(result),
                     result['vendor']['id'] if result['vendor'] else None, normalize(invoice.get('invoice_number', '')),
                     result['po']['id'] if result['po'] else None, db.now(), run_id))
        return result


def process(run_id):
    run = db.get_run(run_id)
    if not run or run['status'] != 'processing':
        return
    start = time.monotonic()
    notify = lambda *args: db.event(run_id, *args)
    try:
        path = db.DATA / 'uploads' / f'{run_id}.pdf'
        notify('intake', 'complete', f"Received {run['filename']}. SHA-256 recorded; real document stored.")
        notify('read', 'running', 'Inspecting PDF pages and text layers.')
        text, source = read_document(path, notify)
        if not text.strip():
            raise ValueError('No readable content was found. Upload a clearer, correctly oriented invoice.')
        notify('read', 'complete', f"Read {source['pages']} page(s) using {', '.join(dict.fromkeys(source['methods']))}.")
        notify('extract', 'running', 'Extracting invoice fields and retaining matching source lines.')
        invoice = extract(text, source)
        unread = [k for k in REQUIRED if invoice.get(k) is None]
        if ai_extract.enabled() and (unread or ai_extract.crosscheck()):
            notify('extract', 'running',
                   f"Label rules left {len(unread)} required field(s) unread. Reading this layout with Gemini {ai_extract.MODEL}."
                   if unread else f'Cross-checking rule-read fields with Gemini {ai_extract.MODEL}.')
            proposed = ai_extract.propose(text)
            if proposed is None:
                invoice['ai_failure'] = 'The configured AI reader timed out, failed, or returned an invalid/empty response. Verify the original evidence before approval.'
            filled = merge_ai_fields(invoice, proposed, text)
            dropped = [r['field'].replace('_', ' ') for r in invoice.get('ai_rejected', [])]
            message = (f"AI reader recovered {', '.join(f.replace('_', ' ') for f in filled)}, each verified against a verbatim quote in the document."
                       if filled else 'AI reader added nothing it could verify; missing fields remain missing.')
            if dropped:
                message += f" Discarded unverifiable: {', '.join(dropped)}."
            notify('extract', 'running', message)
        elif ai_extract.enabled():
            invoice['ai_status'] = ('AI reader not needed: label rules read every required field with source evidence. '
                                    'Gemini is consulted only for layouts the rules cannot read.')
            notify('extract', 'running', 'Label rules read every required field with source evidence; the Gemini reader was not needed.')
        else:
            invoice['ai_status'] = 'Not configured. Label rules and local OCR only; unsupported fields require review.'
        # Inclusive derivations may become possible only after AI fills a total.
        invoice['financial_uncertainty'] = []
        finance.enrich(invoice)
        count = sum(invoice.get(k) is not None for k in REQUIRED)
        with db.connection() as con:
            con.execute("UPDATE runs SET invoice=? WHERE id=? AND status='processing'", (json.dumps(invoice), run_id))
        ai_count = len(invoice.get('ai_fields', []))
        notify('extract', 'complete', f'{count} of {len(REQUIRED)} required fields extracted with source evidence.'
               + (f' {ai_count} read by the Gemini AI reader; policy checks still apply.' if ai_count else ''))
        notify('validate', 'running', 'Checking completeness, dates, arithmetic, and extraction certainty.')
        validations = validate_fields(invoice)
        passed = sum(c['status'] == 'pass' for c in validations)
        notify('validate', 'complete', f'{passed} of {len(validations)} document checks passed. Evidence retained for the decision.', validations)
        notify('match', 'running', 'Matching procurement records and checking committed balances under a database lock.')
        result = commit_decision(run_id, invoice)
        notify('match', 'complete', result['po']['id'] if result['po'] else 'No unique purchase order selected.')
        notify('decision', 'complete', result['summary'])
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Document processing failed. Try a valid PDF or check server logs.'
        if not isinstance(exc, ValueError):
            import logging
            logging.exception('Processing failed for run %s', run_id)
        with db.connection() as con:
            # Never erase an already committed financial decision after a later event failure.
            con.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=? AND status='processing'", (message, db.now(), run_id))
        current = db.get_run(run_id)
        if current['status'] == 'failed':
            unfinished = [e['stage'] for e in current['events'] if e['status'] == 'running'
                          and not any(later['stage'] == e['stage'] and later['status'] == 'complete' for later in current['events'])]
            if unfinished:
                notify(unfinished[-1], 'failed', message)
            notify('decision', 'failed', message)
        else:
            notify('decision', 'complete', 'Financial decision was committed. A later timeline update failed; consult the recorded decision and PO snapshot.')
    finally:
        with db.connection() as con:
            con.execute('UPDATE runs SET elapsed_ms=? WHERE id=? AND elapsed_ms IS NULL', (round((time.monotonic()-start)*1000), run_id))
