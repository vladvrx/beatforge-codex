# Connect an assistant

These are setup recipes, not verified account integrations. First provision the isolated gateway using [HOSTING.md](../HOSTING.md), configure [authentication](../CONNECTOR_AUTH.md), and run `tools/check_connector.py`. Use an HTTPS `/mcp` URL reachable by the assistant provider. A cloud assistant cannot reach your PC's localhost address.

- [Meta Muse and Muse Code](META_MUSE.md)
- [GPT and ChatGPT](OPENAI.md)
- [Claude](CLAUDE.md)
- [Grok](GROK.md)

The first connection should use only `read` scope. Grant `generate` for uploads and generation, `edit` for revisions/presets/cancellation, and `feedback` for explicit human reports. Never give an assistant a `worker` credential. OAuth tokens must use the configured resource audience. Static pilot tokens must be provisioned into the gateway registry; arbitrary tokens do not create accounts.

## One verification workflow for every client

1. Ask for capabilities and resolve “flow, lighter verses, no bombs.” Confirm the returned plan matches Studio.
2. Upload the rights-cleared synthetic sample through the raw authenticated upload endpoint described in CONNECTOR_WORKER.md. Do not put audio bytes or local filesystem paths into MCP arguments. Obtain a ready upload ID.
3. Ask the assistant to generate Hard from that upload with a unique idempotency key. Keep timing confirmation explicit; the synthetic smoke may require `allowUnconfirmed: true`.
4. Inspect the returned job until terminal. Open its preview in Studio; compare job ID, chart hash and QA, then download the ZIP. A queued job with an offline worker is not a generation success.
5. Revise one complete section. Confirm a new child ID, unchanged parent, and full-map QA. Compare both charts at the same song position. Record only feedback the person actually supplies.
6. Test a different account against the job: access must fail. Revoke the test token and confirm access fails according to the configured revocation mechanism. Check duplicate submissions, cancellation, worker reconnect and expired download links.

Record client/version, date, authentication method, job IDs and results without tokens or signed URLs. Local HTTP MCP verification has passed; these four account-level checks remain pending. API inference fees are separate from Render/Vercel credits. No sample below is executed automatically.
