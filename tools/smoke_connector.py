"""Exercise real local generation through the connector with repository synthetic audio."""
import json
import os
import sqlite3
import secrets
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from beatforge.connector import Principal, create_app
from beatforge.connector_worker import run_once
from beatforge.premium import corpus_database

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT/'data/connector-smoke'/uuid.uuid4().hex
    output.mkdir(parents=True)
    source = corpus_database()
    target = output/'official-corpus.sqlite3'
    if source.is_file():
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as original, sqlite3.connect(target) as copy:
            original.backup(copy)
    os.environ['BEATFORGE_CORPUS_DB'] = str(target)
    account_token, worker_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    app = create_app(database=output/'jobs.db', hosts=['testserver'], credentials={
        account_token: Principal('synthetic-smoke', frozenset({'read', 'generate', 'edit'})),
        worker_token: Principal('synthetic-smoke', frozenset({'worker'}))})
    started = time.monotonic()
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer '+account_token
        audio = (ROOT/'web/assets/demo/song.ogg').read_bytes()
        response = client.post('/api/uploads', json={'filename': 'synthetic.ogg', 'size': len(audio)})
        response.raise_for_status()
        upload = response.json()
        client.put(upload['uploadPath'], content=audio).raise_for_status()
        response = client.post('/api/jobs', json={'uploadId': upload['id'], 'title': 'Connector synthetic smoke',
            'artist': 'BeatForge synthetic score', 'idempotencyKey': 'smoke', 'difficulties': ['Hard'],
            'mappingPlan': {'brief': 'flow, lighter verses, no bombs'}, 'allowUnconfirmed': True})
        response.raise_for_status()
        job_id = response.json()['id']
        print(f'Running real synthetic generation: {job_id}', flush=True)
        client.headers['Authorization'] = 'Bearer '+worker_token
        run_once(client, output/'worker')
        client.headers['Authorization'] = 'Bearer '+account_token
        job = client.get('/api/jobs/'+job_id).json()
        artifacts = client.get(f'/api/jobs/{job_id}/artifacts').json()['artifacts']
        downloads = []
        for item in artifacts:
            download = client.get(item['downloadUrl'], headers={'Authorization': ''})
            download.raise_for_status()
            assert len(download.content) == item['size']
            downloads.append(item['name'])
        report = {'jobId': job_id, 'state': job['state'], 'result': job['result'], 'downloadsVerified': downloads,
                  'elapsedSeconds': round(time.monotonic()-started, 2), 'transport': 'in-process ASGI',
                  'liveClientVerified': False, 'humanPlaytestVerified': False}
        (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(output/'report.json'), 'status': job['result']['status'],
                          'downloads': downloads, 'seconds': report['elapsedSeconds']}), flush=True)
        if job['result']['status'] != 'playtest_candidate':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
