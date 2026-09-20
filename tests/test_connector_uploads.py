import hashlib
import asyncio

import pytest
from fastapi.testclient import TestClient

from beatforge.connector import Principal, create_app
from beatforge.connector_store import ConnectorStore


def test_stalled_audio_upload_releases_writer_and_removes_partial_file(tmp_path, monkeypatch):
    from fastapi import HTTPException, Request
    from beatforge.connector_uploads import Uploads
    monkeypatch.setattr('beatforge.connector_uploads.UPLOAD_TIMEOUT_SECONDS', 0.02)
    store = ConnectorStore(tmp_path/'jobs.db')
    uploads = Uploads(store, tmp_path/'uploads')
    reservation = store.reserve_upload('alice', 'track.wav', 4)

    async def exercise():
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request', 'body':b'ab', 'more_body':True}
            await asyncio.sleep(10)
        request = Request({'type':'http', 'headers':[]}, receive)
        with pytest.raises(HTTPException) as failure:
            await uploads.receive('alice', reservation['id'], request)
        assert failure.value.status_code == 408
        assert store.get_upload('alice', reservation['id'])['state'] == 'pending'
        assert not list(uploads.root.iterdir())
        async def retry():
            return {'type':'http.request', 'body':b'data', 'more_body':False}
        result = await uploads.receive('alice', reservation['id'], Request({'type':'http','headers':[]}, retry))
        assert result['state'] == 'ready'
        assert uploads.path(reservation['id']).read_bytes() == b'data'
    asyncio.run(exercise())


@pytest.fixture
def connection(tmp_path):
    tokens = {'a'*32: Principal('alice', frozenset({'read', 'generate'})),
              'b'*32: Principal('bob', frozenset({'read', 'generate'})),
              'c'*32: Principal('viewer', frozenset({'read'}))}
    with TestClient(create_app(database=tmp_path/'jobs.db', credentials=tokens, hosts=['testserver'])) as client:
        client.headers['Authorization'] = 'Bearer '+'a'*32
        yield client


def test_upload_is_immutable_and_owner_scoped(connection, tmp_path):
    client = connection
    reserved = client.post('/api/uploads', json={'filename': 'track.wav', 'size': 4})
    assert reserved.status_code == 200
    upload = reserved.json()
    url = upload['uploadPath']
    assert client.put(url, content=b'data', headers={'Authorization': 'Bearer '+'b'*32}).status_code == 404
    response = client.put(url, content=b'data')
    assert response.status_code == 200
    assert response.json()['state'] == 'ready'
    assert response.json()['sha256'] == hashlib.sha256(b'data').hexdigest()
    assert client.put(url, content=b'evil').status_code == 422
    assert (tmp_path/'uploads'/(upload['id']+'.audio')).read_bytes() == b'data'
    assert client.get('/api/uploads/'+upload['id'], headers={'Authorization': 'Bearer '+'b'*32}).status_code == 404
    assert client.get('/api/uploads/'+upload['id']).json()['state'] == 'ready'


def test_stream_limit_rejects_oversize_and_allows_retry(connection, tmp_path):
    upload = connection.post('/api/uploads', json={'filename': 'track.wav', 'size': 4}).json()
    assert connection.put(upload['uploadPath'], content=iter([b'012', b'345'])).status_code == 413
    assert not list((tmp_path/'uploads').iterdir())
    assert connection.get('/api/uploads/'+upload['id']).json()['state'] == 'pending'
    assert connection.put(upload['uploadPath'], content=iter([b'12'])).status_code == 422
    assert connection.put(upload['uploadPath'], content=b'abcd').status_code == 200


@pytest.mark.parametrize('payload', [
    {'filename': '../track.wav', 'size': 4}, {'filename': 'C:\\track.wav', 'size': 4},
    {'filename': 'track.exe', 'size': 4}, {'filename': 'track.wav', 'size': 0},
    {'filename': 'track.wav', 'size': 67108865}, {'filename': 'track.wav', 'size': 4, 'path': '/etc/passwd'},
])
def test_upload_validation(connection, payload):
    assert connection.post('/api/uploads', json=payload).status_code == 422


def test_quota_and_permission(connection):
    payload = {'filename': 'large.wav', 'size': 64*1024*1024}
    assert connection.post('/api/uploads', json=payload, headers={'Authorization': 'Bearer '+'c'*32}).status_code == 403
    for _ in range(4):
        assert connection.post('/api/uploads', json=payload).status_code == 200
    assert connection.post('/api/uploads', json=payload).status_code == 422


def test_expired_reservation_and_parallel_writer_denied(connection, tmp_path):
    upload = connection.post('/api/uploads', json={'filename': 'track.wav', 'size': 4}).json()
    store = ConnectorStore(tmp_path/'jobs.db')
    store.begin_upload('alice', upload['id'])
    assert connection.put(upload['uploadPath'], content=b'data').status_code == 422
    store.abort_upload('alice', upload['id'])
    with store.connect() as db:
        db.execute('UPDATE uploads SET expires=0 WHERE id=?', (upload['id'],))
    assert connection.put(upload['uploadPath'], content=b'data').status_code == 422
    assert not list((tmp_path/'uploads').iterdir())
