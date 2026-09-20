import json
from fastapi.testclient import TestClient
from beatforge.connector import create_app, Principal
from beatforge.connector_store import ConnectorStore


def test_mcp_handshake_tools_and_shared_editorial_rules(tmp_path):
    token = 'a' * 32
    app = create_app(database=tmp_path / 'jobs.db', credentials={token: Principal('alice', frozenset({'read'}))}, hosts=['testserver'])
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json, text/event-stream'}
    with TestClient(app) as client:
        assert client.post('/mcp', json={}).status_code == 401
        schema = client.get('/openapi.json', headers=headers).json()
        assert schema['security'] == [{'BearerAuth': []}]
        assert schema['paths']['/health']['get']['security'] == []
        assert '/api/feedback' in schema['paths']
        reply = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
        assert reply.status_code == 200
        assert reply.json()['result']['serverInfo']['name'] == 'BeatForge'
        tools = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}).json()['result']['tools']
        assert 'resolve_mapping_plan' in [tool['name'] for tool in tools]
        plan = {'brief': 'lighter verses, no bombs', 'style': 'flow'}
        rest = client.post('/api/mapping-plan/resolve', headers=headers, json=plan)
        result = client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'resolve_mapping_plan', 'arguments': {'plan': plan}}}).json()['result']
        assert not result.get('isError'), result
        assert json.loads(result['content'][0]['text']) == rest.json()
        assert rest.json()['noBombs'] is True
        assert rest.json()['verseDensity'] < 1


def test_rest_ownership_and_scope_enforcement(tmp_path):
    path = tmp_path / 'jobs.db'
    app = create_app(database=path, credentials={'a'*32: Principal('alice', frozenset({'read'})), 'b'*32: Principal('bob', frozenset({'read', 'edit'}))}, hosts=['testserver'])
    job = ConnectorStore(path).submit('alice', 'one', {})
    with TestClient(app) as client:
        assert client.get('/api/jobs/'+job['id'], headers={'Authorization': 'Bearer '+'b'*32}).status_code == 404
        assert client.post('/api/jobs/'+job['id']+'/cancel', headers={'Authorization': 'Bearer '+'a'*32}).status_code == 403
        assert client.get('/api/capabilities', headers={'Authorization': 'Bearer '+'a'*32}).json()['generationAvailable'] is False
