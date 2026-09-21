import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from beatforge.connector import create_app, Principal
from beatforge.connector_artifacts import Artifacts
from beatforge.connector_store import ConnectorStore
from beatforge.connector_limits import bounded_request, MAX_JSON_BYTES


def test_global_reservations_include_other_accounts_and_artifacts(tmp_path):
    store = ConnectorStore(tmp_path/'jobs.db')
    store.BLOB_BUDGET = 12
    Artifacts(store, tmp_path/'artifacts')
    store.reserve_upload('alice', 'one.wav', 4)
    with store.connect() as db:
        db.execute("INSERT INTO artifacts(id,owner,job,lease,name,size) VALUES('id','bob','job','lease','audio.ogg',4)")
    store.reserve_upload('charlie', 'two.wav', 4)
    with pytest.raises(ValueError, match='Gateway storage capacity'):
        store.reserve_upload('dave', 'three.wav', 1)


def test_global_reservations_are_atomic_across_accounts(tmp_path):
    store = ConnectorStore(tmp_path/'jobs.db')
    store.BLOB_BUDGET = 8
    def reserve(owner):
        try:
            store.reserve_upload(owner, 'audio.wav', 8)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, ['alice','bob'])) == [False, True]


@pytest.mark.parametrize('route', ['/mcp', '/api/mapping-plan/resolve'])
def test_rest_and_mcp_reject_large_json_before_parsing(tmp_path, route):
    app = create_app(database=tmp_path/'jobs.db', hosts=['testserver'],
                     credentials={'a'*32: Principal('alice', frozenset({'read'}))})
    with TestClient(app) as client:
        response = client.post(route, content=b'x'*(MAX_JSON_BYTES+1),
                               headers={'Authorization':'Bearer '+'a'*32, 'Content-Type':'application/json'})
    assert response.status_code == 413


def test_chunked_body_cannot_bypass_limit():
    async def exercise():
        messages = iter([{'type':'http.request','body':b'a'*MAX_JSON_BYTES,'more_body':True},
                         {'type':'http.request','body':b'b','more_body':False}])
        sent = []
        async def receive(): return next(messages)
        async def send(message): sent.append(message)
        async def forbidden(*args): pytest.fail('Oversized payload reached parser')
        await bounded_request(forbidden, {'type':'http','method':'POST','headers':[]}, receive, send)
        assert sent[0]['status'] == 413
    asyncio.run(exercise())
