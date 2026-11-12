import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
DATA = Path(os.environ.get('LEDGERFLOW_DATA', str(BASE / 'data')))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / 'ledgerflow.db', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def initialize():
    (DATA / 'uploads').mkdir(parents=True, exist_ok=True)
    with connection() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS vendors (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL,
            approved INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pos (
            id TEXT PRIMARY KEY, vendor_id TEXT REFERENCES vendors(id),
            description TEXT NOT NULL, currency TEXT NOT NULL,
            amount INTEGER NOT NULL, opening INTEGER NOT NULL,
            committed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            invoice TEXT, result TEXT, error TEXT, elapsed_ms INTEGER,
            vendor_id TEXT, invoice_key TEXT, po_id TEXT
        );
        CREATE INDEX IF NOT EXISTS run_identity ON runs(vendor_id,invoice_key);
        CREATE INDEX IF NOT EXISTS run_hash ON runs(sha256);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
            stage TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL,
            created_at TEXT NOT NULL, detail TEXT
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
            reviewer TEXT NOT NULL, note TEXT NOT NULL, action TEXT NOT NULL,
            created_at TEXT NOT NULL, original_result TEXT NOT NULL
        );
        ''')
        # Additive migrations keep existing decisions and balances intact.
        for table, column, declaration in [
            ('runs', 'demo_scope', "TEXT NOT NULL DEFAULT ''"),
            ('pos', 'line_items', "TEXT NOT NULL DEFAULT '[]'"),
            ('reviews', 'original_invoice', 'TEXT'),
        ]:
            if column not in {r['name'] for r in con.execute(f'PRAGMA table_info({table})')}:
                con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
        con.execute('''CREATE TABLE IF NOT EXISTS demo_pos (
            scope TEXT NOT NULL, po_id TEXT NOT NULL REFERENCES pos(id),
            committed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,po_id))''')
        for vendor in [
            ('V-1001', 'Northstar Cloud Ltd', ['Northstar Cloud', 'Northstar Cloud Limited'], 1),
            ('V-1002', 'Meridian Design Studio', ['Meridian Design'], 1),
            ('V-1003', 'Atlas Office Supplies', ['Atlas Office'], 1),
            ('V-1004', 'Redwood Services', [], 0),
        ]:
            con.execute('INSERT OR IGNORE INTO vendors VALUES (?,?,?,?)',
                        (vendor[0], vendor[1], json.dumps(vendor[2]), vendor[3]))
        for po in [
            ('PO-2026-1048', 'V-1001', 'Cloud infrastructure · Q4', 'USD', 1200000, 400000, 0, 'open'),
            ('PO-2026-1053', 'V-1001', 'Managed cloud services', 'USD', 1800000, 0, 0, 'open'),
            ('PO-2026-1051', 'V-1002', 'Brand and website design', 'USD', 1000000, 800000, 0, 'open'),
            ('PO-2026-1062', 'V-1003', 'Office equipment · Bengaluru', 'INR', 23600000, 0, 0, 'open'),
        ]:
            con.execute('INSERT OR IGNORE INTO pos(id,vendor_id,description,currency,amount,opening,committed,status) VALUES (?,?,?,?,?,?,?,?)', po)
        interrupted = con.execute("SELECT id FROM runs WHERE status='processing'").fetchall()
        for row in interrupted:
            con.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?",
                        ('Processing was interrupted by a server restart. Upload the document again.', now(), row['id']))
            con.execute('INSERT INTO events(run_id,stage,status,message,created_at) VALUES (?,?,?,?,?)',
                        (row['id'], 'decision', 'failed', 'Server restarted during processing.', now()))


def reset_demo():
    """Clear run history and approvals made in this workspace, restoring PO balances
    to their opening commitments. Vendors and purchase orders are kept."""
    with connection() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute("SELECT COUNT(*) FROM runs WHERE status='processing'").fetchone()[0]:
            raise ValueError('An invoice is still processing. Wait for it to finish, then reset.')
        con.execute('DELETE FROM reviews')
        con.execute('DELETE FROM events')
        con.execute('DELETE FROM runs')
        con.execute('DELETE FROM demo_pos')
        con.execute('UPDATE pos SET committed=0')
    for upload in (DATA / 'uploads').glob('*.pdf'):
        upload.unlink(missing_ok=True)


def event(run_id, stage, status, message, detail=None):
    with connection() as con:
        con.execute('INSERT INTO events(run_id,stage,status,message,created_at,detail) VALUES (?,?,?,?,?,?)',
                    (run_id, stage, status, message, now(), json.dumps(detail) if detail else None))


def get_run(run_id):
    with connection() as con:
        row = con.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ('invoice', 'result'):
            result[key] = json.loads(result[key]) if result[key] else None
        result['events'] = [dict(r) for r in con.execute('SELECT * FROM events WHERE run_id=? ORDER BY id', (run_id,))]
        for e in result['events']:
            e['detail'] = json.loads(e['detail']) if e['detail'] else None
        result['reviews'] = [dict(r) for r in con.execute('SELECT * FROM reviews WHERE run_id=? ORDER BY id', (run_id,))]
        for review in result['reviews']:
            review['original_result'] = json.loads(review['original_result'])
            review['original_invoice'] = json.loads(review['original_invoice']) if review['original_invoice'] else None
        return result


def scoped_pos(con, scope=''):
    rows = [dict(r) for r in con.execute('SELECT * FROM pos')]
    if scope:
        balances = {r['po_id']: r['committed'] for r in con.execute('SELECT * FROM demo_pos WHERE scope=?', (scope,))}
        for row in rows:
            row['committed'] = balances.get(row['id'], 0)
    return rows
