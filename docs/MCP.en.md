# MCP servers (MCP Manager)

**English** · [简体中文](MCP.md)

ShiritaiBot can act as an MCP (Model Context Protocol) client, connect to MCP servers, and give the tools they provide to the Agent.

Code: `src/shiritaibot/mcp/`. `client.py` is a minimal client with no extra dependencies; `__init__.py` manages connections and registers tools.

## What's supported

- Transports: **stdio** (a local process, line-delimited JSON-RPC) and **Streamable HTTP** (JSON or SSE responses, `Mcp-Session-Id` sessions). The older HTTP+SSE two-endpoint transport isn't supported yet.
- Capabilities: `initialize`, `tools/list` (with pagination), `tools/call`, answering the server's `ping`, and refreshing tools automatically on `notifications/tools/list_changed`.
- Server-initiated sampling, elicitation, and roots requests get "method not found", so the server can fall back on its own.
- Resources and prompts aren't connected yet.

## Configuration

Add servers in the WebUI under **Capabilities & Permissions → MCP**, or write them into `config.toml`:

```toml
[[mcp_servers]]
id = "files"
enabled = true
transport = "stdio"
command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/home/me/docs"]
env = { }
timeout = 60
risk = "sensitive"                       # default risk level
tool_risks = { read_text_file = "safe", list_directory = "safe" }
disabled_tools = ["move_file"]

[[mcp_servers]]
id = "remote"
enabled = true
transport = "http"
url = "https://example.com/mcp"
headers = { Authorization = "Bearer xxx" }   # encrypted to secret:... on save
```

- `command` is an argv list and doesn't go through a shell. If `cwd` is empty, the current directory is used; if set, it must be an absolute path.
- Values in `env` and `headers` can be `env:VARIABLE_NAME` or `secret:...`. On save, every `headers` value, and every `env` value whose name contains KEY / TOKEN / SECRET / PASSWORD / CREDENTIAL / AUTH, is moved into the Vault automatically.
- HTTP URLs must be http(s) and can't contain a username or password.

## Tools and permissions

- Each tool is registered as `mcp_<server>_<tool>` (invalid characters become `_`; names over 64 characters are truncated with a hash added). Its description starts with `[MCP server]`, and its source shows as `mcp:<server>`.
- Risk levels come only from the configuration: `tool_risks` overrides by original tool name; otherwise the server's `risk` applies (default `sensitive`). A server's own `readOnlyHint` is only shown in the WebUI and never lowers a tool to safe.
- Like plugin tools, MCP tools are only available in Agent mode and are subject to per-session tool permissions, approvals, and block lists.
- Text content returned by a tool is joined and passed to the model; `structuredContent` is attached as is; images, audio, and binary resources only return a placeholder. `isError` results are returned to the model as tool errors.

## Lifecycle

- On startup, all enabled servers connect in parallel. A failed connection only logs an error and doesn't affect other servers or startup.
- Saving the configuration restarts only the servers whose configuration changed; unchanged connections stay up.
- If a stdio server process exits unexpectedly, it reconnects once on the next tool call.
- The WebUI shows each server's connection status, server info, and tool list, and you can **Reconnect** by hand.
- MCP server processes run under the same account as ShiritaiBot and count as trusted local code; only connect servers you trust.
