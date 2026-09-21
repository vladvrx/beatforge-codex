# Hosting BeatForge

These files prepare deployments; they do not create resources. No credits have been spent and no hosted client login has been verified. The Vercel build includes the synthetic Studio demo and a private token connection panel for remote run previews and ZIP links. Feedback, comparisons and presets use the gateway while connected. Section revisions use the original worker cache. Studio now uploads audio directly and queues Premium generation; browser OAuth sign-in is still pending.

## Render gateway

Use the repository root containing `render.yaml` as the Blueprint root. The blueprint runs only `beatforge.connector:from_env`, with one process and a 1 GB persistent disk. It must never run `beatforge.api:app`: that API controls the local game, installation and credentials. `requirements-gateway.txt` excludes audio analysis, Torch and the official corpus. Source stays in the repository checkout because the shared mapping-plan module uses the checked-in portable skill.

Before provisioning, review the selected compute and disk prices, credit eligibility and expiration in your Render account. The $50 credit is a ceiling for this project, not a promise of a particular runtime. Set account billing alerts and review usage; this blueprint does not enforce a spending cap. Auto-deploy is disabled. There is no autoscaling or cloud generation worker.

Supply these environment variables through Render's secret/settings UI:

| Variable | Value |
| --- | --- |
| `BEATFORGE_CONNECTOR_HOSTS` | Actual gateway hostname, without scheme or path; include any custom hostname explicitly. No wildcard. |
| `BEATFORGE_CONNECTOR_ORIGINS` | Exact Studio HTTPS origin; comma-separated only if more are intentionally allowed. |
| `BEATFORGE_CONNECTOR_TOKENS` | Private JSON token registry described in CONNECTOR_AUTH.md; worker token owner must match the user's owner. Never place this JSON in Vercel or source control. |
| `BEATFORGE_OAUTH_ISSUER` | Configured OAuth provider issuer. |
| `BEATFORGE_OAUTH_JWKS_URL` | That provider's trusted HTTPS signing-key endpoint. |
| `BEATFORGE_OAUTH_RESOURCE` | `https://YOUR-GATEWAY/mcp`, matching access-token audience. |

For a private token-only pilot, leave all three OAuth settings empty. OAuth client registration, consent and token issuance belong to the provider; the gateway does not implement an authorization server. See CONNECTOR_AUTH.md for required claims and limitations.

The configured disk holds SQLite, uploads and published artifacts together. Back up all three consistently before changes. Do not scale this SQLite deployment to multiple instances. Short-lived artifact links expire after a process restart; request new links. Access logs are disabled to avoid recording bearer download tickets; review platform/proxy logging separately. Per-account quotas and an atomic 512 MiB global upload/artifact reservation limit are enforced. Each SQLite connection limits new metadata growth to 64 MiB, leaving room on the 1 GB disk for journals and operations. REST/MCP JSON requests are capped at 1 MiB before parsing, with a 30-second body deadline. These controls bound application storage, not provider billing or operator-added files. New upload reservations reclaim expired pending reservations atomically. Retention of ready uploads/artifacts and recovery of writing reservations after process crashes remain release work; those exhausted reservations require operator action. Do not open this pilot to unrestricted sign-ups.

Run the outbound worker on your PC using CONNECTOR_WORKER.md. Keep the official corpus and private checkpoints there. Check `/health`, then authenticated capabilities and a real upload/generation/download cycle. A healthy gateway does not prove that a worker is online.

## Vercel Studio

Use the same repository root. `vercel.json` selects Other, builds with `node tools/build_static_demo.mjs`, and publishes only `site`. The build needs Node but no npm dependencies or Python. Never set the output directory to the repository root: it contains private development and worker data. Only the synthetic demo assets in `web/assets` are copied.

Review your Vercel plan and credit eligibility before creating the project. Reserve the stated $20 for eligible usage, configure spending controls available to your account, and avoid functions for audio processing. The static site contains no embedded API tokens. Users can enter a token in the connection panel; it stays in memory for the current tab, with no browser-storage persistence. Use the exact gateway origin and allow the Studio origin in gateway CORS. Tokens are never forwarded to redirects. Disconnect clears remote playback and download links.

Local browser verification used the real synthetic worker output through a separate HTTP gateway on port 8013 and Studio on port 8014: authenticated history loaded, the creative plan populated, a 30-second audio/chart preview played, a signed ZIP link was retrieved, and disconnect cleared remote data. This was a read-only synthetic token, not a hosted OAuth/client test. Remote previews carry a separate remote job ID. Editorial controls select the matching workspace and refuse to rate a local preview into a remote account; unsupported remote revision handlers cannot write to the local service.

## Verification status

Local checks exercise gateway import isolation, authenticated REST and connector contracts. The Node and Python static builds must produce identical HTML and assets. Neither local checks nor a valid blueprint prove Render provisioning, Vercel deployment, OAuth consent, budget enforcement or compatibility inside an assistant client.

Configuration references checked on 2026-09-20: [Render Blueprint specification](https://render.com/docs/blueprint-spec), [persistent disks](https://render.com/docs/disks), and [Vercel configuration](https://vercel.com/docs/project-configuration/vercel-json). Recheck account-specific prices before provisioning.
