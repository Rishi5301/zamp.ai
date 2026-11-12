"""Behavioral tests with real PDFs, a real database, and no mocked decision engine."""
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest

import db
import engine
from fixtures import SAMPLES, invoice_pdf


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    # A developer's real key or cross-check setting must never reach the provider from tests.
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_CROSSCHECK', raising=False)
    db.initialize()


def register(content, filename='invoice.pdf'):
    run_id = str(uuid.uuid4())
    (db.DATA / 'uploads' / f'{run_id}.pdf').write_bytes(content)
    with db.connection() as con:
        con.execute('INSERT INTO runs(id,filename,sha256,status,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                    (run_id, filename, hashlib.sha256(content).hexdigest(), 'processing', db.now(), db.now()))
    return run_id


def run_sample(index=0, **changes):
    sample = dict(SAMPLES[index], **changes)
    run_id = register(invoice_pdf(sample))
    engine.process(run_id)
    run = db.get_run(run_id)
    assert run['status'] != 'failed', run['error']
    return run


def po_balance(po_id):
    with db.connection() as con:
        r = con.execute('SELECT * FROM pos WHERE id=?', (po_id,)).fetchone()
        return r['amount'] - r['opening'] - r['committed']


def test_happy_path_extracts_real_pdf_and_reserves_exact_gross_amount():
    run = run_sample()
    assert run['status'] == 'approved'
    assert run['invoice']['total'] == 495000
    assert 'Northstar Cloud Ltd' in run['invoice']['evidence']['vendor']
    assert run['invoice']['source']['methods'] == ['PDF text']
    assert po_balance('PO-2026-1048') == 305000
    assert {e['stage'] for e in run['events']} == {'intake', 'read', 'extract', 'validate', 'match', 'decision'}
    assert run['elapsed_ms'] is not None


def test_reexported_pdf_is_duplicate_by_vendor_invoice_identity():
    original = run_sample()
    duplicate = run_sample(1)
    assert original['sha256'] != duplicate['sha256']
    assert duplicate['status'] == 'rejected'
    assert duplicate['result']['duplicate_run_id'] == original['id']
    assert po_balance('PO-2026-1048') == 305000


def test_exact_duplicate_still_detected_after_renaming_file():
    original = run_sample()
    content = (db.DATA / 'uploads' / f"{original['id']}.pdf").read_bytes()
    run_id = register(content, 'totally-different-name.pdf')
    engine.process(run_id)
    assert db.get_run(run_id)['status'] == 'rejected'
    assert po_balance('PO-2026-1048') == 305000


def test_duplicate_identity_normalizes_vendor_alias_and_invoice_punctuation():
    original = run_sample()
    duplicate = run_sample(number='NC2026084', vendor='Northstar Cloud Limited', alternate=True)
    assert duplicate['status'] == 'rejected'
    assert duplicate['result']['duplicate_run_id'] == original['id']


def test_missing_po_with_two_candidates_waits_for_human_and_does_not_reserve():
    run = run_sample(2)
    assert run['status'] == 'review'
    assert len(run['result']['candidates']) == 2
    assert run['result']['po'] is None
    assert po_balance('PO-2026-1048') == 800000
    engine.commit_decision(run['id'], run['invoice'], reviewer='AP reviewer', note='Confirmed the cloud infrastructure order.', selected_po='PO-2026-1048')
    updated = db.get_run(run['id'])
    assert updated['status'] == 'approved'
    assert po_balance('PO-2026-1048') == 701000
    assert updated['reviews'][0]['reviewer'] == 'AP reviewer'
    with db.connection() as con:
        previous = json.loads(con.execute('SELECT original_result FROM reviews').fetchone()[0])
        assert previous['status'] == 'review'


def test_one_eligible_po_can_be_inferred_but_not_invented():
    run = run_sample(5, scan=False, po=None)
    assert run['status'] == 'approved'
    assert run['result']['po']['id'] == 'PO-2026-1062'
    assert any('only eligible PO' in c['detail'] for c in run['result']['checks'])


def test_split_billing_uses_remaining_balance_not_original_po_amount():
    run = run_sample(3)
    assert run['status'] == 'review'
    assert '450.00' in run['result']['summary']
    assert po_balance('PO-2026-1051') == 200000


def test_human_cannot_waive_remaining_balance():
    run = run_sample(3)
    with pytest.raises(ValueError, match='exceeds the remaining'):
        engine.commit_decision(run['id'], run['invoice'], reviewer='AP reviewer', note='Approve this exception anyway.')
    assert db.get_run(run['id'])['status'] == 'review'
    assert po_balance('PO-2026-1051') == 200000
    assert db.get_run(run['id'])['reviews'] == []


def test_human_approval_rechecks_current_balance_after_another_invoice():
    pending = run_sample(2)
    approved = run_sample(number='NC-OTHER', subtotal=7000, tax=500, total=7500)
    assert approved['status'] == 'approved'
    with pytest.raises(ValueError, match='remaining'):
        engine.commit_decision(pending['id'], pending['invoice'], reviewer='AP reviewer', note='Confirmed the correct order.', selected_po='PO-2026-1048')
    assert po_balance('PO-2026-1048') == 50000
    engine.commit_decision(pending['id'], pending['invoice'], reviewer='AP reviewer', note='Confirmed managed services instead.', selected_po='PO-2026-1053')
    assert po_balance('PO-2026-1053') == 1701000


def test_simultaneous_approvals_cannot_overspend_one_po():
    ids = [register(invoice_pdf(dict(SAMPLES[0], number=f'CONCURRENT-{n}', subtotal=4500, tax=500, total=5000))) for n in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(engine.process, ids))
    statuses = sorted(db.get_run(i)['status'] for i in ids)
    assert statuses == ['approved', 'review']
    assert po_balance('PO-2026-1048') == 300000


def test_simultaneous_duplicate_submissions_reserve_only_once():
    ids = [register(invoice_pdf(dict(SAMPLES[0], alternate=bool(n)))) for n in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(engine.process, ids))
    assert sorted(db.get_run(i)['status'] for i in ids) == ['approved', 'rejected']
    assert po_balance('PO-2026-1048') == 305000


def test_actual_image_only_pdf_runs_neural_ocr_and_holds_missing_date():
    content = invoice_pdf(SAMPLES[4])
    with pymupdf.open(stream=content, filetype='pdf') as pdf:
        assert pdf[0].get_text().strip() == ''
    run = run_sample(4)
    assert run['status'] == 'review'
    assert run['invoice']['source']['methods'] == ['Neural OCR']
    assert run['invoice']['total'] == 2360000
    assert 'invoice date' in run['result']['summary']
    assert po_balance('PO-2026-1062') == 23600000


def test_complete_scan_can_approve_from_real_ocr():
    run = run_sample(5)
    assert run['status'] == 'approved'
    assert run['invoice']['source']['methods'] == ['Neural OCR']
    assert run['invoice']['invoice_number'] == 'AO-2026-333'
    assert po_balance('PO-2026-1062') == 22420000


def test_large_invoice_image_with_small_text_overlay_still_uses_ocr(tmp_path):
    with pymupdf.open(stream=invoice_pdf(SAMPLES[5]), filetype='pdf') as pdf:
        pdf[0].insert_text((40, 30), 'Archive metadata ' * 10, fontsize=5)
        path = tmp_path / 'mixed-layer.pdf'
        pdf.save(path)
    text, source = engine.read_document(path)
    assert 'Neural OCR' in source['methods']
    assert engine.extract(text, source)['total'] == 1180000


def test_inconsistent_tax_arithmetic_holds_without_reserving():
    run = run_sample(total=4900)
    assert run['status'] == 'review'
    assert 'does not reconcile' in run['result']['summary']
    assert po_balance('PO-2026-1048') == 800000


@pytest.mark.parametrize('bad_date', ['2026-02-30', '2999-01-01', '28/10/2026', '20261028'])
def test_invalid_future_or_ambiguous_date_is_held(bad_date):
    run = run_sample(date=bad_date)
    assert run['status'] == 'review'
    assert po_balance('PO-2026-1048') == 800000


def test_currency_mismatch_is_blocked_without_conversion():
    run = run_sample(currency='EUR')
    assert run['status'] == 'rejected'
    assert 'differs from PO currency' in run['result']['summary']
    assert po_balance('PO-2026-1048') == 800000


def test_unsupported_currency_is_held_without_defaulting_to_usd():
    run = run_sample(currency='JPY')
    assert run['status'] == 'review'
    assert run['invoice'].get('currency') is None
    assert po_balance('PO-2026-1048') == 800000


def test_unapproved_vendor_is_blocked():
    run = run_sample(vendor='Redwood Services')
    assert run['status'] == 'rejected'
    assert 'not approved' in run['result']['summary']


def test_unknown_vendor_is_held_instead_of_fuzzy_matched():
    run = run_sample(vendor='Northstar Cloud Unrecognized')
    assert run['status'] == 'review'
    assert run['result']['vendor'] is None


def test_explicit_wrong_vendor_po_is_not_replaced_with_another():
    run = run_sample(po='PO-2026-1051')
    assert run['status'] == 'rejected'
    assert 'different vendor' in run['result']['summary']


def test_conflicting_totals_are_not_silently_selected():
    invoice = engine.extract('Vendor: Northstar Cloud Ltd\nInvoice number: X-101\nInvoice date: 2026-10-28\nPO number: PO-2026-1048\nCurrency: USD\nSubtotal: 100.00\nTax: 10.00\nTotal: 110.00\nTotal: 120.00\n', {'methods':['PDF text']})
    rid = register(b'%PDF-test-conflict')
    result = engine.commit_decision(rid, invoice)
    assert result['status'] == 'review'
    assert 'total' in invoice['conflicts']
    assert po_balance('PO-2026-1048') == 800000


def test_missing_tax_is_not_assumed_zero():
    invoice = engine.extract('Vendor: Northstar Cloud Ltd\nInvoice number: X-102\nInvoice date: 2026-10-28\nPO number: PO-2026-1048\nCurrency: USD\nSubtotal: 100.00\nTotal: 100.00\n', {'methods':['PDF text']})
    result = engine.commit_decision(register(b'%PDF-test-missing-tax'), invoice)
    assert result['status'] == 'review'
    assert invoice.get('tax') is None


def test_reviewer_cannot_reapprove_and_double_reserve():
    run = run_sample(2)
    engine.commit_decision(run['id'], run['invoice'], reviewer='AP reviewer', note='Confirmed purchase order.', selected_po='PO-2026-1048')
    with pytest.raises(ValueError, match='currently awaiting review'):
        engine.commit_decision(run['id'], run['invoice'], reviewer='AP reviewer', note='Trying a repeated request.', selected_po='PO-2026-1048')
    assert po_balance('PO-2026-1048') == 701000


def test_retrying_a_committed_decision_is_idempotent():
    run = run_sample()
    result = engine.commit_decision(run['id'], run['invoice'])
    assert result['status'] == 'approved'
    assert po_balance('PO-2026-1048') == 305000


def test_reviewer_cannot_replace_explicit_po_to_bypass_balance():
    run = run_sample(3)
    with pytest.raises(ValueError, match='cannot replace an explicit PO'):
        engine.commit_decision(run['id'], run['invoice'], reviewer='AP reviewer', note='Try another unrelated order.', selected_po='PO-2026-1048')


def test_review_rejection_keeps_original_decision_and_note():
    run = run_sample(3)
    engine.commit_decision(run['id'], run['invoice'], reviewer='AP reviewer', note='Vendor will issue a corrected invoice.', reject=True)
    assert db.get_run(run['id'])['status'] == 'rejected'
    assert db.get_run(run['id'])['reviews'][0]['action'] == 'reject'
    assert po_balance('PO-2026-1051') == 200000


def test_corrupted_pdf_produces_visible_failure_and_no_commitments():
    rid = register(b'%PDF-1.7\nThis is not a valid PDF')
    engine.process(rid)
    run = db.get_run(rid)
    assert run['status'] == 'failed'
    assert run['error']
    assert run['events'][-1]['status'] == 'failed'
    assert po_balance('PO-2026-1048') == 800000


def test_password_protected_pdf_reports_an_actionable_error(tmp_path):
    path = tmp_path / 'locked.pdf'
    with pymupdf.open(stream=invoice_pdf(SAMPLES[0]), filetype='pdf') as pdf:
        pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='private')
    with pytest.raises(ValueError, match='password protected'):
        engine.read_document(path)


def test_unfamiliar_layout_is_held_for_review_without_ai_reader():
    run = run_sample(6)
    assert run['status'] == 'review'
    assert all(run['invoice'].get(k) is None for k in ('vendor', 'invoice_number', 'currency', 'total'))
    assert po_balance('PO-2026-1053') == 1800000


# What a correct model reading of 07-unfamiliar-layout looks like: value + verbatim quote.
AI_READING = {
    'vendor': ('Northstar Cloud Ltd', 'Issued by Northstar Cloud Ltd for professional services rendered.'),
    'invoice_number': ('NC-2026-120', 'Document ref NC-2026-120'),
    'invoice_date': ('2026-11-05', 'Dated 05 Nov 2026'),
    'po_number': ('PO-2026-1053', 'Raised against order PO-2026-1053'),
    'currency': ('USD', 'All amounts shown are payable in US Dollars.'),
    'subtotal': ('9000.00', 'Net 9,000.00'),
    'tax': ('900.00', 'Sales tax 900.00'),
    'total': ('9900.00', 'Payable 9,900.00'),
}


def simulate_ai(monkeypatch, **changes):
    reading = dict(AI_READING, **changes)
    monkeypatch.setattr(engine.ai_extract, 'enabled', lambda: True)
    monkeypatch.setattr(engine.ai_extract, 'propose',
                        lambda text: {k: {'value': v, 'quote': q} for k, (v, q) in reading.items()})


def test_ai_reader_recovers_unfamiliar_layout_and_policy_approves(monkeypatch):
    simulate_ai(monkeypatch)
    run = run_sample(6)
    assert run['status'] == 'approved'
    assert set(AI_READING) == set(run['invoice']['ai_fields'])
    assert 'Payable 9,900.00' in run['invoice']['evidence']['total']
    assert run['invoice']['total'] == 990000
    assert po_balance('PO-2026-1053') == 810000


def test_ai_value_missing_from_its_quote_is_discarded(monkeypatch):
    simulate_ai(monkeypatch, total=('9500.00', 'Payable 9,900.00'))  # invented amount
    run = run_sample(6)
    assert run['status'] == 'review'
    assert run['invoice'].get('total') is None
    assert run['invoice']['ai_rejected'] == [{'field': 'total', 'reason': 'the amount is not in its quote'}]
    assert po_balance('PO-2026-1053') == 1800000


def test_ai_quote_that_is_not_in_the_document_is_discarded(monkeypatch):
    simulate_ai(monkeypatch, vendor=('Northstar Cloud Ltd', 'Billed by Northstar Cloud Ltd'))  # invented quote
    run = run_sample(6)
    assert run['status'] == 'review'
    assert run['invoice'].get('vendor') is None
    assert run['invoice']['ai_rejected'][0]['field'] == 'vendor'


def test_grounded_but_misread_ai_amount_is_rejected_by_field_semantics(monkeypatch):
    simulate_ai(monkeypatch, total=('9000.00', 'Net 9,000.00'))  # real quote, wrong line
    run = run_sample(6)
    assert run['status'] == 'review'
    assert run['invoice'].get('total') is None
    assert 'financial field' in run['invoice']['ai_rejected'][0]['reason']
    assert po_balance('PO-2026-1053') == 1800000


def test_rule_read_invoice_never_calls_or_depends_on_the_ai_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(engine.ai_extract, 'enabled', lambda: True)
    monkeypatch.setattr(engine.ai_extract, 'propose', lambda text: calls.append(text))  # would also mean "provider failed"
    run = run_sample()  # clean happy-path invoice: every required field read from labels
    assert run['status'] == 'approved'
    assert calls == []
    assert 'not needed' in run['invoice']['ai_status']
    assert po_balance('PO-2026-1048') == 305000


def test_provider_failure_on_an_unreadable_layout_is_held_for_review(monkeypatch):
    monkeypatch.setattr(engine.ai_extract, 'enabled', lambda: True)
    monkeypatch.setattr(engine.ai_extract, 'propose', lambda text: None)
    before = po_balance('PO-2026-1053')
    run = run_sample(6)
    assert run['status'] == 'review'
    assert 'AI reader' in run['result']['summary']
    assert po_balance('PO-2026-1053') == before  # nothing reserved


def test_ai_reader_audits_populated_fields_and_holds_unsupported_proposals(monkeypatch):
    monkeypatch.setenv('GEMINI_CROSSCHECK', '1')  # strict mode: double-read rule-read invoices too
    monkeypatch.setattr(engine.ai_extract, 'enabled', lambda: True)
    monkeypatch.setattr(engine.ai_extract, 'propose', lambda text: {
        'total': {'value': '999999.00', 'quote': 'Total: 4,950.00'}, 'vendor': {'value': 'Someone Else', 'quote': 'x'}})
    run = run_sample()  # clean happy-path invoice: every field already read from labels
    assert run['status'] == 'review'
    assert run['invoice']['total'] == 495000
    assert 'ai_fields' not in run['invoice']


def test_demo_reset_clears_history_and_restores_balances():
    first = run_sample()
    assert first['status'] == 'approved'
    assert po_balance('PO-2026-1048') == 305000
    db.reset_demo()
    assert db.get_run(first['id']) is None
    assert list((db.DATA / 'uploads').glob('*.pdf')) == []
    assert po_balance('PO-2026-1048') == 800000
    again = run_sample()  # same invoice is no longer a duplicate after a reset
    assert again['status'] == 'approved'


def test_demo_reset_refuses_while_an_invoice_is_processing():
    register(b'%PDF-still-processing')
    with pytest.raises(ValueError, match='still processing'):
        db.reset_demo()


def test_restart_marks_interrupted_work_and_preserves_committed_decisions():
    approved = run_sample()
    interrupted = register(b'%PDF-interrupted')
    db.initialize()
    assert db.get_run(approved['id'])['status'] == 'approved'
    assert db.get_run(interrupted)['status'] == 'failed'
    assert 'server restart' in db.get_run(interrupted)['error']
    assert po_balance('PO-2026-1048') == 305000
