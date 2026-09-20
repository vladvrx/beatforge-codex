# Claude

For a Claude account offering custom connectors, register your hosted `/mcp` URL and complete its authentication setup. Verify actual discovery and account ownership before enabling writes.

For the Messages API, use the documented MCP beta with a server entry and a separate toolset. Construct these values server-side:

```python
import os

mcp_servers = [{
    "type": "url",
    "name": "beatforge",
    "url": os.environ["BEATFORGE_MCP_URL"],
    "authorization_token": os.environ["BEATFORGE_ACCESS_TOKEN"],
}]
tools = [{
    "type": "mcp_toolset",
    "mcp_server_name": "beatforge",
    "default_config": {"enabled": False},
    "configs": {
        name: {"enabled": True}
        for name in ["get_capabilities", "resolve_mapping_plan", "list_jobs", "get_job", "get_qa"]
    },
}]
betas = ["mcp-client-2025-11-20"]
```

Include these fields in a Messages request with your chosen supported model, messages and token limit. Authentication is issued separately; BeatForge does not mint provider access tokens. Enable generation/edit/feedback tools only after provisioning the matching scopes.

Source checked 2026-09-20: [Claude MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector). Neither this API recipe nor the Claude UI has been account-tested here.
