"""Lease-bound, immutable worker outputs with account storage limits."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
import secrets
import jwt
from pathlib import Path

from fastapi import HTTPException, Request

from beatforge.connector_store import ConnectorStore

ARTIFACT_TYPES = {'map.zip': 'application/zip', 'audio.ogg': 'audio/ogg', 'qa.json': 'application/json',
                  **{f'preview-{name}.json': 'application/json' for name in ('Easy', 'Normal', 'Hard', 'Expert', 'ExpertPlus')}}


class Artifacts:
    def __init__(self, store: ConnectorStore, root: Path):
        self.store, self.root = store, root.resolve()
        # Short-lived links intentionally expire on restart as well as by time.
        self.link_key = secrets.token_bytes(32)
        self.root.mkdir(parents=True, exist_ok=True)
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, job TEXT NOT NULL,
                lease TEXT NOT NULL, name TEXT NOT NULL, size INTEGER NOT NULL,
                sha256 TEXT, ready INTEGER NOT NULL DEFAULT 0,
                UNIQUE(job,lease,name))''')

    def path(self, artifact_id: str) -> Path:
        if len(artifact_id) != 32 or any(c not in '0123456789abcdef' for c in artifact_id):
            raise KeyError('Artifact not found')
        path = (self.root / artifact_id).resolve()
        if path.parent != self.root:
            raise KeyError('Artifact not found')
        return path

    async def receive(self, owner: str, job_id: str, lease: str, name: str, request: Request) -> dict:
        if name not in ARTIFACT_TYPES:
            raise ValueError('Unsupported artifact')
        maximum = 16*1024*1024 if name.endswith('.json') else 64*1024*1024
        length = request.headers.get('content-length', '')
        if not length.isdecimal() or not 1 <= int(length) <= maximum:
            raise HTTPException(413, 'A bounded Content-Length is required')
        size = int(length)
        self.store.check_lease(owner, job_id, lease)
        artifact_id = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM artifacts WHERE job=? AND lease=? AND name=?', (job_id, lease, name)).fetchone():
                raise ValueError('Artifact already uploaded for this lease')
            usage = db.execute('SELECT COALESCE(SUM(size),0) FROM artifacts WHERE owner=?', (owner,)).fetchone()[0]
            if usage + size > 256*1024*1024:
                raise ValueError('Account artifact quota reached')
            self.store.check_blob_budget(db, size)
            db.execute('INSERT INTO artifacts(id,owner,job,lease,name,size) VALUES(?,?,?,?,?,?)', (artifact_id, owner, job_id, lease, name, size))
        path = self.path(artifact_id)
        try:
            digest, received = hashlib.sha256(), 0
            with path.open('xb') as stream:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > size:
                        raise HTTPException(413, 'Artifact exceeds its reserved size')
                    stream.write(chunk)
                    digest.update(chunk)
            if received != size:
                raise HTTPException(422, 'Artifact is incomplete')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                valid = db.execute("SELECT id FROM jobs WHERE id=? AND owner=? AND lease=? AND lease_until>? AND state='running'", (job_id, owner, lease, time.time())).fetchone()
                if not valid:
                    raise ValueError('Lease expired, revoked, or replaced')
                db.execute('UPDATE artifacts SET ready=1,sha256=? WHERE id=?', (digest.hexdigest(), artifact_id))
        except BaseException:
            path.unlink(missing_ok=True)
            with self.store.connect() as db:
                db.execute('DELETE FROM artifacts WHERE id=?', (artifact_id,))
            raise
        return {'name': name, 'size': size, 'sha256': digest.hexdigest()}

    def manifest(self, owner: str, job_id: str, lease: str) -> list[dict]:
        with self.store.connect() as db:
            rows = db.execute('SELECT id,name,size,sha256 FROM artifacts WHERE owner=? AND job=? AND lease=? AND ready=1 ORDER BY name', (owner, job_id, lease)).fetchall()
        return [dict(row) for row in rows]

    def published(self, owner: str, job_id: str) -> list[dict]:
        job = self.store.get(owner, job_id)
        return (job.get('result') or {}).get('artifacts', []) if job['state'] == 'completed' else []

    def named(self, owner: str, job_id: str, name: str) -> dict:
        entry = next((item for item in self.published(owner, job_id) if item['name'] == name), None)
        if not entry:
            raise KeyError('Artifact not found')
        return entry

    def read_json(self, owner: str, job_id: str, name: str) -> dict:
        entry = self.named(owner, job_id, name)
        if not name.endswith('.json') or entry['size'] > 16*1024*1024:
            raise ValueError('Not a readable JSON artifact')
        content = self.path(entry['id']).read_bytes()
        if len(content) != entry['size'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise ValueError('Artifact integrity check failed')
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError('Artifact must contain an object')
        return result

    def download(self, owner: str, job_id: str, artifact_id: str):
        entry = next((item for item in self.published(owner, job_id) if item['id'] == artifact_id), None)
        if not entry or not self.path(artifact_id).is_file():
            raise KeyError('Artifact not found')
        return self.path(artifact_id), entry['name'], ARTIFACT_TYPES[entry['name']]

    def link(self, owner: str, job_id: str, artifact_id: str) -> dict:
        self.download(owner, job_id, artifact_id)
        expires = int(time.time()) + 300
        token = jwt.encode({'sub': owner, 'job': job_id, 'artifact': artifact_id, 'exp': expires,
                            'aud': 'beatforge-artifact'}, self.link_key, algorithm='HS256')
        return {'downloadUrl': f'/downloads/{artifact_id}?ticket={token}', 'expiresAt': expires}

    def signed_download(self, artifact_id: str, ticket: str):
        try:
            if len(ticket) > 4096:
                raise jwt.InvalidTokenError('Oversized ticket')
            claims = jwt.decode(ticket, self.link_key, algorithms=['HS256'], audience='beatforge-artifact',
                                options={'require': ['sub', 'job', 'artifact', 'exp']})
            if claims['artifact'] != artifact_id:
                raise jwt.InvalidTokenError('Artifact mismatch')
            return self.download(claims['sub'], claims['job'], artifact_id)
        except jwt.PyJWTError as error:
            raise HTTPException(401, 'Download link expired or invalid') from error
