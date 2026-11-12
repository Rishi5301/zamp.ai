import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import ai_extract
import db
from engine import process, commit_decision, POLICY
import finance
from fixtures import SAMPLES, ROOT as SAMPLES_ROOT, ensure_samples

BASE = Path(__file__).parent
EXECUTOR = None
MAX_BYTES = 10 * 1024 * 1024
ACCESS_TOKEN = os.environ.get('LEDGERFLOW_ACCESS_TOKEN', '')
COOKIE_VALUE = hmac.new(ACCESS_TOKEN.encode(), b'ledgerflow-session-v1', hashlib.sha256).hexdigest() if ACCESS_TOKEN else ''


@asynccontextmanager
async def lifespan(app):
    global EXECUTOR
    finance.settings()  # Fail early on invalid policy configuration.
    db.initialize()
    ensure_samples()
    EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='invoice')
    yield
    EXECUTOR.shutdown(wait=True)


app = FastAPI(title='Ledgerflow', description='Invoice processing with evidence and deterministic decisions.', lifespan=lifespan)


@app.middleware('http')
async def access_control(request: Request, call_next):
    path = request.url.path
    if ACCESS_TOKEN and path not in ('/login', '/api/health'):
        if not secrets.compare_digest(request.cookies.get('ledgerflow_session', ''), COOKIE_VALUE):
            if path.startswith('/api/'):
                return JSONResponse({'detail': 'Sign in to continue.'}, status_code=401)
            return RedirectResponse('/login', status_code=303)
    if request.method in ('POST', 'DELETE', 'PUT', 'PATCH'):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Cross-origin changes are not allowed.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    if path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/login', response_class=HTMLResponse)
def login_page():
    return '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Ledgerflow</title><style>body{font:16px system-ui;background:#f5f6f2;color:#183d32;display:grid;place-items:center;min-height:95vh}form{width:300px;background:white;padding:40px;border:1px solid #ddd;border-radius:18px}input,button{box-sizing:border-box;width:100%;padding:14px;margin-top:16px;border:1px solid #ccc;border-radius:8px}button{background:#163f33;color:white;cursor:pointer}</style><form method="post"><h1>Ledgerflow.</h1><p>Sign in to your AP workspace.</p><label for="token">Workspace access code</label><input id="token" name="token" type="password" required autocomplete="current-password"><button>Open workspace</button></form></html>'''


@app.post('/login')
async def login(request: Request):
    form = await request.form()
    if not ACCESS_TOKEN or not secrets.compare_digest(str(form.get('token', '')), ACCESS_TOKEN):
        return HTMLResponse('Access code is incorrect. <a href="/login">Try again</a>.', status_code=401)
    response = RedirectResponse('/', status_code=303)
    response.set_cookie('ledgerflow_session', COOKIE_VALUE, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', max_age=86400)
    return response


@app.get('/api/health')
def health():
    return {'status': 'ok', 'policy_version': POLICY}


@app.get('/')
def index():
    return FileResponse(BASE / 'static' / 'index.html')


@app.get('/api/samples')
def samples():
    primary = ['01-happy-path', '02-reexported-duplicate', '04-split-billing-overrun', '08-inclusive-bundle', '03-ambiguous-po']
    ordered = sorted(SAMPLES, key=lambda s: primary.index(s['id']) if s['id'] in primary else len(primary))

    def expected(sample):
        # The unfamiliar layout can only be approved when the AI reader is configured.
        if sample.get('unstructured'):
            return 'approved' if ai_extract.enabled() else 'review'
        return sample['expected']
    return [{**{k: s[k] for k in ('id', 'title', 'category', 'description')}, 'expected': expected(s)} for s in ordered]


def create_run(content, filename, demo_scope='', queued=True):
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(413, 'Upload a PDF smaller than 10 MB.')
    if not content.startswith(b'%PDF-'):
        raise HTTPException(422, 'This file is not a PDF. Upload a genuine PDF document.')
    # Admission control keeps a public demo from building an unbounded OCR queue.
    with db.connection() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute("SELECT COUNT(*) FROM runs WHERE status='processing'").fetchone()[0] >= 8:
            raise HTTPException(429, 'The processing queue is full. Please retry shortly.')
        run_id = str(uuid.uuid4())
        safe_name = Path(filename or 'invoice.pdf').name[:180]
        con.execute('INSERT INTO runs(id,filename,sha256,status,created_at,updated_at,demo_scope) VALUES (?,?,?,?,?,?,?)',
                    (run_id, safe_name, hashlib.sha256(content).hexdigest(), 'processing', db.now(), db.now(), demo_scope))
    try:
        (db.DATA / 'uploads' / f'{run_id}.pdf').write_bytes(content)
        if queued:
            EXECUTOR.submit(process, run_id)
        else:
            process(run_id)
    except Exception:
        with db.connection() as con:
            con.execute("UPDATE runs SET status='failed',error=? WHERE id=?", ('Could not queue this document. Try again.', run_id))
        raise HTTPException(500, 'Could not store or queue the PDF.')
    return {'id': run_id}


@app.post('/api/runs', status_code=202)
async def upload(file: UploadFile = File(...)):
    content = await file.read(MAX_BYTES + 1)
    return create_run(content, file.filename)


@app.post('/api/samples/{sample_id}/run', status_code=202)
def run_sample(sample_id: str):
    if sample_id not in {s['id'] for s in SAMPLES}:
        raise HTTPException(404, 'Sample not found.')
    # Each exploration owns an isolated authorization/duplicate namespace.
    # The duplicate story seeds its original through the real processing engine.
    scope = str(uuid.uuid4())
    if sample_id == '02-reexported-duplicate':
        original = create_run((SAMPLES_ROOT / '01-happy-path.pdf').read_bytes(), '01-happy-path.pdf', scope, queued=False)
        if db.get_run(original['id'])['status'] != 'approved':
            raise HTTPException(409, 'The duplicate demo baseline needs review. Inspect the original run before retrying.')
    return create_run((SAMPLES_ROOT / f'{sample_id}.pdf').read_bytes(), f'{sample_id}.pdf', scope)


@app.get('/api/samples/{sample_id}/document')
def sample_document(sample_id: str):
    if sample_id not in {s['id'] for s in SAMPLES}:
        raise HTTPException(404, 'Sample not found.')
    return FileResponse(SAMPLES_ROOT / f'{sample_id}.pdf', media_type='application/pdf', filename=f'{sample_id}.pdf')


@app.get('/api/runs')
def list_runs():
    with db.connection() as con:
        rows = con.execute('SELECT id,filename,status,created_at,updated_at,invoice,result,elapsed_ms,error,demo_scope FROM runs ORDER BY created_at DESC LIMIT 1000').fetchall()
    out = []
    for row in rows:
        r = dict(row)
        invoice = json.loads(r.pop('invoice') or '{}')
        decision = json.loads(r.pop('result') or '{}')
        r.update(vendor=invoice.get('vendor'), invoice_number=invoice.get('invoice_number'),
                 total=invoice.get('total'), currency=invoice.get('currency'),
                 po=decision.get('po', {}).get('id') if decision.get('po') else None,
                 duplicate=bool(decision.get('duplicate_run_id')), reason=decision.get('summary') or r.get('error'),
                 demo=bool(r.pop('demo_scope')))
        out.append(r)
    return out


@app.get('/api/summary')
def summary():
    with db.connection() as con:
        counts = {r['status']: r['n'] for r in con.execute('SELECT status,COUNT(*) n FROM runs GROUP BY status')}
        total = con.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        avg = con.execute('SELECT AVG(elapsed_ms) FROM runs WHERE elapsed_ms IS NOT NULL').fetchone()[0]
        duplicates = con.execute("SELECT COUNT(*) FROM runs WHERE json_extract(result,'$.duplicate_run_id') IS NOT NULL").fetchone()[0]
    return {'counts': counts, 'total': total, 'average_ms': round(avg) if avg else None, 'duplicates': duplicates}


@app.get('/api/procurement')
def procurement():
    with db.connection() as con:
        pos = [dict(r) for r in con.execute('SELECT p.*,v.name vendor FROM pos p JOIN vendors v ON v.id=p.vendor_id ORDER BY p.id')]
        vendors = [dict(r) for r in con.execute('SELECT id,name,approved FROM vendors')]
    for po in pos:
        po['remaining'] = po['amount'] - po['opening'] - po['committed']
    return {'pos': pos, 'vendors': vendors}


@app.get('/api/policy')
def policy():
    return {'version': POLICY, **finance.settings()}


def require_run(run_id):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, 'Run not found.')
    return run


@app.get('/api/runs/{run_id}')
def read_run(run_id: str):
    return require_run(run_id)


@app.get('/api/runs/{run_id}/events')
async def events(run_id: str, request: Request):
    require_run(run_id)
    try:
        last_id = max(0, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        last_id = 0

    async def stream():
        cursor = last_id
        while not await request.is_disconnected():
            run = await asyncio.to_thread(require_run, run_id)
            for e in run['events']:
                if e['id'] > cursor:
                    yield f"id: {e['id']}\nevent: stage\ndata: {json.dumps(e)}\n\n"
                    cursor = e['id']
            # A committed result is terminal even if a later event write failed.
            if run['status'] != 'processing':
                yield f"event: done\ndata: {json.dumps({'status': run['status']})}\n\n"
                break
            yield ': heartbeat\n\n'
            await asyncio.sleep(.25)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


@app.get('/api/runs/{run_id}/document')
def document(run_id: str):
    run = require_run(run_id)
    return FileResponse(db.DATA / 'uploads' / f'{run_id}.pdf', media_type='application/pdf',
                        filename=run['filename'], content_disposition_type='inline')


@app.get('/api/runs/{run_id}/report')
def report(run_id: str):
    run = require_run(run_id)
    return JSONResponse(run, headers={'Content-Disposition': f'attachment; filename="decision-{run_id[:8]}.json"'})


@app.get('/api/runs/{run_id}/follow-up')
def follow_up(run_id: str):
    run = require_run(run_id)
    if not run.get('result'):
        raise HTTPException(409, 'The decision is not available yet.')
    invoice, result = run['invoice'], run['result']
    text = f"Subject: Information needed for invoice {invoice.get('invoice_number', run['filename'])}\n\nHello {invoice.get('vendor', 'vendor')},\n\nWe reviewed your invoice and need the following resolved before AP can process it:\n\n"
    text += '\n'.join('- ' + c['detail'] for c in result['checks'] if c['status'] != 'pass')
    text += '\n\nPlease send a corrected invoice or supporting information. No payment has been initiated.\n\nAccounts Payable\n\n[DRAFT — review before sending]'
    return StreamingResponse(iter([text]), media_type='text/plain', headers={'Content-Disposition': f'attachment; filename="follow-up-{run_id[:8]}.txt"'})


class Review(BaseModel):
    reviewer: str = Field(min_length=2, max_length=80)
    note: str = Field(min_length=10, max_length=2000)
    action: str
    po_id: str | None = None
    corrections: dict[str, str] = Field(default_factory=dict)


@app.post('/api/runs/{run_id}/review')
def review(run_id: str, body: Review):
    if body.action not in ('approve', 'reject', 'correct'):
        raise HTTPException(422, 'Choose approve, reject, or save corrections.')
    if not body.reviewer.strip() or len(body.note.strip()) < 10:
        raise HTTPException(422, 'Enter a reviewer name and a meaningful review note of at least 10 characters.')
    run = require_run(run_id)
    try:
        commit_decision(run_id, run['invoice'], reviewer=body.reviewer.strip(), note=body.note.strip(),
                        selected_po=body.po_id, reject=body.action == 'reject', corrections=body.corrections,
                        save_only=body.action == 'correct')
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.event(run_id, 'decision', 'complete', f"Human review: {body.action} by {body.reviewer.strip()}. Note: {body.note.strip()}")
    return require_run(run_id)


@app.get('/api/export.csv')
def export_csv():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Run ID', 'Filename', 'Vendor', 'Invoice', 'Currency', 'Total', 'Status', 'PO', 'Created UTC'])
    for r in list_runs():
        def safe(s):
            s = str(s or '')
            return "'" + s if s.lstrip().startswith(('=', '+', '-', '@')) else s
        writer.writerow([safe(r['id']), safe(r['filename']), safe(r['vendor']), safe(r['invoice_number']),
                         safe(r['currency']), f"{r['total']/100:.2f}" if r['total'] is not None else '',
                         r['status'], safe(r['po']), r['created_at']])
    return StreamingResponse(iter([out.getvalue()]), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="ledgerflow-runs.csv"'})


app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')),
                proxy_headers=True, forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS', '127.0.0.1'), workers=1)
