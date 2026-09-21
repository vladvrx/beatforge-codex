# Shared editorial API

The remote connector preserves creative briefs in each job request and shares operation handlers between REST and MCP. Its data is account-owned in the gateway SQLite database. The local Studio's existing feedback file is separate and is not silently uploaded or merged.

| MCP tool | REST operation | Required scope |
| --- | --- | --- |
| `list_jobs` | `GET /api/jobs?limit=20&offset=0` | `read` |
| `get_job` | `GET /api/jobs/{job_id}` | `read` |
| `get_chart_preview` | `GET /api/jobs/{job_id}/preview?difficulty=Hard` | `read` |
| `get_qa` | `GET /api/jobs/{job_id}/qa` | `read` |
| `record_mapping_feedback` | `POST /api/feedback` | `feedback` |
| `record_comparison` | `POST /api/comparisons` | `feedback` |
| `save_preset` | `POST /api/presets` | `edit` |
| `list_presets` | `GET /api/presets` | `read` |
| `get_learning_summary` | `GET /api/learning` | `read` |
| `suggest_mapping_adjustments` | `POST /api/learning/suggestions` | `read` |

MCP preview responses contain identity, timing, sections, note count and the REST preview path. REST returns the full chart and waveform plus a five-minute audio URL for the editor. Both read the same hash-verified published artifact. Relative links resolve against the gateway origin. QA findings establish structural inspection only, not headset comfort or release clearance.

Use the authenticated `/openapi.json` route for exact REST schemas. It declares HTTP bearer authentication. MCP tool schemas come from the same request models; MCP object arguments have the wrapper names `feedback`, `comparison`, `preset` or `request`, while REST accepts the object directly as its body.

## Feedback and comparison

Feedback requires `jobId`, `difficulty` and `idempotencyKey`, plus a rating, tag or note. Include the person's tester name. Ratings are integer values from 1 to 5 for overall, flow, readability, musicality and variety. Tags use the same local Studio vocabulary. Agents must only record what a person explicitly supplied; neither defaults nor automated QA establish a human rating.

The server binds records to chart, audio and preview artifact hashes. Clients cannot supply replacement hashes. Retrying identical feedback with the same key returns the original record; changing the payload with that key fails. Comparisons require two different, owned charts with matching audio, tempo, offset and difficulty. Identical charts are rejected.

Suggestions reuse the local Studio's bounded feedback rules. They need evidence from at least three distinct charts from the selected tester, deduplicate repeated reports, and return a proposed plan without applying it. They do not train or promote an RL model. Hosted training and promotion are not implemented.

## Presets and retention

Saving a preset normalizes its musical plan and replaces the same account's preset with the same case-insensitive name. The gateway currently permits up to 1,000 editorial records per account. Authenticated `/api/learning/export` exports only the caller's feedback, comparisons and presets, including their chart hashes. Deletion and retention controls remain unfinished.

## Current verification

Tests cover history pagination, cross-account denial for previews and QA, hash-bound feedback, duplicate handling, permission scopes, comparison identity, private presets and insufficient-evidence suggestions. The Studio connection now routes history, presets, explicit feedback, comparisons, suggestions and dataset export through the gateway while connected. Connection changes clear drafts and comparison state; local previews cannot be rated into the remote account. Identical feedback submissions reuse their idempotency key within the connection. A/B preview switching preserves the song position and selected comparison difficulty. Remote section revision now queues through REST/MCP and the Studio; the live browser revision flow has been verified against a localhost gateway. Browser checks verified a real remote preview, preset save/reload, and empty-feedback rejection without inventing human ratings. Browser A/B switching between a real generated parent and revision preserved the 7-second playback position; no preference rating was fabricated.


## Remote section revisions

Call MCP `revise_section` with `job_id` and `revision`, or POST `/api/jobs/{job_id}/revise` with the revision body. Required fields are `idempotencyKey`, `difficulty`, `baseHash`, `startBeat`, and `endBeat`; optional fields are `seed` and `mappingPlan`. The base hash comes from the published preview. Edit scope and ownership are required. The server rejects stale hashes, reversed/out-of-song ranges and variable-BPM charts. The operation returns a separate queued job and preserves its parent.

Keep the original worker directory. Revisions use that worker's retained analysis and locate a parent map by its complete chart/audio identity; private analysis is never uploaded to the gateway. A different worker without the cache cannot revise the map and reports failure. This is currently a single-worker-per-account workflow, not distributed cache recovery. Older published previews may have revision controls disabled; newly published previews advertise revision eligibility when analysis exists.

The worker revalidates the parent, regenerates the selected difficulty using the existing choreography and timing projection, and applies the shared half-open section merge. Crossing objects require a wider range. All unselected difficulties and objects outside the range are preserved. The child clears inherited playtests and reviews, then passes complete-package QA before any artifacts are published. Failed validation leaves the parent untouched and publishes no playable child.

Transport tests exercise a real section generator and validator with an isolated synthetic audio/chart fixture, stale hashes, ownership denial, idempotency, outside-range preservation, unchanged parent hashes and unselected difficulty bytes, cleared human evidence, and a deliberately invalid child that cannot publish. These are automated structural checks, not headset approval. The Studio submits remote revisions to the gateway, polls their status, and opens a completed child only if the person is still previewing its parent.


On 2026-09-20, a real browser selected the repository synthetic OGG, uploaded it to a separate local HTTP gateway, and queued Hard generation. The outbound worker completed job `f154782ba3da4193a1d69be586df9ec8`. The browser then revised beats 0–16 into child `09eab72ffe764b4ea466790992270688`, opened the result, and switched A/B at 7 seconds. Disk checks confirmed the parent hash was unchanged, the child chart changed, all content outside the selection was preserved, and child QA had zero errors. Timing was explicitly allowed to remain unconfirmed. The ignored smoke directory contains `browser-workflow-report.json`. This is local end-to-end evidence, not hosted OAuth or assistant-account verification.
