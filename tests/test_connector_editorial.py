import json

import pytest
from fastapi.testclient import TestClient

from beatforge.connector import Principal, create_app
from beatforge.connector_worker import run_once
from test_connector_artifacts import sample_map


@pytest.fixture
def editor(tmp_path, monkeypatch):
    app = create_app(database=tmp_path/'jobs.db', hosts=['testserver'], credentials={
        'a'*32: Principal('alice', frozenset({'read', 'generate', 'edit', 'feedback'})),
        'b'*32: Principal('bob', frozenset({'read', 'feedback'})),
        'v'*32: Principal('alice', frozenset({'read'})),
        'w'*32: Principal('alice', frozenset({'worker'}))})
    counter = [0]
    def pipeline(**kwargs):
        counter[0] += 1
        sample_map(kwargs['output'])
        if counter[0] > 1:
            path = kwargs['output']/'HardStandard.dat'
            chart = json.loads(path.read_text())
            chart['colorNotes'][0]['b'] += .125
            path.write_text(json.dumps(chart))
        return {'status': 'playtest_candidate'}
    monkeypatch.setattr('beatforge.connector_worker.run_premium_pipeline', pipeline)
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer '+'a'*32
        upload = client.post('/api/uploads', json={'filename': 'test.wav', 'size': 4}).json()
        client.put(upload['uploadPath'], content=b'data').raise_for_status()
        jobs = []
        for key in ('a', 'b'):
            response = client.post('/api/jobs', json={'uploadId': upload['id'], 'idempotencyKey': key, 'title': key})
            response.raise_for_status()
            jobs.append(response.json()['id'])
            client.headers['Authorization'] = 'Bearer '+'w'*32
            run_once(client, tmp_path/'worker')
            client.headers['Authorization'] = 'Bearer '+'a'*32
        yield client, jobs


def test_history_preview_and_qa_share_account_scope(editor):
    client, jobs = editor
    history = client.get('/api/jobs?limit=1').json()
    assert history['total'] == 2 and len(history['jobs']) == 1
    assert client.get('/api/jobs?limit=0').status_code == 422
    preview = client.get(f'/api/jobs/{jobs[0]}/preview?difficulty=Hard')
    assert preview.status_code == 200
    assert preview.json()['chart']['colorNotes']
    assert client.get(preview.json()['audioUrl'], headers={'Authorization': ''}).status_code == 200
    assert client.get(f'/api/jobs/{jobs[0]}/qa').json()['humanPlaytestRequired'] is True
    bob = {'Authorization': 'Bearer '+'b'*32}
    assert client.get('/api/jobs', headers=bob).json()['total'] == 0
    assert client.get(f'/api/jobs/{jobs[0]}/preview', headers=bob).status_code == 404
    assert client.get(f'/api/jobs/{jobs[0]}/qa', headers=bob).status_code == 404


def test_feedback_is_hash_bound_idempotent_and_scope_checked(editor):
    client, jobs = editor
    payload = {'jobId': jobs[0], 'difficulty': 'Hard', 'tester': 'Synthetic test fixture',
               'notes': 'Test feedback only, not a human playtest', 'idempotencyKey': 'feedback-1'}
    response = client.post('/api/feedback', json=payload)
    assert response.status_code == 200
    assert response.json()['previewSha256']
    preview = client.get(f'/api/jobs/{jobs[0]}/preview').json()
    assert response.json()['chartHash'] == preview['chartHash']
    assert client.post('/api/feedback', json=payload).json()['id'] == response.json()['id']
    assert client.get('/api/learning').json()['feedbackCount'] == 1
    exported = client.get('/api/learning/export').json()
    assert len(exported['feedback']) == 1
    assert exported['feedback'][0]['chartHash'] == preview['chartHash']
    assert exported['automaticTraining'] is False
    assert client.get('/api/learning/export', headers={'Authorization': 'Bearer '+'b'*32}).json()['feedback'] == []
    assert client.get('/api/learning/export', headers={'Authorization': 'Bearer '+'w'*32}).status_code == 403
    suggestions = client.post('/api/learning/suggestions', json={'tester': payload['tester']}).json()
    assert suggestions['evidenceCount'] == 1
    assert suggestions['ready'] is False
    assert suggestions['changedFields'] == {}
    assert client.post('/api/feedback', json={**payload, 'notes': 'changed'}).status_code == 422
    assert client.post('/api/feedback', json={**payload, 'chartHash': 'forged'}).status_code == 422
    assert client.post('/api/feedback', json=payload, headers={'Authorization': 'Bearer '+'v'*32}).status_code == 403
    assert client.post('/api/feedback', json=payload, headers={'Authorization': 'Bearer '+'b'*32}).status_code == 404


def test_comparison_and_presets_are_persistent_and_private(editor):
    client, jobs = editor
    payload = {'preferredJob': jobs[0], 'alternateJob': jobs[1], 'difficulty': 'Hard',
               'tester': 'Synthetic test fixture', 'idempotencyKey': 'comparison-1'}
    response = client.post('/api/comparisons', json=payload)
    assert response.status_code == 200
    assert response.json()['preferredHash'] != response.json()['alternateHash']
    assert client.post('/api/comparisons', json={**payload, 'alternateJob': jobs[0]}).status_code == 422
    preset = {'name': 'Light verses', 'mappingPlan': {'brief': 'lighter verses, no bombs'}}
    first = client.post('/api/presets', json=preset).json()
    assert first['mappingPlan']['noBombs'] is True
    assert client.post('/api/presets', json={**preset, 'name': 'LIGHT VERSES'}).json()['id'] == first['id']
    assert len(client.get('/api/presets').json()['presets']) == 1
    assert client.get('/api/presets', headers={'Authorization': 'Bearer '+'b'*32}).json()['presets'] == []
    assert client.get('/api/learning').json()['automaticTraining'] is False
    exported = client.get('/api/learning/export').json()
    assert exported['comparisons'][0]['preferredHash'] == response.json()['preferredHash']
    assert exported['presets'][0]['id'] == first['id']
