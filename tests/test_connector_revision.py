import json
import shutil
import pytest

from fastapi.testclient import TestClient
from beatforge.connector import create_app, Principal
from beatforge.connector_worker import run_once
from beatforge.preview import chart_identity, merge_section, read_json
from test_preview import studio, saved_map, validate_package, choreography


@pytest.mark.parametrize('invalid_candidate', [False, True])
def test_remote_revision_preserves_parent_and_publishes_valid_child(tmp_path, monkeypatch, saved_map, invalid_candidate):
    def pipeline(**kwargs):
        shutil.copytree(saved_map, kwargs['output'])
        return {'status': 'playtest_candidate'}
    monkeypatch.setattr('beatforge.connector_worker.run_premium_pipeline', pipeline)
    original_generate = choreography.generate_all
    def isolated_generate(analysis, sections, seed, **kwargs):
        maps, report = original_generate(analysis, sections, seed, corpus_database=tmp_path/'no-corpus.sqlite', **kwargs)
        if invalid_candidate:
            maps['Easy']['colorNotes'].append({'b':2, 'x':99, 'y':0, 'c':0, 'd':1, 'a':0})
        return maps, report
    monkeypatch.setattr(choreography, 'generate_all', isolated_generate)
    app = create_app(database=tmp_path/'gateway/jobs.db', hosts=['testserver'], credentials={
        'a'*32: Principal('alice', frozenset({'read', 'generate', 'edit'})),
        'b'*32: Principal('bob', frozenset({'read', 'edit'})),
        'w'*32: Principal('alice', frozenset({'worker'}))})
    worker_root = tmp_path/'worker'
    user = {'Authorization': 'Bearer '+'a'*32}
    with TestClient(app, headers=user) as client:
        upload = client.post('/api/uploads', json={'filename':'test.wav', 'size':4}).json()
        client.put(upload['uploadPath'], content=b'data').raise_for_status()
        parent = client.post('/api/jobs', json={'uploadId':upload['id'], 'idempotencyKey':'parent',
                            'title':'Revision fixture', 'difficulties':['Easy','Hard']}).json()
        client.headers['Authorization'] = 'Bearer '+'w'*32
        run_once(client, worker_root)
        client.headers.update(user)
        preview = client.get(f"/api/jobs/{parent['id']}/preview?difficulty=Easy").json()
        assert preview['canRevise'] is True
        request = {'idempotencyKey':'edit', 'difficulty':'Easy', 'startBeat':0, 'endBeat':8,
                   'baseHash':preview['chartHash'], 'seed':123,
                   'mappingPlan':{'noBombs':True, 'noWalls':True, 'candidateCount':1}}
        path = f"/api/jobs/{parent['id']}/revise"
        assert client.post(path, json={**request, 'baseHash':'0'*64}).status_code == 422
        assert client.post(path, json={**request, 'endBeat':1000}).status_code == 422
        assert client.post(path, json=request, headers={'Authorization':'Bearer '+'b'*32}).status_code == 404
        reply = client.post(path, json=request)
        assert reply.status_code == 200, reply.text
        child = reply.json()
        assert child['id'] != parent['id']
        assert client.post(path, json=request).json()['id'] == child['id']
        parent_folder = next((worker_root/parent['id']).glob('*/map'))
        before = chart_identity(parent_folder)
        original = read_json(parent_folder/'EasyStandard.dat')
        client.headers['Authorization'] = 'Bearer '+'w'*32
        run_once(client, worker_root)
        client.headers.update(user)
        result = client.get('/api/jobs/'+child['id']).json()
        assert chart_identity(parent_folder) == before
        if invalid_candidate:
            assert result['result']['status'] == 'invalid'
            assert result['result']['artifacts'] == []
            return
        assert result['result']['status'] == 'playtest_candidate', result
        revised = next((worker_root/child['id']).glob('*/map'))
        assert chart_identity(parent_folder) == before
        assert (revised/'HardStandard.dat').read_bytes() == (parent_folder/'HardStandard.dat').read_bytes()
        chart = read_json(revised/'EasyStandard.dat')
        assert [n for n in chart['colorNotes'] if n['b'] >= 8] == [n for n in original['colorNotes'] if n['b'] >= 8]
        assert not validate_package(revised).errors
        provenance = read_json(revised/'_beatforge/provenance.json')
        assert provenance['humanPlaytests'] == [] and provenance['releaseGate'] == {}
        assert 'reviews' not in provenance
        assert client.get(f"/api/jobs/{child['id']}/artifacts").status_code == 200
