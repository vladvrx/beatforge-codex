# Meta Muse / Muse Code

Muse consumer custom connectors use service API information. Ask Muse to create a BeatForge connector and provide the gateway origin and authenticated `/openapi.json` schema. Supply credentials through its secure credential flow, not chat prose. Request read-only discovery first. Do not assume the consumer app accepts a native MCP URL because Muse Code does.

Suggested setup description:

> Connect to my BeatForge gateway. Use its authenticated OpenAPI schema. Start with capabilities, mapping-plan resolution, job history, QA and preview retrieval. Audio uploads use a separate raw-byte endpoint. Generation and revisions return asynchronous job IDs. Record ratings or preferences only when I explicitly provide them. Never report automated QA as a headset playtest.

Source: [Muse custom connectors](https://www.meta.com/help/artificial-intelligence/1687253048996149/). The exact import and consent flow remains unverified in the user's account.

For Muse Code, merge this entry into your user settings or project `.mcp.json`:

```json
{
  "mcpServers": {
    "beatforge": {
      "type": "streamable-http",
      "url": "https://YOUR-GATEWAY/mcp"
    }
  }
}
```

With OAuth configured, use `muse mcp login beatforge --scope read`. Register a client ID with your provider if required. Do not put `${TOKEN}` in HTTP headers: the documented version does not expand it. Plugin MCP entries currently cannot carry credentials, so use authenticated settings instead of claiming a self-contained plugin install.

Source checked 2026-09-20: [Muse Code MCP settings and OAuth](https://meta-models.github.io/muse-code-sdk/next/guides/extend/mcp-servers/). Configuration readiness is not a successful Muse login.
