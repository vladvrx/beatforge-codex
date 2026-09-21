# Connector authentication

The connector is a separate application from the privileged local Studio. It implements an OAuth resource server, not an authorization server. Sign-in, consent, client registration and token issuance belong to your configured OAuth provider. No provider account or public deployment has been configured yet.

Install the connector dependencies with `python -m pip install '.[connector]'`. Start the isolated gateway with `python -m uvicorn beatforge.connector:from_env --factory --host 127.0.0.1 --port 8013`.

## Hosted OAuth configuration

Set these environment variables in the gateway service. Example domains are placeholders.

| Variable | Example or purpose |
| --- | --- |
| `BEATFORGE_OAUTH_ISSUER` | `https://identity.example/`, exactly matching the provider's issuer claim |
| `BEATFORGE_OAUTH_JWKS_URL` | `https://identity.example/.well-known/jwks.json`, a trusted provider key URL |
| `BEATFORGE_OAUTH_RESOURCE` | `https://forge.example/mcp`, also the required access-token audience |
| `BEATFORGE_CONNECTOR_HOSTS` | `forge.example`, comma-separated explicit allowed hosts |
| `BEATFORGE_CONNECTOR_ORIGINS` | `https://studio.example`, comma-separated browser origins |
| `BEATFORGE_CONNECTOR_DB` | A path on persistent storage, such as `/var/data/jobs.sqlite3` |
| `BEATFORGE_REVOKED_SUBJECTS` | Optional JSON array of provider subject IDs denied access, default `[]` |

The provider must issue RS256 access tokens with `iss`, `aud`, `sub`, `iat` and `exp`. Permissions use the space-separated `scope` claim: `read`, `generate`, `edit`, `feedback`. The server ignores unsupported scopes, including `worker`. Worker authority must be provisioned separately. Give the provider a dedicated API audience; never configure an identity-token client ID as the resource audience.

Unauthenticated clients discover the issuer through `/.well-known/oauth-protected-resource/mcp` or the root metadata route. Unauthorized responses include a `WWW-Authenticate` challenge pointing to that metadata. Both REST and MCP require authorization on every request. Browser CORS preflight works only for the configured origins.

The provider must offer authorization-server discovery and authorization code flow with PKCE. Configure each client's redirect URI and client registration at the provider. Dynamic registration or manual client IDs depend on the provider and actual client. This code does not establish that ChatGPT, Claude, Grok or Muse has completed a login flow.

Account ownership is derived from the trusted issuer and subject together. Token-controlled key URLs are ignored. Key discovery uses the configured JWKS endpoint with a five-second timeout and five-minute key-set cache. Invalid tokens and unavailable keys fail closed. No incoming assistant token is forwarded to a worker or another service.

Use short-lived access tokens. JWT validation alone does not detect provider-side revocation before token expiry. For immediate local account blocking, add the subject to `BEATFORGE_REVOKED_SUBJECTS` and restart the service. Immediate per-token revocation/introspection is not implemented. Re-run the real client login and revocation acceptance tests after choosing a provider.

## Private pilot credentials

For local integration tests or a private pilot, `BEATFORGE_CONNECTOR_TOKENS` accepts a JSON object mapping random tokens to an owner and scopes:

```json
{"<random-secret-at-least-32-characters>": {"owner": "pilot-account", "scopes": ["read", "edit"]}}
```

Generate secrets with a cryptographic random generator and store them in environment secrets, never source control. These credentials are static bearer tokens, not OAuth. Removing a token and restarting revokes it. Use the same owner only when connections deliberately share an account. OAuth owners are separate from pilot owners unless an administrator explicitly provisions that linkage.

## Validation status

Automated tests exercise signed RSA tokens, resource discovery, CORS preflight, wrong issuer/audience, expiry, malformed claims, account revocation, JWKS outage, ownership isolation and shared REST/MCP editorial behavior. They use local keys, not a live provider. Actual hosted login, client registration and provider revocation remain unverified.

References: [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) and [PyJWT usage](https://pyjwt.readthedocs.io/en/latest/usage.html).
