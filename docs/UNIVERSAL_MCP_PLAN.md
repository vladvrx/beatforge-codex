# BeatForge across AI assistants

Research date: 2026-09-20. This is a proposed architecture, not a deployed service or a claim of tested client compatibility.

## Decision

Build one authenticated service with Streamable HTTP MCP at `/mcp` and a documented REST/OpenAPI interface. Keep browser WebMCP as a third adapter to the same application operations. Use Vercel for the website, Render for the gateway and durable job queue, and an outbound-connected local worker for expensive audio processing and RL. The existing local Studio should not be exposed directly to the internet.

## Client compatibility

- ChatGPT: remote MCP app with OAuth and account-dependent developer-mode availability. OpenAI Responses API also supports remote MCP. Public directory distribution is a separate submission process.
- Claude: remote MCP custom connector, plus API connector where needed.
- Grok: custom remote MCP connector and remote MCP API tools.
- Meta Muse consumer app: documented custom connector creation from service API information. Provide a scoped REST API and connector setup instructions. Do not equate this with a verified native MCP URL registration workflow.
- Muse Code: remote MCP settings support credentials/OAuth. Developer-preview plugin manifests currently do not carry credential fields; authenticated access should use settings rather than embedding a token in a plugin.

## Hosting and budget

Treat the stated $50 Render and $20 Vercel as separate hosting credits, subject to redemption terms and expiry. They do not establish any model-inference allowance.

Target a small always-on Render gateway. Published entry compute pricing surfaced at approximately $7/month; $50 / $7 is about seven months of compute alone. Storage, egress, workspace charges, credit eligibility and actual selected plan must be checked before provisioning. Do not put Torch, stem separation or RL training in that small process.

Use Vercel for static pages, sign-in redirects, upload UI and preview. Avoid running song generation inside a function. Upload song bytes directly to authenticated storage or the gateway, not through MCP messages. Reserve the $20 for eligible usage; do not assume promotional credits pay subscription fees.

Begin with one Render process and durable SQLite on an attached paid disk for a single gateway instance. Separate job metadata from audio storage and apply retention. Move to a managed database/object store if multi-instance operation or traffic justifies it.

The PC worker authenticates over outbound HTTPS and leases jobs. The PC must be online for private generation; return worker-offline status immediately when it is unavailable. Keep private official-corpus assets and checkpoints local. Cloud users see only their own authorized artifacts.

## Implementation sequence

1. Extract application operations from browser state and FastAPI handlers. Define typed inputs/results shared by REST, MCP and WebMCP.
2. Implement OAuth discovery, audience-bound token validation, project ownership checks and scopes such as read, generate and feedback. A job ID alone must never authorize access. Use an established OAuth provider/library.
3. Add MCP tools: capabilities, create upload session, resolve mapping plan, start generation, inspect job, cancel job, read QA, revise section, get preview/download links, and record explicit feedback. Long operations return a job ID promptly; support idempotent submission.
4. Add a durable queue with leases, heartbeats, attempt limits and restart recovery. A worker uses constrained operations, never arbitrary model-supplied shell commands or filesystem paths.
5. Deploy the gateway separately from the local Studio. Do not expose credential management, game launch or automatic local installation as public cloud tools. Add request/size/concurrency limits and per-user quotas before enabling writes.
6. Connect Vercel UI to the gateway through authenticated requests and an explicit CORS allowlist. Keep secrets server-side, and use short-lived artifact URLs. Build a public read-only synthetic demo before opening paid generation.
7. Supply four setup guides, including Muse custom-connector instructions and Muse Code settings. Test each actual client; do not infer all-client compatibility from SDK tests.

## Acceptance

### Human Studio design

Visual quality is a release requirement alongside connector functionality. Preserve the existing BeatForge logo, red and blue chart identity, creative briefs and editorial controls. The Studio should feel like a music workspace, with the audio and chart preview as the main review area.

- Use consistent spacing, typography, borders and restrained accent colors. Give primary actions clear emphasis; keep technical configuration behind progressive disclosure.
- Make the sequence easy to follow: choose a track, describe the map, generate, listen and inspect, revise, then export. Let people return to the brief without losing preview position or work.
- Keep waveform, playback controls, difficulty selection, section boundaries and revision tools together. Distinguish the selected section and changes in a revision without relying on color alone.
- Show useful empty, loading, offline, failed and completed states. Never display decorative progress or fake connection status. An assistant-created project must open in the same editorial workspace with its actual brief, artifacts and QA.
- Provide visible keyboard focus, readable contrast, adequately sized controls and reduced-motion support. Support phone, tablet and desktop layouts without horizontal page overflow.
- Review rendered screenshots at 390, 768 and 1440 pixels wide. Exercise upload, demo playback, section selection, revision and recovery in the browser. Passing backend tests does not establish visual quality.

Initial Studio visual review passed at phone, tablet and desktop widths. Remote upload, generation, preview, section revision and A/B switching have also been exercised in the browser. Final visual regression of the complete connector UI remains part of release review.

For every client, authenticate, discover tools, submit the same synthetic test job, observe progress, retrieve identical job/QA data and a working preview/download link. Verify token revocation, cross-user denial, duplicate requests, worker disconnect/restart, cancellation, expired downloads and exhausted quotas. No automated test may invent human ratings or headset clearance. Measure real memory, storage and bandwidth before estimating paid cloud generation capacity.

## Sources

- Meta Muse custom connectors: https://www.meta.com/help/artificial-intelligence/1687253048996149/
- Muse Code MCP: https://meta-models.github.io/muse-code-sdk/next/guides/extend/mcp-servers/
- Muse Code plugins: https://meta-models.github.io/muse-code-sdk/next/guides/plugins/
- OpenAI remote MCP: https://developers.openai.com/api/docs/guides/tools-connectors-mcp
- OpenAI plugin authentication: https://developers.openai.com/plugins/build/auth
- Claude remote MCP: https://platform.claude.com/docs/en/agents-and-tools/remote-mcp-servers
- Grok remote MCP: https://docs.x.ai/developers/tools/remote-mcp
- Grok custom connectors: https://docs.x.ai/grok/connectors/custom-mcp-tunneling
- Render pricing and disks: https://render.com/pricing and https://render.com/docs/disks
- Vercel function limits: https://vercel.com/docs/functions/limitations

The initial repository review found only browser-bound WebMCP and the local API. The current worktree now implements the isolated MCP/REST gateway, scoped authentication, durable queue, outbound worker and remote editorial UI. See CONNECTOR_WORKER.md and CONNECTOR_EDITORIAL.md for measured local verification. Hosted OAuth and assistant-account tests remain unverified. No hosting resources have been purchased or deployed.
