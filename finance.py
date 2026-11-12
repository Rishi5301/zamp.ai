"""Small AP policy: decimal arithmetic, explicit tax, and evidenced invoice lines.

This is invoice/PO validation, not a tax-law or goods-receipt engine.
"""
import os
import re
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
ROUNDING = 1  # one minor unit; never increases spending authorization


def decimal(value):
    value = Decimal(str(value).replace(',', ''))
    if not value.is_finite() or value < 0:
        raise ValueError('Expected a finite nonnegative decimal.')
    return value


def cents(value):
    return int((decimal(value) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def settings():
    return {'rounding_minor': ROUNDING,
            'match_percent': str(decimal(os.getenv('LEDGERFLOW_MATCH_PERCENT', '1'))),
            'match_absolute_minor': cents(os.getenv('LEDGERFLOW_MATCH_ABSOLUTE', '2.00')),
            'authorization_allowance_minor': 0}


def variance(actual, expected, rule='PO line price/amount'):
    policy = settings()
    # Both limits must hold. Floor to whole minor units; endpoints are inclusive.
    permitted = min(policy['match_absolute_minor'],
                    int(Decimal(expected) * Decimal(policy['match_percent']) / 100))
    actual_variance = abs(actual - expected)
    return {'actual_minor': actual_variance, 'permitted_minor': permitted,
            'basis_minor': expected, 'rule': rule,
            'detail': f'Variance {actual_variance / 100:.2f}; permitted {permitted / 100:.2f} '
                      f'(lesser of {policy["match_percent"]}% of PO basis {expected / 100:.2f} '
                      f'and {policy["match_absolute_minor"] / 100:.2f}; inclusive boundary).',
            'status': 'pass' if actual_variance <= permitted else 'review'}


def evidence(invoice, field, method, text, derivation=None):
    page = None
    for index, content in enumerate(invoice.get('raw_text', '').split('\f'), 1):
        if text and ' '.join(text.split()) in ' '.join(content.split()):
            page = index
            break
    record = {'method': method, 'text': text, 'page': page}
    if derivation:
        record['calculation'] = derivation
    invoice.setdefault('provenance', {})[field] = record


def enrich(invoice):
    """Parse only explicit tax declarations and supported, structured line tables.

    Pipe/column tables support description | quantity | unit price | amount with
    optional tax rate/amount columns; bundled description | amount keeps null qty.
    Unrecognized tables remain an explicit review instead of invented mappings.
    """
    text = invoice.get('raw_text', '')
    issues = invoice.setdefault('financial_uncertainty', [])
    issues.extend(invoice.get('line_uncertainty', []))
    for key, quote in invoice.get('evidence', {}).items():
        if key not in invoice.get('provenance', {}):
            evidence(invoice, key, 'label rules', quote)
    declarations = re.findall(r'[^\n\f]*(?:inclusive\s+of|includes|including)\s+(\d+(?:\.\d+)?)\s*%\s*(?:tax|VAT|GST)[^\n\f]*', text, re.I)
    inclusive_text = re.search(r'[^\n\f]*(?:inclusive\s+of|includes|including)\s+\d+(?:\.\d+)?\s*%\s*(?:tax|VAT|GST)[^\n\f]*', text, re.I)
    ambiguous = re.search(r'(?:tax[- ]inclusive|inclusive\s+of\s+(?:tax|VAT|GST)|tax\s+included)', text, re.I)
    rates = re.findall(r'^\s*(?:tax|VAT|GST)\s+rate\s*:\s*(\d+(?:\.\d+)?)\s*%\s*$', text, re.I | re.M)
    if len({decimal(r) for r in declarations + rates}) > 1:
        issues.append('Conflicting tax rates; verify tax treatment against the original document.')
    elif declarations and invoice.get('total') is not None and 'total' not in invoice.get('conflicts', []):
        rate = decimal(declarations[0])
        if rate > 100:
            issues.append('Unsupported tax rate above 100%; verify the document.')
        else:
            invoice['tax_mode'] = 'inclusive'
            invoice['tax_rate'] = str(rate)
            net = int((Decimal(invoice['total']) / (1 + rate / 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            tax = invoice['total'] - net
            for key, value in [('subtotal', net), ('tax', tax)]:
                if key in invoice.get('conflicts', []):
                    continue
                if invoice.get(key) is None or invoice.get('provenance', {}).get(key, {}).get('method') == 'derived':
                    invoice[key] = value
                    quote = inclusive_text.group(0).strip()
                    invoice.setdefault('evidence', {})[key] = quote
                    evidence(invoice, key, 'derived', quote,
                             f'Net = {invoice["total"]/100:.2f} / (1 + {rate}%); rounded half up = {net/100:.2f}. '
                             f'Tax = {invoice["total"]/100:.2f} - {net/100:.2f} = {tax/100:.2f}.')
                    invoice['provenance'][key]['inputs'] = {
                        'total': {'value_minor': invoice['total'], 'evidence': invoice.get('evidence', {}).get('total'),
                                  'page': invoice.get('provenance', {}).get('total', {}).get('page')},
                        'tax_rate': {'value_percent': str(rate), 'evidence': quote}}
                elif abs(invoice[key] - value) > ROUNDING:
                    issues.append(f'Explicit {key} contradicts the inclusive-tax calculation.')
    elif ambiguous:
        issues.append('Tax-inclusive wording needs an explicit applicable rate and readable total; tax was not guessed.')
    elif invoice.get('tax') is not None:
        invoice['tax_mode'] = 'separate'
        if rates:
            invoice['tax_rate'] = rates[0]
    else:
        issues.append('Tax treatment is ambiguous: supply separately stated tax or an explicit inclusive amount and rate.')

    # An outstanding balance is not necessarily the original invoice total.
    if re.search(r'\b(?:previous balance|amount paid|payments received|credit applied|balance brought forward)\b', text, re.I):
        issues.append('Payment or previous-balance amounts appear in this document. Verify the original invoice total; amount due is not sufficient evidence.')

    if 'line_items' not in invoice:
        before_lines = len(issues)
        lines, header, seen_table = [], None, False
        for raw in text.splitlines():
            parts = [p.strip() for p in (raw.split('|') if '|' in raw else re.split(r'\s{2,}', raw.strip()))]
            lower = [p.lower() for p in parts]
            if any(p in ('description', 'item', 'service') for p in lower) and any(p in ('amount', 'line amount') for p in lower):
                header, seen_table = lower, True
                continue
            if header and len(parts) == len(header) and len(parts) >= 2:
                row = dict(zip(header, parts))
                try:
                    description = row.get('description', row.get('item', row.get('service')))
                    amount = cents(row.get('amount', row.get('line amount')))
                    quantity = row.get('quantity', row.get('qty'))
                    price = row.get('unit price', row.get('price'))
                    rate = row.get('tax rate')
                    line_tax = row.get('tax amount')
                    for monetary in [row.get('amount', row.get('line amount')), price, line_tax]:
                        if monetary is not None and decimal(monetary)*100 != (decimal(monetary)*100).to_integral_value():
                            issues.append('Line monetary precision exceeds two decimals; verify the original before approval.')
                    item = {'description': description, 'quantity': str(decimal(quantity)) if quantity else None,
                            'unit_price': cents(price) if price else None, 'amount': amount,
                            'tax_rate': str(decimal(rate.rstrip('%'))) if rate else None,
                            'tax_amount': cents(line_tax) if line_tax else None,
                            'evidence': raw.strip()}
                    if (quantity is None) != (price is None):
                        issues.append('Incomplete line quantity/price columns; verify line arithmetic.')
                    lines.append(item)
                except (ValueError, ArithmeticError, TypeError):
                    issues.append('A line-table row could not be read reliably; verify all charges.')
            elif header and re.search(r'^(?:subtotal|tax|total|grand total|net amount)\s*:', raw.strip(), re.I):
                header = None
            elif header and re.search(r'\d', raw) and not re.search(r'(?:inclusive\s+of|includes|including)\s+\d+(?:\.\d+)?\s*%', raw, re.I):
                issues.append('A possible line-table row has an unsupported column layout; verify all charges.')
        invoice['line_items'] = lines
        if not lines and (seen_table or re.search(r'\b(?:qty|quantity|unit price)\b', text, re.I)):
            issues.append('Line-item table was detected but not read reliably. Verify its rows and mappings.')
        invoice['line_uncertainty'] = issues[before_lines:]
    invoice['financial_uncertainty'] = list(dict.fromkeys(issues))
    return invoice


def checks(invoice):
    out = []
    def add(key, label, passed, detail, **extra):
        out.append(dict(key=key, label=label, status='pass' if passed else 'review', detail=detail, **extra))
    for index, issue in enumerate(invoice.get('financial_uncertainty', [])):
        add(f'financial-{index}', 'Tax / extraction uncertainty', False, issue)
    if invoice.get('tax_rate') is not None and invoice.get('tax_mode') == 'separate' and invoice.get('subtotal') is not None and invoice.get('tax') is not None:
        expected = int((Decimal(invoice['subtotal']) * decimal(invoice['tax_rate']) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        add('tax-rate', 'Stated tax rate', abs(expected - invoice['tax']) <= ROUNDING,
            f'Net × {invoice["tax_rate"]}% = {expected/100:.2f}; printed tax {invoice["tax"]/100:.2f}; rounding allowance 0.01.')
    lines = invoice.get('line_items', [])
    for n, line in enumerate(lines, 1):
        if line.get('quantity') is not None and line.get('unit_price') is not None:
            expected = int((decimal(line['quantity']) * line['unit_price']).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            add(f'line-{n}', f'Line {n} arithmetic', abs(expected-line['amount']) <= ROUNDING,
                f'{line["description"]}: {line["quantity"]} × {line["unit_price"]/100:.2f} = {expected/100:.2f}; printed {line["amount"]/100:.2f}; rounding allowance 0.01.')
        if line.get('tax_rate') is not None and line.get('tax_amount') is not None:
            rate = decimal(line['tax_rate'])
            expected = (Decimal(line['amount']) * rate / (100 + rate) if invoice.get('tax_mode') == 'inclusive'
                        else Decimal(line['amount']) * rate / 100)
            expected = int(expected.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            add(f'line-tax-{n}', f'Line {n} tax', abs(expected-line['tax_amount']) <= ROUNDING,
                f'Stated rate {rate}% gives tax {expected/100:.2f}; printed {line["tax_amount"]/100:.2f}.')
    if lines:
        basis = 'total' if invoice.get('tax_mode') == 'inclusive' else 'subtotal'
        expected = invoice.get(basis)
        actual = sum(line['amount'] for line in lines)
        add('line-sum', 'Line charges reconcile', expected is not None and abs(actual-expected) <= ROUNDING,
            f'Line amounts sum to {actual/100:.2f}; compare with {basis} {expected/100:.2f}; rounding allowance 0.01.' if expected is not None else 'Document total/net is missing.')
        if all(line.get('tax_amount') is not None for line in lines) and invoice.get('tax') is not None:
            actual_tax = sum(line['tax_amount'] for line in lines)
            add('line-tax-sum', 'Line taxes reconcile', abs(actual_tax-invoice['tax']) <= ROUNDING,
                f'Line taxes {actual_tax/100:.2f}; document tax {invoice["tax"]/100:.2f}.')
        rates = {decimal(line['tax_rate']) for line in lines if line.get('tax_rate') is not None}
        if rates and (len(rates) > 1 or (invoice.get('tax_rate') is not None and rates != {decimal(invoice['tax_rate'])})):
            add('mixed-tax', 'Line tax basis', False, 'Mixed or contradictory line tax rates require review; a single-rate derivation is insufficient.')
        if all(line.get('tax_rate') is not None for line in lines) and invoice.get('tax') is not None:
            expected_taxes = []
            for line in lines:
                rate = decimal(line['tax_rate'])
                expected = (Decimal(line['amount']) * rate / (100 + rate) if invoice.get('tax_mode') == 'inclusive'
                            else Decimal(line['amount']) * rate / 100)
                expected_taxes.append(int(expected.quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
            add('line-rate-sum', 'Line rates reconcile to document tax', abs(sum(expected_taxes)-invoice['tax']) <= ROUNDING,
                f'Tax calculated from explicit line rates is {sum(expected_taxes)/100:.2f}; document tax {invoice["tax"]/100:.2f}.')
    return out


def po_checks(invoice, po_lines):
    if not po_lines:
        return [dict(key='po-lines', label='PO matching scope', status='pass',
                     detail='Amount-only PO: vendor, currency and remaining gross authorization checked. No authoritative PO lines; quantities, unit prices and delivery are not verified.')]
    out, used = [], set()
    for index, line in enumerate(invoice.get('line_items', []), 1):
        # Deliberately exact description matching; do not invent a SKU or allocation.
        matches = [(i, p) for i, p in enumerate(po_lines) if p['description'].casefold().strip() == line['description'].casefold().strip()]
        if len(matches) != 1 or matches[0][0] in used:
            out.append(dict(key=f'po-map-{index}', label='PO line mapping', status='review', detail=f'No unique unused PO line maps to “{line["description"]}”. Procurement must clarify the allocation.'))
            continue
        i, expected = matches[0]
        used.add(i)
        if (invoice.get('tax_mode') == 'inclusive') != (expected.get('tax_basis', 'net') == 'gross'):
            out.append(dict(key=f'po-basis-{index}', label='PO line tax basis', status='review', detail='Invoice line and PO line tax bases differ; explicit net/gross allocation is needed.'))
            continue
        if expected.get('quantity') is not None:
            remaining = decimal(expected['quantity']) - decimal(expected.get('opening_quantity', 0)) - decimal(expected.get('committed_quantity', 0))
            if line.get('quantity') is None or decimal(line['quantity']) > remaining:
                out.append(dict(key=f'po-qty-{index}', label='PO line quantity', status='review', detail=f'Quantity is missing or exceeds ordered quantity remaining ({remaining}); no quantity overrun is authorized.'))
            elif expected.get('opening_quantity') is None:
                out.append(dict(key=f'po-qty-{index}', label='Cumulative line allocation', status='review', detail='PO line lacks authoritative opening quantity. Procurement must supply prior line allocations; quantities were not assumed to be unconsumed.'))
            else:
                out.append(dict(key=f'po-qty-{index}', label='PO line quantity', status='pass', detail=f'Invoice quantity {line["quantity"]} ≤ remaining {remaining} of ordered {expected["quantity"]}; opening {expected["opening_quantity"]}, approved here {expected.get("committed_quantity", 0)}. Delivery/receipt is not verified.'))
        for field in ('unit_price', 'amount'):
            if expected.get(field) is None:
                continue
            basis = expected[field]
            if field == 'amount' and expected.get('quantity') and line.get('quantity') is not None:
                basis = int((Decimal(basis) * decimal(line['quantity']) / decimal(expected['quantity'])).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            if line.get(field) is None:
                out.append(dict(key=f'po-{field}-{index}', label='PO line comparison', status='review', detail=f'Invoice has no supported {field} for a PO line comparison.'))
            else:
                result = variance(line[field], basis)
                out.append(dict(key=f'po-{field}-{index}', label=f'PO line {index} {field.replace("_", " ")}', **result))
    if not invoice.get('line_items'):
        out.append(dict(key='po-map', label='PO line mapping', status='review', detail='PO has line-level detail but invoice lines were not read. Confirm the mapping; no allocation was invented.'))
    return out
