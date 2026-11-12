"""Synthetic invoices: real PDF bytes, independent of the processing engine."""
import io
from pathlib import Path

import pymupdf
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).parent / 'samples'
SAMPLES = [
    {'id': '01-happy-path', 'title': 'A clean invoice', 'category': 'Happy path', 'expected': 'approved',
     'description': 'Complete invoice, approved vendor, and an explicit PO. Ready for AP.',
     'vendor': 'Northstar Cloud Ltd', 'number': 'NC-2026-084', 'po': 'PO-2026-1048',
     'subtotal': 4500, 'tax': 450, 'total': 4950, 'currency': 'USD'},
    {'id': '02-reexported-duplicate', 'title': 'Same invoice. Different PDF.', 'category': 'Edge case 01', 'expected': 'rejected',
     'description': 'An isolated scenario seeds the original first. Different PDF bytes, same vendor and invoice number; second reservation blocked.',
     'vendor': 'Northstar Cloud Ltd', 'number': 'NC-2026-084', 'po': 'PO-2026-1048',
     'subtotal': 4500, 'tax': 450, 'total': 4950, 'currency': 'USD', 'alternate': True},
    {'id': '03-ambiguous-po', 'title': 'Two possible purchase orders', 'category': 'Edge case 04', 'expected': 'review',
     'description': 'No PO reference. Two open POs qualify, so a reviewer must select one.',
     'vendor': 'Northstar Cloud Ltd', 'number': 'NC-2026-091', 'po': None,
     'subtotal': 900, 'tax': 90, 'total': 990, 'currency': 'USD'},
    {'id': '04-split-billing-overrun', 'title': 'The balance tells a different story', 'category': 'Edge case 02', 'expected': 'review',
     'description': 'A $2,450 invoice against a $10,000 PO with only $2,000 remaining.',
     'vendor': 'Meridian Design Studio', 'number': 'MD-2026-217', 'po': 'PO-2026-1051',
     'subtotal': 2250, 'tax': 200, 'total': 2450, 'currency': 'USD'},
    {'id': '05-incomplete-scan', 'title': 'A scan with a missing date', 'category': 'Supporting OCR case', 'expected': 'review',
     'description': 'An image-only invoice. OCR reads it; the missing date still prevents approval.',
     'vendor': 'Atlas Office Supplies', 'number': 'AO-2026-332', 'po': 'PO-2026-1062',
     'subtotal': 20000, 'tax': 3600, 'total': 23600, 'currency': 'INR', 'scan': True, 'missing_date': True},
    {'id': '06-complete-scan', 'title': 'Paper to a decision', 'category': 'OCR happy path', 'expected': 'approved',
     'description': 'A complete image-only PDF, read by a local neural OCR model. No API key needed.',
     'vendor': 'Atlas Office Supplies', 'number': 'AO-2026-333', 'po': 'PO-2026-1062',
     'subtotal': 10000, 'tax': 1800, 'total': 11800, 'currency': 'INR', 'scan': True},
    {'id': '07-unfamiliar-layout', 'title': 'A layout the labels miss', 'category': 'AI extraction', 'expected': 'review',
     'description': 'An unusual vendor layout with none of the standard field labels. Rule-based extraction cannot read it, so it is held for review — unless the Gemini reader is enabled, which reads the layout and lets the same deterministic policy approve it.',
     'vendor': 'Northstar Cloud Ltd', 'number': 'NC-2026-120', 'po': 'PO-2026-1053',
     'subtotal': 9000, 'tax': 900, 'total': 9900, 'currency': 'USD', 'date': '2026-11-05', 'unstructured': True},
    {'id': '08-inclusive-bundle', 'title': 'Inclusive tax, reconciled charges', 'category': 'Edge case 03', 'expected': 'approved',
     'description': 'Two service bundles total $1,180 inclusive of 18% tax. Derive $1,000 net and $180 tax from explicit evidence; do not invent quantities.',
     'vendor': 'Northstar Cloud Ltd', 'number': 'NC-2026-130', 'po': 'PO-2026-1048',
     'subtotal': 1000, 'tax': 180, 'total': 1180, 'currency': 'USD', 'inclusive': True,
     'lines': ['Description | Amount', 'Cloud service bundle | 708.00', 'Support service bundle | 472.00']},
    {'id': '09-itemised-separate-tax', 'title': 'Itemised charges, separate tax', 'category': 'Supporting reconciliation', 'expected': 'approved',
     'description': 'The equivalent $1,000 net + $180 tax = $1,180, with explicit quantities and unit prices.',
     'vendor': 'Northstar Cloud Ltd', 'number': 'NC-2026-131', 'po': 'PO-2026-1048',
     'subtotal': 1000, 'tax': 180, 'total': 1180, 'currency': 'USD',
     'lines': ['Description | Quantity | Unit price | Amount', 'Cloud compute | 2 | 300.00 | 600.00', 'Support hours | 4 | 100.00 | 400.00']},
]


def unstructured_pdf(s):
    """A deliberately non-standard layout: real text, but no labels the rule-based
    extractor recognizes. It forces the AI reader (when enabled) and is otherwise
    held for review. Amounts use words/labels the deterministic patterns do not match."""
    output = io.BytesIO()
    c = canvas.Canvas(output, pagesize=A4, invariant=1)
    w, h = A4
    ink, muted, accent = colors.HexColor('#1b2a33'), colors.HexColor('#6b7784'), colors.HexColor('#2f6db5')
    c.setFillColor(accent)
    c.rect(0, h - 14, w, 14, fill=1, stroke=0)
    c.setFillColor(ink)
    c.setFont('Helvetica-Bold', 22)
    c.drawString(48, h - 78, 'Billing statement')
    c.setFont('Helvetica', 11)
    y = h - 118
    lines = [
        f"Issued by {s['vendor']} for professional services rendered.",
        f"Document ref  {s['number']}",
        'Dated 05 Nov 2026',
        f"Raised against order {s['po']}",
        'All amounts shown are payable in US Dollars.',
    ]
    for text in lines:
        c.drawString(48, y, text)
        y -= 24
    y -= 30
    c.setFont('Helvetica-Bold', 11)
    c.drawString(48, y, 'Charges')
    c.setFont('Helvetica', 11)
    y -= 26
    for label, key in [('Net', 'subtotal'), ('Sales tax', 'tax'), ('Payable', 'total')]:
        c.setFont('Helvetica-Bold' if key == 'total' else 'Helvetica', 12 if key == 'total' else 11)
        c.drawString(60, y, label)
        c.drawRightString(w - 60, y, f"{s[key]:,.2f}")
        y -= 26
    c.setFillColor(muted)
    c.setFont('Helvetica', 9)
    c.drawString(48, 70, 'SYNTHETIC DEMO DOCUMENT - no real vendor or payment details')
    c.drawString(48, 55, 'Non-standard layout: no conventional field labels are present.')
    c.save()
    return output.getvalue()


def invoice_pdf(s):
    if s.get('unstructured'):
        return unstructured_pdf(s)
    output = io.BytesIO()
    c = canvas.Canvas(output, pagesize=A4, invariant=1)
    w, h = A4
    ink, muted, accent = colors.HexColor('#182b27'), colors.HexColor('#66746e'), colors.HexColor('#237c60')
    c.setFillColor(accent)
    c.rect(0, h - 12, w, 12, fill=1, stroke=0)
    c.setFillColor(ink)
    c.setFont('Helvetica-Bold', 26)
    c.drawString(48, h - 80, 'INVOICE' if not s.get('alternate') else 'INVOICE / COPY')
    c.setFont('Helvetica', 11)
    y = h - 125
    for text in [f"Vendor: {s['vendor']}", f"Invoice number: {s['number']}",
                 None if s.get('missing_date') else f"Invoice date: {s.get('date', '2026-10-28')}",
                 f"PO number: {s['po']}" if s.get('po') else None,
                 f"Currency: {s['currency']}"]:
        if text:
            c.drawString(48, y, text)
            y -= 25
    y -= 40
    c.setFillColor(colors.HexColor('#edf3ef'))
    c.rect(48, y - 8, w - 96, 29, fill=1, stroke=0)
    c.setFillColor(ink)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(60, y + 2, 'DESCRIPTION')
    c.drawRightString(w - 60, y + 2, 'AMOUNT')
    c.setFont('Helvetica', 11)
    y -= 40
    if s.get('lines'):
        c.setFont('Helvetica', 9)
        for line in s['lines']:
            c.drawString(60, y, line)
            y -= 20
        if s.get('inclusive'):
            c.drawString(60, y, 'Total inclusive of 18% tax')
            y -= 20
    else:
        c.drawString(60, y, 'Services and supplies as agreed')
        c.drawRightString(w - 60, y, f"{s['subtotal']:,.2f}")
    y -= 70
    for label, key in [('Subtotal', 'subtotal'), ('Tax', 'tax'), ('Total', 'total')]:
        if s.get('inclusive') and key != 'total':
            continue
        c.setFont('Helvetica-Bold' if key == 'total' else 'Helvetica', 12)
        c.drawString(310, y, f"{label}: {s[key]:,.2f}")
        y -= 29
    c.setFillColor(muted)
    c.setFont('Helvetica', 9)
    c.drawString(48, 70, 'SYNTHETIC DEMO DOCUMENT - no real vendor or payment details')
    if s.get('alternate'):
        c.drawString(48, 55, 'Re-exported from vendor portal. Original invoice identity retained.')
    c.save()
    data = output.getvalue()
    if s.get('scan'):
        with pymupdf.open(stream=data, filetype='pdf') as doc:
            pix = doc[0].get_pixmap(dpi=150)
            scanned = pymupdf.open()
            page = scanned.new_page(width=w, height=h)
            page.insert_image(page.rect, stream=pix.tobytes('png'))
            data = scanned.tobytes(deflate=True)
            scanned.close()
    return data


def ensure_samples():
    ROOT.mkdir(exist_ok=True)
    for sample in SAMPLES:
        path = ROOT / (sample['id'] + '.pdf')
        if not path.exists():
            path.write_bytes(invoice_pdf(sample))


if __name__ == '__main__':
    ensure_samples()
    print(f'Created {len(SAMPLES)} genuine PDF fixtures in {ROOT}')
