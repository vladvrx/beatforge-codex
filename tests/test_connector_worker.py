import pytest
from fastapi.testclient import TestClient

from beatforge.connector import Principal, create_app
from beatforge.connector_store import ConnectorStore
from beatforge.connector_worker import run_once, validate_gateway


def setup_job(tmp_path):
    credentials = {'a'*32: Principal('alice', frozenset({'read', 'generate', 'edit'})),
                   'w'*32: Principal('alice', frozenset({'worker'})),
                   'b'*32: Principal('bob', frozenset({'worker', 'read'}))}
    app = create_app(database=tmp_path/'jobs.db', credentials=credentials, hosts=['testserver'])
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer '+'a'*32
    upload = client.post('/api/uploads', json={'filename': 'song.wav', 'size': 4}).json()
    assert client.put(upload['uploadPath'], content=b'data').status_code == 200
    request = {'uploadId': upload['id'], 'idempotencyKey': 'one', 'title': 'Synthetic test',
               'mappingPlan': {'brief': 'lighter verses, no bombs'}}
    response = client.post('/api/jobs', json=request)
    assert response.status_code == 200, response.text
    assert response.json()['workerOnline'] is False
    assert client.post('/api/jobs', json=request).json()['id'] == response.json()['id']
    return client, response.json(), request


def test_worker_lease_audio_finish_and_scopes(tmp_path):
    client, job, _ = setup_job(tmp_path)
    with client:
        assert client.post('/api/worker/claim').status_code == 403
        assert client.post('/api/worker/claim', headers={'Authorization': 'Bearer '+'b'*32}).json()['job'] is None
        worker = {'Authorization': 'Bearer '+'w'*32}
        claimed = client.post('/api/worker/claim', headers=worker).json()['job']
        assert claimed['id'] == job['id']
        assert client.get('/api/capabilities').json()['generationAvailable'] is True
        worker['X-BeatForge-Lease'] = claimed['leaseToken']
        assert client.get(f"/api/worker/jobs/{job['id']}/audio", headers=worker).content == b'data'
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
        assert client.get(f"/api/worker/jobs/{job['id']}/audio", headers=worker).status_code == 422
        assert client.post(f"/api/worker/jobs/{job['id']}/finish", headers=worker, json={'status': 'playtest_candidate'}).status_code == 422


def test_outbound_worker_calls_pipeline_with_editorial_plan(tmp_path, monkeypatch):
    client, job, _ = setup_job(tmp_path)
    received = {}
    def pipeline(**kwargs):
        received.update(kwargs)
        assert kwargs['audio'].read_bytes() == b'data'
        return {'status': 'needs_anchors'}
    monkeypatch.setattr('beatforge.connector_worker.run_premium_pipeline', pipeline)
    with client:
        client.headers['Authorization'] = 'Bearer '+'w'*32
        assert run_once(client, tmp_path/'worker') is True
        assert run_once(client, tmp_path/'worker') is False
    assert received['mapping_plan']['noBombs'] is True
    assert received['mapping_plan']['verseDensity'] < 1
    assert received['allow_unconfirmed'] is False
    result = ConnectorStore(tmp_path/'jobs.db').get('alice', job['id'])
    assert result['state'] == 'completed'
    assert result['result']['status'] == 'needs_anchors'
    assert result['result']['humanPlaytestRequired'] is True
    assert 'audio' not in result['result']


def test_worker_refuses_corrupted_audio_before_pipeline(tmp_path, monkeypatch):
    client, job, _ = setup_job(tmp_path)
    (tmp_path/'uploads'/(job['request']['uploadId']+'.audio')).write_bytes(b'evil')
    monkeypatch.setattr('beatforge.connector_worker.run_premium_pipeline', lambda **kwargs: pytest.fail('Corrupt input reached mapper'))
    with client:
        client.headers['Authorization'] = 'Bearer '+'w'*32
        with pytest.raises(ValueError, match='integrity'):
            run_once(client, tmp_path/'worker')
    assert ConnectorStore(tmp_path/'jobs.db').get('alice', job['id'])['state'] == 'failed'


def test_job_quota_does_not_break_idempotency(tmp_path):
    client, job, request = setup_job(tmp_path)
    with client:
        assert client.post('/api/jobs', json={**request, 'idempotencyKey': 'two'}).status_code == 200
        assert client.post('/api/jobs', json={**request, 'idempotencyKey': 'three'}).status_code == 422
        assert client.post('/api/jobs', json=request).json()['id'] == job['id']


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:secret@example.com', 'https://example.com/path', 'https://example.com?token=abc'])
def test_worker_rejects_unsafe_gateway_urls(url):
    with pytest.raises(ValueError):
        validate_gateway(url)
