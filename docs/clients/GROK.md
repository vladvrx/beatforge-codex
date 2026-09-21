# Grok

Use the custom MCP connector workflow available to your Grok account with the hosted BeatForge `/mcp` endpoint. Never tunnel the privileged local Studio API. The isolated gateway already provides remote HTTP transport.

For xAI's OpenAI-compatible Responses API, construct this tool server-side and include it in the request to `https://api.x.ai/v1/responses`:

```python
import os

beatforge_tool = {
    "type": "mcp",
    "server_label": "beatforge",
    "server_url": os.environ["BEATFORGE_MCP_URL"],
    "authorization": os.environ["BEATFORGE_ACCESS_TOKEN"],
    "allowed_tools": ["get_capabilities", "resolve_mapping_plan", "list_jobs", "get_job", "get_qa"],
}
```

Use your xAI API credential separately for the model request. xAI's current documentation says `require_approval` is unsupported; do not rely on that OpenAI field to protect writes. Use BeatForge scopes and explicit tool allowlists. Add mutation tools only after the user authorizes the workflow.

Sources checked 2026-09-20: [remote MCP API](https://docs.x.ai/developers/tools/remote-mcp), [custom connectors](https://docs.x.ai/grok/connectors/custom-mcp-tunneling). Grok account setup and inference requests have not been executed here.
