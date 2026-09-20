"""Account-owned editorial evidence bound to immutable published charts."""
import json
import time
import uuid
from datetime import datetime, timezone

from pydantic import Field

from beatforge.connector_artifacts import Artifacts
from beatforge.connector_store import ConnectorStore
from beatforge.editorial_models import FeedbackRequest, ComparisonRequest, PresetRequest, SuggestionsRequest
from beatforge.feedback_guidance import derive_feedback_adjustment
from beatforge.mapping_plan import normalize_mapping_plan


class ConnectorFeedback(FeedbackRequest):
    idempotencyKey: str = Field(min_length=1, max_length=120)


class ConnectorComparison(ComparisonRequest):
    idempotencyKey: str = Field(min_length=1, max_length=120)


class Editorial:
    def __init__(self, store: ConnectorStore, artifacts: Artifacts):
        self.store, self.artifacts = store, artifacts
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS editorial (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                request_key TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                UNIQUE(owner,kind,request_key))''')

    def save(self, owner, kind, key, payload, *, replace=False):
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM editorial WHERE owner=? AND kind=? AND request_key=?', (owner, kind, key)).fetchone()
            if old:
                if not replace and old['payload'] != encoded:
                    raise ValueError('Idempotency key belongs to different feedback')
                if replace:
                    db.execute('UPDATE editorial SET payload=? WHERE id=?', (encoded, old['id']))
                return {'id': old['id'], **json.loads(encoded if replace else old['payload']), 'createdAt': old['created']}
            count = db.execute('SELECT COUNT(*) FROM editorial WHERE owner=?', (owner,)).fetchone()[0]
            if count >= 1000:
                raise ValueError('Account editorial record limit reached')
            record_id, now = uuid.uuid4().hex, time.time()
            db.execute('INSERT INTO editorial VALUES(?,?,?,?,?,?)', (record_id, owner, kind, key, encoded, now))
        return {'id': record_id, **payload, 'createdAt': now}

    def records(self, owner, kind):
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM editorial WHERE owner=? AND kind=? ORDER BY created DESC,id DESC', (owner, kind)).fetchall()
        return [{'id': row['id'], **json.loads(row['payload']), 'createdAt': row['created']} for row in rows]

    def preview(self, owner, job_id, difficulty):
        if difficulty not in {'Easy', 'Normal', 'Hard', 'Expert', 'ExpertPlus'}:
            raise ValueError('Unknown difficulty')
        payload = self.artifacts.read_json(owner, job_id, f'preview-{difficulty}.json')
        if payload.get('difficulty') != difficulty or not payload.get('chartHash') or not payload.get('audioHash'):
            raise ValueError('Preview identity is invalid')
        return payload

    def feedback(self, owner: str, request: ConnectorFeedback):
        preview = self.preview(owner, request.jobId, request.difficulty)
        payload = request.model_dump(exclude={'idempotencyKey'}, exclude_none=True)
        payload.update(chartHash=preview['chartHash'], audioHash=preview['audioHash'],
                       previewSha256=self.artifacts.named(owner, request.jobId, f'preview-{request.difficulty}.json')['sha256'],
                       source='human', schemaVersion=1)
        return self.save(owner, 'feedback', request.idempotencyKey, payload)

    def compare(self, owner: str, request: ConnectorComparison):
        if request.preferredJob == request.alternateJob:
            raise ValueError('Choose two different maps')
        a = self.preview(owner, request.preferredJob, request.difficulty)
        b = self.preview(owner, request.alternateJob, request.difficulty)
        if any(a.get(key) != b.get(key) for key in ('audioHash', 'bpm', 'offsetSeconds')):
            raise ValueError('Compare the same audio, timing and difficulty')
        if a['chart'] == b['chart']:
            raise ValueError('The selected charts are identical')
        payload = request.model_dump(exclude={'idempotencyKey'})
        payload.update(preferredHash=a['chartHash'], alternateHash=b['chartHash'], audioHash=a['audioHash'])
        return self.save(owner, 'comparison', request.idempotencyKey, payload)

    def preset(self, owner: str, request: PresetRequest):
        payload = {'name': request.name, 'mappingPlan': normalize_mapping_plan(request.mappingPlan)}
        return self.save(owner, 'preset', request.name.casefold(), payload, replace=True)

    def suggestions(self, owner: str, request: SuggestionsRequest):
        records = [{**row, 'createdAt': datetime.fromtimestamp(row['createdAt'], timezone.utc).isoformat()}
                   for row in self.records(owner, 'feedback')]
        return derive_feedback_adjustment(records, request.mappingPlan, tester=request.tester, difficulty=request.difficulty)
