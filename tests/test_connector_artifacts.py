import json
import zipfile
from pathlib import Path

from beatforge.connector_export import prepare_artifacts
from beatforge.connector_worker import run_once
from test_connector_worker import setup_job

ROOT = Path(__file__).resolve().parents[1]


def test_stalled_artifact_releases_quota_and_can_retry(tmp_path, monkeypatch):
    import asyncio
    import pytest
    from fastapi import HTTPException, Request
    from beatforge.connector_artifacts import Artifacts
    from beatforge.connector_store import ConnectorStore
    store = ConnectorStore(tmp_path/'jobs.db')
    artifacts = Artifacts(store, tmp_path/'artifacts')
    store.submit('alice', 'test', {})
    job = store.claim('alice')
    monkeypatch.setattr('beatforge.connector_artifacts.ARTIFACT_TIMEOUT_SECONDS', 0.02)
    async def exercise():
        delivered = False
        async def stalled():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':b'ab','more_body':True}
            await asyncio.sleep(10)
        scope = {'type':'http','headers':[(b'content-length',b'4')]}
        with pytest.raises(HTTPException) as failure:
            await artifacts.receive('alice',job['id'],job['leaseToken'],'audio.ogg',Request(scope,stalled))
        assert failure.value.status_code == 408
        assert not list(artifacts.root.iterdir())
        with store.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] == 0
        async def retry(): return {'type':'http.request','body':b'data','more_body':False}
        await artifacts.receive('alice',job['id'],job['leaseToken'],'audio.ogg',Request(scope,retry))
        assert len(artifacts.manifest('alice',job['id'],job['leaseToken'])) == 1
    asyncio.run(exercise())


def sample_map(destination):
    # Repository-owned synthetic fixture, not an uploaded archive.
    with zipfile.ZipFile(ROOT/'web/assets/demo/map.zip') as archive:
        archive.extractall(destination)


def test_export_excludes_private_pipeline_files(tmp_path):
    folder = tmp_path/'map'
    sample_map(folder)
    (folder/'private-corpus.txt').write_text('private corpus')
    outputs = prepare_artifacts(folder, tmp_path/'publish', {'brief': 'test'})
    with zipfile.ZipFile(outputs['map.zip']) as archive:
        assert 'Info.dat' in archive.namelist()
        assert all(not name.startswith('_beatforge/') for name in archive.namelist())
        assert 'private-corpus.txt' not in archive.namelist()
    preview = json.loads(outputs['preview-Hard.json'].read_text())
    assert preview['chart']['colorNotes']
    assert preview['mappingPlan'] == {'brief': 'test'}
    assert 'provenance' not in preview
    assert json.loads(outputs['qa.json'].read_text())['humanPlaytestRequired'] is True


def test_worker_publishes_real_fixture_artifacts_and_downloads(tmp_path, monkeypatch):
    client, job, _ = setup_job(tmp_path)
    def pipeline(**kwargs):
        sample_map(kwargs['output'])
        return {'status': 'playtest_candidate'}
    monkeypatch.setattr('beatforge.connector_worker.run_premium_pipeline', pipeline)
    with client:
        client.headers['Authorization'] = 'Bearer '+'w'*32
        assert run_once(client, tmp_path/'worker')
        client.headers['Authorization'] = 'Bearer '+'a'*32
        result = client.get('/api/jobs/'+job['id']).json()
        assert result['state'] == 'completed'
        assert result['result']['humanPlaytestRequired'] is True
        artifacts = client.get(f"/api/jobs/{job['id']}/artifacts").json()['artifacts']
        assert len(artifacts) == 8
        preview = next(item for item in artifacts if item['name'] == 'preview-Hard.json')
        response = client.get(preview['downloadPath'])
        assert response.status_code == 200
        assert response.json()['difficulty'] == 'Hard'
        assert response.headers['cache-control'] == 'no-store'
        assert client.get(preview['downloadPath'], headers={'Authorization': 'Bearer '+'b'*32}).status_code == 404
        assert client.get(preview['downloadPath'], headers={'Authorization': ''}).status_code == 401
        assert client.get(preview['downloadUrl'], headers={'Authorization': ''}).status_code == 200
        assert client.get(preview['downloadUrl']+'broken', headers={'Authorization': ''}).status_code == 401
        wrong_artifact = preview['downloadUrl'].replace(preview['id'], 'f'*32, 1)
        assert client.get(wrong_artifact, headers={'Authorization': ''}).status_code == 401
        import datetime
        import jwt.api_jwt
        class FutureClock(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.datetime.now(tz) + datetime.timedelta(minutes=6)
        monkeypatch.setattr(jwt.api_jwt, 'datetime', FutureClock)
        assert client.get(preview['downloadUrl'], headers={'Authorization': ''}).status_code == 401


def test_cancelled_artifacts_cannot_publish_and_missing_outputs_block_finish(tmp_path):
    client, job, _ = setup_job(tmp_path)
    worker = {'Authorization': 'Bearer '+'w'*32}
    with client:
        lease = client.post('/api/worker/claim', headers=worker).json()['job']['leaseToken']
        worker['X-BeatForge-Lease'] = lease
        finish = f"/api/worker/jobs/{job['id']}/finish"
        assert client.post(finish, headers=worker, json={'status': 'playtest_candidate'}).status_code == 422
        endpoint = f"/api/worker/jobs/{job['id']}/artifacts/qa.json"
        assert client.put(endpoint, content=b'{}', headers=worker).status_code == 200
        assert client.put(endpoint, content=b'{}', headers=worker).status_code == 422
        assert client.get(f"/api/jobs/{job['id']}/artifacts").json()['artifacts'] == []
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
        assert client.put(endpoint, content=b'{}', headers=worker).status_code == 422
        assert client.post(finish, headers=worker, json={'status': 'needs_anchors'}).status_code == 422
        assert client.get(f"/api/jobs/{job['id']}/artifacts").json()['artifacts'] == []


def test_http_access_logs_redact_download_tickets():
    import logging
    import httpx
    from beatforge.connector_logging import DownloadTicketFilter
    record = logging.LogRecord('httpx', logging.INFO, __file__, 1, 'HTTP Request: %s %s',
                               ('GET', httpx.URL('https://example.com/downloads/123?ticket=private')), None)
    assert DownloadTicketFilter().filter(record)
    assert 'private' not in record.getMessage()
    assert '[redacted]' in record.getMessage()
