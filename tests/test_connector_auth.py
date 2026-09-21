import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from beatforge.connector import create_app
from beatforge.connector_auth import OAuthSettings, OAuthVerifier
from beatforge.connector_store import ConnectorStore


@pytest.fixture
def signing(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient, 'get_signing_key_from_jwt', lambda self, token: SimpleNamespace(key=key.public_key()))
    settings = OAuthSettings('https://identity.example/', 'https://identity.example/jwks', 'https://forge.example/mcp')

    def issue(**changes):
        claims = {'iss': settings.issuer, 'aud': settings.resource, 'sub': 'alice',
                  'iat': int(time.time()), 'exp': int(time.time()) + 300, 'scope': 'read edit worker'}
        claims.update(changes)
        return jwt.encode(claims, key, algorithm='RS256')
    return settings, issue


def test_oauth_discovery_cors_and_identity(tmp_path, signing):
    settings, issue = signing
    app = create_app(database=tmp_path/'jobs.db', oauth=settings, hosts=['testserver'], origins=['https://studio.example'])
    actor = OAuthVerifier(settings).verify(issue())
    assert actor.scopes == frozenset({'read', 'edit'})
    job = ConnectorStore(tmp_path/'jobs.db').submit(actor.owner, 'one', {})
    with TestClient(app) as client:
        metadata = client.get('/.well-known/oauth-protected-resource/mcp')
        assert metadata.status_code == 200
        assert metadata.json()['resource'] == settings.resource
        denied = client.get('/api/capabilities', headers={'Origin': 'https://studio.example'})
        assert denied.status_code == 401
        assert settings.metadata_url in denied.headers['www-authenticate']
        assert denied.headers['access-control-allow-origin'] == 'https://studio.example'
        preflight = client.options('/mcp', headers={'Origin': 'https://studio.example', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,mcp-protocol-version'})
        assert preflight.status_code == 200
        assert client.get('/api/jobs/'+job['id'], headers={'Authorization': 'bearer '+issue()}).status_code == 200
        assert client.get('/api/jobs/'+job['id'], headers={'Authorization': 'Bearer '+issue(sub='bob')}).status_code == 404
        assert client.get('/health', headers={'host': 'attacker.example'}).status_code == 400


@pytest.mark.parametrize('changes', [
    {'aud': 'https://different.example/mcp'}, {'iss': 'https://untrusted.example/'},
    {'exp': 1}, {'sub': ''}, {'scope': ['read']}, {'iat': int(time.time()) + 3600},
])
def test_invalid_access_tokens_rejected(tmp_path, signing, changes):
    settings, issue = signing
    app = create_app(database=tmp_path/'jobs.db', oauth=settings, hosts=['testserver'])
    with TestClient(app) as client:
        response = client.get('/api/capabilities', headers={'Authorization': 'Bearer '+issue(**changes)})
        assert response.status_code == 401
        assert response.json() == {'detail': 'Authentication required'}


def test_revocation_missing_claims_and_algorithm(signing):
    settings, issue = signing
    revoked = OAuthSettings(settings.issuer, settings.jwks_url, settings.resource, frozenset({'alice'}))
    with pytest.raises(jwt.InvalidTokenError):
        OAuthVerifier(revoked).verify(issue())
    with pytest.raises(jwt.InvalidTokenError):
        OAuthVerifier(settings).verify(jwt.encode({'sub': 'alice'}, 'secret', algorithm='HS256'))
    with pytest.raises(jwt.InvalidTokenError):
        OAuthVerifier(settings).verify(issue(exp=None))


def test_jwks_outage_fails_closed(tmp_path, signing, monkeypatch):
    settings, issue = signing
    def unavailable(self, token):
        raise jwt.PyJWKClientConnectionError('offline')
    monkeypatch.setattr(jwt.PyJWKClient, 'get_signing_key_from_jwt', unavailable)
    with TestClient(create_app(database=tmp_path/'jobs.db', oauth=settings, hosts=['testserver'])) as client:
        assert client.get('/api/capabilities', headers={'Authorization': 'Bearer '+issue()}).status_code == 401


@pytest.mark.parametrize('url', ['http://id.example/', 'https://id.example/?x=1', 'https://user:secret@id.example/', 'https://id.example/\n'])
def test_insecure_metadata_configuration_rejected(url):
    with pytest.raises(ValueError):
        OAuthSettings(url, 'https://id.example/jwks', 'https://forge.example/mcp')
