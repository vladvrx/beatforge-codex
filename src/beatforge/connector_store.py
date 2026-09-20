"""Durable, owner-scoped connector jobs with expiring worker leases."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager


class ConnectorStore:
    BLOB_BUDGET = 512 * 1024 * 1024
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, lease TEXT, lease_until REAL,
                result TEXT, created REAL NOT NULL, updated REAL NOT NULL,
                UNIQUE(owner, request_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT NOT NULL,
                size INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                sha256 TEXT, expires REAL NOT NULL)''')
            db.execute('CREATE TABLE IF NOT EXISTS workers (owner TEXT PRIMARY KEY, seen REAL NOT NULL)')

    def worker_seen(self, owner: str):
        with self.connect() as db:
            db.execute('INSERT INTO workers(owner,seen) VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET seen=excluded.seen', (owner, time.time()))

    def worker_online(self, owner: str) -> bool:
        with self.connect() as db:
            row = db.execute('SELECT seen FROM workers WHERE owner=?', (owner,)).fetchone()
        return bool(row and row['seen'] > time.time() - 90)

    def check_lease(self, owner: str, job_id: str, token: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND lease=? AND lease_until>? AND state='running'", (job_id, owner, token, time.time())).fetchone()
        if row is None:
            raise ValueError('Lease expired, revoked, or replaced')
        return self.public(row)

    def reserve_upload(self, owner: str, filename: str, size: int) -> dict:
        if not 1 <= size <= 64 * 1024 * 1024:
            raise ValueError('Audio must be between 1 byte and 64 MiB')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Pending reservations have no committed bytes. Serialize expiry with
            # begin_upload so cleanup can never race an active or completed writer.
            db.execute("DELETE FROM uploads WHERE state='pending' AND expires<=?", (time.time(),))
            usage = db.execute('SELECT COUNT(*) AS count, COALESCE(SUM(size),0) AS bytes FROM uploads WHERE owner=?', (owner,)).fetchone()
            if usage['count'] >= 32 or usage['bytes'] + size > 256 * 1024 * 1024:
                raise ValueError('Account upload quota reached')
            self.check_blob_budget(db, size)
            upload_id, expires = uuid.uuid4().hex, time.time() + 3600
            db.execute('INSERT INTO uploads(id,owner,filename,size,expires) VALUES(?,?,?,?,?)', (upload_id, owner, filename, size, expires))
        return self.get_upload(owner, upload_id)

    def get_upload(self, owner: str, upload_id: str) -> dict:
        with self.connect() as db:
            row = db.execute('SELECT * FROM uploads WHERE id=? AND owner=?', (upload_id, owner)).fetchone()
        if row is None:
            raise KeyError('Upload not found')
        return {key: row[key] for key in ('id', 'filename', 'size', 'state', 'sha256', 'expires')}

    def begin_upload(self, owner: str, upload_id: str) -> dict:
        with self.connect() as db:
            changed = db.execute("UPDATE uploads SET state='writing' WHERE id=? AND owner=? AND state='pending' AND expires>?", (upload_id, owner, time.time())).rowcount
            if not changed:
                raise ValueError('Upload expired, already written, or unavailable')
        return self.get_upload(owner, upload_id)

    def complete_upload(self, owner: str, upload_id: str, digest: str):
        with self.connect() as db:
            changed = db.execute("UPDATE uploads SET state='ready',sha256=? WHERE id=? AND owner=? AND state='writing' AND expires>?", (digest, upload_id, owner, time.time())).rowcount
            if not changed:
                raise ValueError('Upload expired or unavailable')

    def abort_upload(self, owner: str, upload_id: str):
        with self.connect() as db:
            db.execute("UPDATE uploads SET state='pending' WHERE id=? AND owner=? AND state='writing'", (upload_id, owner))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            db.execute(f'PRAGMA max_page_count={64 * 1024 * 1024 // page_size}')
            with db:
                yield db
        finally:
            db.close()

    def check_blob_budget(self, db, additional: int):
        """Called inside the same write transaction as the reservation."""
        usage = db.execute('SELECT COALESCE(SUM(size),0) FROM uploads').fetchone()[0]
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone():
            usage += db.execute('SELECT COALESCE(SUM(size),0) FROM artifacts').fetchone()[0]
        if usage + additional > self.BLOB_BUDGET:
            raise ValueError('Gateway storage capacity reached. Export data and contact the operator.')

    def submit(self, owner: str, request_key: str, payload: dict, *, max_active: int = 2) -> dict:
        if not owner or not request_key:
            raise ValueError('Owner and idempotency key are required')
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM jobs WHERE owner=? AND request_key=?', (owner, request_key)).fetchone()
            if previous:
                if previous['payload'] != encoded:
                    raise ValueError('Idempotency key already belongs to a different request')
                return self.public(previous)
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND state IN ('queued','running')", (owner,)).fetchone()[0]
            if active >= max_active:
                raise ValueError('Account active-job limit reached')
            job_id = uuid.uuid4().hex
            db.execute('INSERT INTO jobs(id,owner,request_key,payload,created,updated) VALUES(?,?,?,?,?,?)',
                       (job_id, owner, request_key, encoded, now, now))
        return self.get(owner, job_id)

    @staticmethod
    def public(row) -> dict:
        return {'id': row['id'], 'state': row['state'], 'attempts': row['attempts'],
                'request': json.loads(row['payload']), 'result': json.loads(row['result']) if row['result'] else None,
                'createdAt': row['created'], 'updatedAt': row['updated']}

    def get(self, owner: str, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (job_id, owner)).fetchone()
        if row is None:
            raise KeyError('Job not found')
        return self.public(row)

    def list_jobs(self, owner: str, limit: int = 20, offset: int = 0) -> dict:
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise ValueError('Use a limit of 1 to 100 and a nonnegative offset up to 100000')
        with self.connect() as db:
            rows = db.execute('SELECT * FROM jobs WHERE owner=? ORDER BY created DESC,id DESC LIMIT ? OFFSET ?', (owner, limit, offset)).fetchall()
            total = db.execute('SELECT COUNT(*) FROM jobs WHERE owner=?', (owner,)).fetchone()[0]
        return {'jobs': [self.public(row) for row in rows], 'total': total, 'offset': offset}

    def cancel(self, owner: str, job_id: str) -> dict:
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET state='cancelled',lease=NULL,lease_until=NULL,updated=? WHERE id=? AND owner=? AND state IN ('queued','running')",
                                 (time.time(), job_id, owner)).rowcount
        result = self.get(owner, job_id)
        if not changed and result['state'] != 'cancelled':
            raise ValueError('Completed jobs cannot be cancelled')
        return result

    def claim(self, owner: str, *, lease_seconds: int = 60, max_attempts: int = 3) -> dict | None:
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError('Lease duration and attempt limit must be positive')
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET state='failed',lease=NULL,lease_until=NULL,updated=? WHERE owner=? AND state='running' AND lease_until<=? AND attempts>=?", (now, owner, now, max_attempts))
            row = db.execute("SELECT * FROM jobs WHERE owner=? AND attempts<? AND (state='queued' OR (state='running' AND lease_until<=?)) ORDER BY created,id LIMIT 1", (owner, max_attempts, now)).fetchone()
            if row is None:
                return None
            token = secrets.token_urlsafe(32)
            db.execute("UPDATE jobs SET state='running',attempts=attempts+1,lease=?,lease_until=?,updated=? WHERE id=?", (token, now + lease_seconds, now, row['id']))
            updated = db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()
            return {**self.public(updated), 'leaseToken': token, 'leaseExpiresAt': now + lease_seconds}

    def heartbeat(self, owner: str, job_id: str, token: str, *, lease_seconds: int = 60):
        if lease_seconds < 1:
            raise ValueError('Lease duration must be positive')
        now = time.time()
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET lease_until=?,updated=? WHERE id=? AND owner=? AND lease=? AND lease_until>? AND state='running'", (now + lease_seconds, now, job_id, owner, token, now)).rowcount
            if not changed:
                raise ValueError('Lease expired, revoked, or replaced')

    def finish(self, owner: str, job_id: str, token: str, result: dict, *, failed: bool = False):
        encoded = json.dumps(result, allow_nan=False)
        now = time.time()
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET state=?,result=?,lease=NULL,lease_until=NULL,updated=? WHERE id=? AND owner=? AND lease=? AND lease_until>? AND state='running'",
                                 ('failed' if failed else 'completed', encoded, now, job_id, owner, token, now)).rowcount
            if not changed:
                raise ValueError('Lease expired, revoked, or replaced')
        return self.get(owner, job_id)
