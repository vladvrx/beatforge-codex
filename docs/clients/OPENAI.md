# GPT / ChatGPT

For ChatGPT, add the hosted `/mcp` endpoint using the custom app/connector feature available to your account and complete the configured OAuth flow. Account access, OAuth registration and actual tool discovery still need verification; this repository is not a published ChatGPT app.

For the Responses API, construct the MCP tool server-side:

```python
import os

beatforge_tool = {
    "type": "mcp",
    "server_label": "beatforge",
    "server_url": os.environ["BEATFORGE_MCP_URL"],
    "authorization": os.environ["BEATFORGE_ACCESS_TOKEN"],
    "allowed_tools": ["get_capabilities", "resolve_mapping_plan", "list_jobs", "get_job", "get_qa"],
    "require_approval": "always",
}
```

Pass this in `tools` with a supported model and your normal Responses request. Handle approval requests explicitly. Supply authorization again on subsequent requests. Add write tools only with the corresponding BeatForge scopes and intended workflow. Never put a token in a prompt or browser bundle.

Source checked 2026-09-20: [OpenAI MCP guide](https://developers.openai.com/api/docs/guides/tools-connectors-mcp). This configuration has not been run against the OpenAI API or a ChatGPT account.
