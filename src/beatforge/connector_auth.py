"""OAuth resource-server validation; the configured provider owns sign-in and consent."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient

USER_SCOPES = frozenset({'read', 'generate', 'edit', 'feedback'})


@dataclass(frozen=True)
class Principal:
    owner: str
    scopes: frozenset[str]


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or
            any(ord(char) < 33 or ord(char) > 126 or char in '"\\' for char in value)):
        raise ValueError('OAuth URLs must be absolute HTTPS URLs without credentials, query or fragment')
    return value


@dataclass(frozen=True)
class OAuthSettings:
    issuer: str
    jwks_url: str
    resource: str
    revoked_subjects: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        for url in (self.issuer, self.jwks_url, self.resource):
            https_url(url)
        if urlsplit(self.resource).path != '/mcp':
            raise ValueError('The connector resource must identify its /mcp endpoint')

    @property
    def metadata_url(self) -> str:
        parsed = urlsplit(self.resource)
        return f'{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource/mcp'

    def metadata(self) -> dict:
        return {'resource': self.resource, 'resource_name': 'BeatForge',
                'authorization_servers': [self.issuer],
                'scopes_supported': sorted(USER_SCOPES),
                'bearer_methods_supported': ['header']}


class OAuthVerifier:
    def __init__(self, settings: OAuthSettings):
        self.settings = settings
        # Key discovery is fixed by deployment configuration, never by token jku/x5u.
        self.jwks = PyJWKClient(settings.jwks_url, cache_jwk_set=True, lifespan=300, timeout=5)

    def verify(self, token: str) -> Principal:
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256':
            raise jwt.InvalidAlgorithmError('Unsupported access-token signing algorithm')
        key = self.jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=['RS256'], issuer=self.settings.issuer,
                            audience=self.settings.resource,
                            options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
        subject = claims['sub']
        if not isinstance(subject, str) or not subject or subject in self.settings.revoked_subjects:
            raise jwt.InvalidTokenError('Account access revoked or invalid subject')
        scope = claims.get('scope', '')
        if not isinstance(scope, str):
            raise jwt.InvalidTokenError('Invalid scope claim')
        # Worker authority is provisioned separately and cannot be requested by an assistant.
        scopes = frozenset(scope.split()) & USER_SCOPES
        identity = json.dumps([self.settings.issuer, subject], separators=(',', ':'))
        owner = 'oauth:' + hashlib.sha256(identity.encode()).hexdigest()
        return Principal(owner, scopes)
