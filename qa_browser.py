"""Exercise the actual browser and HTTP service; no mocked workflow responses."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).parent
ARTIFACTS = BASE / 'artifacts'


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ledgerflow-browser-') as workspace:
        env = dict(os.environ, LEDGERFLOW_DATA=workspace, HOST='127.0.0.1', PORT='8010', LEDGERFLOW_ACCESS_TOKEN='', GEMINI_API_KEY='')
        with open(ARTIFACTS / 'browser-server.log', 'w') as log:
            server = subprocess.Popen([sys.executable, 'app.py'], cwd=BASE, env=env, stdout=log, stderr=log)
            try:
                for _ in range(80):
                    try:
                        urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=1)
                        break
                    except Exception:
                        if server.poll() is not None:
                            raise RuntimeError('Preview service failed: ' + (ARTIFACTS / 'browser-server.log').read_text())
                        time.sleep(.1)
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=os.environ.get('LEDGERFLOW_CHROME','/usr/bin/google-chrome'), headless=True,
                                                 args=['--no-sandbox','--disable-dev-shm-usage'])
                    page = browser.new_page(viewport={'width':1440,'height':1080}, device_scale_factor=1)
                    page.emulate_media(reduced_motion='reduce')
                    errors = []
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.goto('http://127.0.0.1:8010', wait_until='networkidle')
                    page.get_by_text('Your first decision starts here', exact=True).wait_for()
                    page.screenshot(path=str(ARTIFACTS / '01-overview-desktop.png'), full_page=True)
                    print('PASS empty dashboard and local assets', flush=True)

                    def run_sample(sid, expected):
                        rid = page.evaluate("""async (sid) => { const r=await fetch('/api/samples/'+sid+'/run',{method:'POST'});const d=await r.json();location.hash='run/'+d.id; return d.id; }""", sid)
                        page.locator(f'[data-current-run="{rid}"] .decision-card.' + expected).wait_for(timeout=30000)
                        page.locator(f'[data-current-run="{rid}"] .timeline-item.complete').last.wait_for()
                        return rid

                    # First sample uses visible UI controls; later scenarios use the same HTTP intake.
                    page.get_by_role('button', name='No invoice handy? Try a sample').click()
                    page.get_by_role('heading', name='Real PDFs. Thoughtful edge cases.').wait_for()
                    page.locator('[data-sample="01-happy-path"]').click()
                    page.locator('.decision-card.approved').wait_for(timeout=15000)
                    happy_id=page.url.split('/')[-1]
                    page.get_by_role('tab', name='Extracted fields').click()
                    page.get_by_text('NC-2026-084', exact=True).last.wait_for()
                    assert 'Vendor: Northstar Cloud Ltd' in page.locator('.tab-content').inner_text()
                    page.get_by_role('tab', name='Decision checks').click()
                    page.screenshot(path=str(ARTIFACTS / '02-approved-decision.png'), full_page=True)
                    print('PASS sample intake, real approval, source evidence and timeline', flush=True)

                    run_sample('02-reexported-duplicate','rejected')
                    page.get_by_role('link', name='Open original invoice →').wait_for()
                    assert happy_id not in page.get_by_role('link', name='Open original invoice →').get_attribute('href')
                    assert 'Synthetic demo' in page.locator('main').inner_text()
                    print('PASS duplicate identity and original-run link', flush=True)

                    ambiguous_id = run_sample('03-ambiguous-po','review')
                    page.get_by_label('Reviewer name').fill('Demo Reviewer')
                    page.get_by_label('Confirm the purchase order').select_option('PO-2026-1048')
                    page.get_by_label('Decision note').fill('Verified the original PDF and confirmed the cloud infrastructure purchase order.')
                    page.get_by_role('button', name='Verify & approve').click()
                    page.locator('.decision-card.approved').wait_for(timeout=10000)
                    page.get_by_role('tab', name='Audit trail').click()
                    page.get_by_text('Human reviews:', exact=False).wait_for()
                    print('PASS human review, current-balance check and audit', flush=True)

                    run_sample('04-split-billing-overrun','review')
                    assert '450.00' in page.locator('.decision-card').inner_text()
                    page.screenshot(path=str(ARTIFACTS / '03-overrun-review.png'), full_page=True)
                    print('PASS cumulative PO overrun', flush=True)

                    run_sample('05-incomplete-scan','review')
                    assert 'invoice date' in page.locator('.decision-card').inner_text()
                    page.get_by_role('tab', name='Extracted fields').click()
                    assert 'Neural OCR' in page.locator('.source-note').inner_text()
                    print('PASS real neural OCR and missing-date hold', flush=True)

                    run_sample('06-complete-scan','approved')
                    print('PASS real neural OCR approval', flush=True)

                    run_sample('08-inclusive-bundle','approved')
                    page.get_by_role('tab', name='Extracted fields').click()
                    assert 'Derived' in page.locator('.tab-content').inner_text()
                    assert 'Cloud service bundle' in page.locator('.tab-content').inner_text()
                    assert page.locator('iframe[title="Invoice PDF beside extracted evidence"]').count()==1
                    page.screenshot(path=str(ARTIFACTS / '06-inclusive-evidence.png'), full_page=True)
                    run_sample('09-itemised-separate-tax','approved')
                    run_sample('01-happy-path','approved')
                    print('PASS repeatable samples, inclusive tax, bundles and itemised arithmetic', flush=True)

                    # Real uploads use shared authorization and retain duplicate protection.
                    for expected in ['approved','rejected']:
                        page.get_by_role('button', name='New invoice').click()
                        previous_url=page.url
                        page.locator('#file-input').set_input_files(str(BASE / 'samples' / '01-happy-path.pdf'))
                        page.wait_for_url(lambda url: str(url)!=previous_url and '#run/' in str(url))
                        page.locator('.decision-card.'+expected).wait_for(timeout=10000)
                    print('PASS genuine file uploads, duplicate detection and authorization isolation', flush=True)

                    page.get_by_role('link', name='All invoices').click()
                    page.get_by_role('heading', name='Every invoice, accounted for.').wait_for()
                    page.locator('#invoice-search').fill('Meridian')
                    assert page.locator('#table-body tr').count()==1
                    page.locator('#invoice-search').fill('')
                    assert page.locator('#table-body tr').count()==12
                    with page.expect_download() as download:
                        page.get_by_role('link', name='Export CSV').click()
                    csv_path = download.value.path()
                    assert 'MD-2026-217' in Path(csv_path).read_text()
                    page.get_by_role('link', name='Purchase orders', exact=True).click()
                    page.get_by_role('heading', name='The full picture behind the PO.').wait_for()
                    assert '$3,050.00' in page.locator('main').inner_text()
                    print('PASS search, history, CSV and persisted PO balances', flush=True)

                    page.get_by_role('link', name='Overview', exact=True).click()
                    page.get_by_role('heading', name='A little clarity for your day.').wait_for()
                    page.screenshot(path=str(ARTIFACTS / '04-populated-overview.png'), full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(ARTIFACTS / '05-overview-mobile.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.get_by_role('button', name='Toggle navigation').click()
                    page.get_by_role('link', name='Needs review').click()
                    page.get_by_role('heading', name='A human touch, where it matters.').wait_for()
                    assert page.locator('#table-body tr').count()==2
                    assert not page.locator('#sidebar').evaluate("e=>e.classList.contains('open')")
                    assert not errors, errors
                    print('PASS mobile layout, mobile navigation and zero JavaScript errors', flush=True)
                    report = page.request.get(f'http://127.0.0.1:8010/api/runs/{ambiguous_id}/report').json()
                    assert report['reviews'][0]['original_result']['status']=='review'
                    assert report['result']['status']=='approved'
                    events = page.request.get(f'http://127.0.0.1:8010/api/runs/{happy_id}/events').text()
                    assert 'event: stage' in events and 'event: done' in events
                    bad = page.request.post('http://127.0.0.1:8010/api/runs', multipart={'file':{'name':'fake.pdf','mimeType':'application/pdf','buffer':b'not a PDF'}})
                    assert bad.status==422
                    assert page.request.get('http://127.0.0.1:8010/api/runs/unknown').status==404
                    print('PASS immutable review evidence, SSE replay, invalid upload and missing-run errors', flush=True)
                    browser.close()
                    protected_env = dict(env, PORT='8011', LEDGERFLOW_ACCESS_TOKEN='synthetic-acceptance-test-access-code')
                    protected = subprocess.Popen([sys.executable, 'app.py'], cwd=BASE, env=protected_env, stdout=log, stderr=log)
                    try:
                        for _ in range(60):
                            try:
                                urllib.request.urlopen('http://127.0.0.1:8011/api/health', timeout=1)
                                break
                            except Exception:
                                time.sleep(.1)
                        client = p.request.new_context(base_url='http://127.0.0.1:8011')
                        assert client.get('/api/runs').status==401
                        assert client.post('/login',form={'token':'incorrect'}).status==401
                        login = client.post('/login',form={'token':'synthetic-acceptance-test-access-code'},max_redirects=0)
                        assert login.status==303
                        assert 'HttpOnly' in login.headers['set-cookie'] and 'SameSite=strict' in login.headers['set-cookie']
                        assert client.get('/api/runs').status==200
                        assert len(client.get('/api/runs').json())==12
                        assert client.post('/api/samples/01-happy-path/run',headers={'Origin':'https://another-origin.example'}).status==403
                        # TLS terminates at the deployment proxy; forwarded scheme must work.
                        forwarded = client.post('/api/samples/01-happy-path/run',headers={'Origin':'https://127.0.0.1:8011','X-Forwarded-Proto':'https'})
                        assert forwarded.status==202
                        client.dispose()
                        print('PASS access gate, login cookies, restart persistence, origin checks and HTTPS proxy headers', flush=True)
                    finally:
                        protected.terminate()
                        protected.wait(timeout=5)
                    (ARTIFACTS / 'browser-check.json').write_text(json.dumps({'passed':True,'javascript_errors':errors,'scenarios':12},indent=2))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()


if __name__=='__main__':
    main()
