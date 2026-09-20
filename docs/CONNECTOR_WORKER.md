# Local connector worker

The gateway stores account-owned audio and durable job records. The worker polls over outbound HTTP requests, downloads only its leased job's audio, verifies its hash and runs the existing premium mapping pipeline locally. It does not install maps, launch Beat Saber or accept shell commands or filesystem paths from an assistant.

This is an implementation in progress. Valid generated maps now transfer back to the gateway with audio, QA and per-difficulty preview JSON. Timing-anchor continuation and the hosted editorial UI are not wired yet. A completed queue entry means its pipeline attempt finished; inspect `result.status` for a request for anchors, corpus setup, palette approval, invalid output or a playtest candidate. It never means human approval.

## Run a private pilot

Install `.[connector,studio]` in the repository's virtual environment. The worker needs the same local corpus and audio tools as the local Studio. Provision a random worker token in the gateway's `BEATFORGE_CONNECTOR_TOKENS` secret with scope `worker` and the same owner as the intended account. Do not give that token to an assistant. OAuth accounts use a namespaced owner derived from issuer and subject; provisioning must deliberately match that account, not merely a display name.

Set the worker's `BEATFORGE_WORKER_TOKEN` environment variable, then run:

```powershell
.venv\Scripts\python.exe -m beatforge.connector_worker --gateway http://127.0.0.1:8013 --work-dir data/worker
```

Use HTTPS outside localhost. `--once` claims at most one job and exits. Store the working directory on a disk with room for audio analysis and generated maps. Outputs are retained for inspection; automatic local retention cleanup is not implemented.

## Request flow

1. Call `create_upload_session` through MCP, or `POST /api/uploads` with `filename` and exact `size` in bytes.
2. Send raw audio bytes to the returned `uploadPath` using `PUT` and the account's authorization header. Audio is not embedded in MCP messages. Use `get_upload` or `GET /api/uploads/{id}` to confirm `ready` and its SHA-256 hash.
3. Call `start_generation` through MCP with a `generation` object, or `POST /api/jobs` with the object as its body. Include `uploadId`, `idempotencyKey`, `title` and optional `artist`, `mapper`, `seed`, `difficulties`, `mappingPlan`, `allowUnconfirmed`. Keep `allowUnconfirmed` false unless the person explicitly accepts unverified timing.
4. Read `get_job` or `GET /api/jobs/{id}` for state and result. Submission returns `workerOnline`; offline jobs remain queued. Cancel with `cancel_job` or `POST /api/jobs/{id}/cancel`.
5. Call `get_artifacts` or `GET /api/jobs/{id}/artifacts`. Each output includes a private five-minute `downloadUrl` and a persistent `downloadPath` requiring account authorization. Resolve relative paths against the gateway origin. Request fresh links after expiry or a gateway restart. Download tickets authorize one specific artifact; treat them as secrets. Configure the deployment and reverse proxy to omit query strings from access logs, or disable access logging with Uvicorn's `--no-access-log` option.

An idempotency key can be retried with the same normalized request. Reusing it for a different request fails. At most two queued/running jobs per account are allowed. Uploads have a 64 MiB per-file limit, 256 MiB reserved account storage limit and 32 reservations per account. An unfinished reservation expires after one hour. Reservations continue counting toward storage quota until explicitly cleaned up; a retention/deletion interface is still pending. The gateway validates extensions and byte counts; the local decoder validates actual audio contents.

## Lease behavior

`POST /api/worker/claim` both announces worker presence and claims an account-owned job. A worker is considered online for 90 seconds after its last claim or heartbeat. Leases last 60 seconds, and the worker heartbeats every 15 seconds. Three expired attempts exhaust retries. Cancellation invalidates the lease immediately; an old worker cannot submit a result after cancellation or reassignment.

Audio download, heartbeat and finish requests require both worker authentication and `X-BeatForge-Lease`. A heartbeat failure stops local computation. The server accepts a constrained result status and message, never pipeline logs or private corpus paths. Authenticated users cannot invoke worker operations without the separately provisioned scope.

The continuous worker reconnects after transport errors, HTTP 408/425/429 and server failures, with delays of 2, 4, 8, 16, 32 and at most 60 seconds. A successful poll resets the backoff. Authentication and other permanent HTTP errors stop the process; repair configuration before restarting. `--once` propagates failures without retrying. Transient transfer errors leave the job lease recoverable instead of prematurely recording a failed job. If a completion response is lost after the gateway commits it, the worker does not overwrite that result. Reclaiming an expired lease still respects the gateway's three-attempt limit.

## Evidence

Artifacts are immutable within a worker lease and count toward a separate 256 MiB account quota. The server only publishes the manifest attached to a completed job. Cancelled and stale attempts cannot publish. Download routes recheck the completed job's published manifest. Map ZIPs contain only referenced gameplay, audio and cover files, not `_beatforge` metadata or unrelated files. Public preview JSON excludes local provenance; public QA retains finding codes and beat positions while omitting diagnostic text that may contain machine paths.

Connector tests exercise streamed size rejection, ownership, immutable completed uploads, quota enforcement, atomic concurrent claims, bounded retries, cancellation, worker input hashing and forwarding the normalized musical brief. Worker pipeline tests substitute a mapper function to isolate the transport contract. Artifact tests publish the repository's real synthetic map fixture through the worker and gateway, then retrieve previews and verify signed-link tampering, expiry and ownership restrictions.

On 2026-09-20, `python tools/smoke_connector.py` also ran the real pipeline through an in-process gateway with the synthetic 30-second track and an isolated copy of the local official corpus. The fresh Hard chart had 32 notes, zero bombs, zero QA errors and one warning. Generation plus upload and retrieval took 61.08 seconds. All four artifacts downloaded successfully: map ZIP, audio, Hard preview and QA. Timing remained explicitly unverified; the test deliberately allowed unconfirmed timing and supplied no human playtest evidence. This is local integration evidence, not a hosted deployment or a verified assistant login. The script retains its report, local diagnostics and artifacts under the ignored `data/connector-smoke` directory.

The smoke run exposed a missing UnityPy dependency. The Studio extra and dependency doctor now include the skill's pinned UnityPy version. The script copies the corpus with SQLite's read-only source connection before indexing, so the original database is not modified by the test.

## Real HTTP MCP check

Set `BEATFORGE_CHECK_TOKEN` to a read-scoped account token in the terminal environment, then run:

```powershell
.venv\Scripts\python.exe tools/check_connector.py --gateway https://YOUR-GATEWAY --job YOUR_JOB_ID
```

Omit `--job` for discovery and mapping-plan checks only. The script uses the official Python MCP SDK, compares the same account's MCP and REST responses, and streams published artifacts through authenticated download routes to verify sizes and SHA-256 hashes. It does not generate maps or write feedback. Output excludes tokens and signed download URLs.

Verified on 2026-09-20 against a live localhost gateway: initialization, 17 discovered tools, equal MCP/REST plans, equal job and QA data, and all four synthetic output downloads with matching hashes. This proves the HTTP MCP transport for that local configuration. It does not prove Meta Muse, Grok, ChatGPT or Claude account setup, hosted OAuth, public deployment, or credit eligibility.
