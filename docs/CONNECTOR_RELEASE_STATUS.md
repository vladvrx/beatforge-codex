# Connector release evidence

Status: implementation available in draft PR #1; public deployment and assistant account compatibility are not verified. Last reviewed 2026-09-20.

| Requirement | Evidence | Remaining verification |
| --- | --- | --- |
| Shared MCP and REST operations | `connector.py`, operations tests, real HTTP SDK check in `tools/check_connector.py` | Repeat against hosted HTTPS endpoint |
| Authentication, scopes and ownership | RSA JWT/static-token tests, resource discovery, cross-owner denials | Configure identity provider; complete real client login and revocation checks |
| Durable generation and local worker | SQLite leases/idempotency, worker recovery tests; real synthetic browser generation | Measure hosted gateway memory, disk and egress |
| Briefs, plans, previews and QA | Real synthetic generation and synchronized browser preview | Human musical/headset assessment remains separate |
| Safe section revision and A/B | Real child generation, unchanged parent, preserved outside section, zero QA errors; browser A/B retained position | Requires original worker cache |
| Presets and explicit feedback | Owner-bound persistence tests and browser preset save | No human ratings or playtest clearance have been fabricated |
| Human Studio presentation | Transparent neon logo; gateway connection form reviewed at 390, 768 and 1440 pixels, no horizontal page overflow | Connected history and feedback states need final responsive review |
| Existing local behavior | GitHub app-tests and portable skill checks succeeded for `d30e328`; local non-browser suite previously passed | Continue regression checks on subsequent changes |
| Render/Vercel readiness | Isolated lightweight gateway, persistent disk blueprint, static Studio build, setup instructions | Accounts/projects, credit eligibility and selected costs must be checked before provisioning |
| Meta Muse, GPT/ChatGPT, Claude and Grok | Individual setup guides and common acceptance procedure in `clients/` | All four actual account workflows remain unverified |

## Open release work

- Select and configure the OAuth provider and hosting projects. Studio currently supports an in-memory private token session; browser OAuth sign-in is not implemented.
- Exercise each named assistant with the same synthetic generation/revision workflow after the gateway is reachable over HTTPS. SDK transport checks alone do not establish these integrations.
- Add a retention/recovery operation for exhausted storage and abandoned upload reservations before a sustained hosted pilot. Current bounded quotas reject additional work safely but do not reclaim old reservations automatically.
- Verify expired audio-link renewal through the browser and complete responsive review of connected editorial states.

No cloud resources were provisioned or credits spent. See `CONNECTOR_WORKER.md` and `CONNECTOR_EDITORIAL.md` for local workflow evidence and `clients/README.md` for the account verification procedure.
