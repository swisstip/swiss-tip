# Connect a client

**Last update:** 24 September 2026

How to connect an MCP client, an agent harness or an agent framework to
Swiss TIP. The server speaks MCP over Streamable HTTP at `/mcp` - stateless,
JSON responses, no authentication, no legacy SSE endpoint - and over stdio
when a client starts it as a local process. Every example uses the hosted
endpoint; for a server on your machine, started as the
[README](../README.md#quick-start) describes, put the local URL in its place.

| | |
| --- | --- |
| Hosted endpoint | `https://51-96-83-1.sslip.io/mcp` |
| Local endpoint | `http://127.0.0.1:8000/mcp` |
| stdio | `docker run --rm -i ghcr.io/swisstip/swiss-tip:mvp-zurich --transport stdio` |
| Server name used below | `swiss-tip`, or `swiss_tip` where a client wants no hyphen |
| Tools | `get_coverage`, `search`, `resolve`, `get_evidence`, and `lookup` where the calendar connector runs, as on the hosted endpoint |

Three things hold for every client:

- **Cloud clients need the hosted endpoint.** Claude's and ChatGPT's
  connectors, Le Chat, Copilot Studio and OpenAI's hosted MCP tool call the
  server from the vendor's cloud, so they reach only the public HTTPS URL,
  never a server on your machine.
- **Timeouts.** A tool call normally answers in well under a second. A stdio
  server loads its embedding model before it answers, about ten seconds;
  pull the image first (`docker pull ghcr.io/swisstip/swiss-tip:mvp-zurich`)
  so that the client's start-up timeout is not spent on the download.
- **The rules for the model travel with the tools.** The server's
  instructions (search once, then resolve; translate the key terms of a
  French, Italian or Romansh question into German; follow
  `guidance_for_caller`) are repeated in the tool descriptions and in every
  result, so a client that drops the MCP instructions still passes them on.

## What was tested

These routes were run against the hosted endpoint, listing its tools and
calling one:

| Route | Version |
| --- | --- |
| MCP Python SDK, the v2 `Client` and the v1 `streamable_http_client` | 2.2.0 and 1.30.0 |
| MCP Inspector, CLI mode | 2.8.0 |
| `mcp-remote` as a stdio bridge, driven by the Python SDK's stdio client | 0.14.3 |
| OpenCode (`opencode mcp list` reports it connected) | 1.18.31 |
| Docker stdio, driven by the Python SDK's stdio client | the pack image |
| Claude Code: `claude mcp add` writes the configuration below; the connection itself was not approved in the test | 2.1.220 |

Every other configuration below follows the client's own documentation as of
September 2026 and was not run here.

## Command-line agents and editors

### Claude Code

```shell
claude mcp add --transport http swiss-tip https://51-96-83-1.sslip.io/mcp
```

Options go before the name. `--scope project` writes the server into
`.mcp.json`, to share it with a project:

```json
{"mcpServers": {"swiss-tip": {"type": "http", "url": "https://51-96-83-1.sslip.io/mcp"}}}
```

Over stdio instead:

```shell
claude mcp add --transport stdio swiss-tip -- docker run --rm -i ghcr.io/swisstip/swiss-tip:mvp-zurich --transport stdio
```

### Codex

```shell
codex mcp add swiss-tip --url https://51-96-83-1.sslip.io/mcp
```

or in `~/.codex/config.toml` (`.codex/config.toml` in a project):

```toml
[mcp_servers.swiss-tip]
url = "https://51-96-83-1.sslip.io/mcp"
```

Over stdio, raise the start-up timeout (10 seconds by default), because the
image loads its model first:

```toml
[mcp_servers.swiss-tip]
command = "docker"
args = ["run", "--rm", "-i", "ghcr.io/swisstip/swiss-tip:mvp-zurich", "--transport", "stdio"]
startup_timeout_sec = 30
```

The Codex IDE extension adds the same server under its MCP settings, as a
Streamable HTTP server.

### Gemini CLI

```shell
gemini mcp add --transport http --scope user swiss-tip https://51-96-83-1.sslip.io/mcp
```

or in `~/.gemini/settings.json`:

```json
{"mcpServers": {"swiss-tip": {"httpUrl": "https://51-96-83-1.sslip.io/mcp"}}}
```

A plain `url` without `"type": "http"` means SSE to Gemini CLI and fails
against this server; `mcp add` writes `url` together with `"type": "http"`,
which works as well.

### OpenCode

In `opencode.json` of the project, or `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "swiss_tip": {"type": "remote", "url": "https://51-96-83-1.sslip.io/mcp", "enabled": true}
  }
}
```

`opencode mcp list` then shows it connected.
`swisstip-server --print-client-config opencode --url <endpoint>` prints the
same block.

### Goose

`goose configure`, then Add Extension, Remote Extension (Streamable HTTP),
with the endpoint URL; in Goose Desktop, Extensions, Add custom extension.
The same in `~/.config/goose/config.yaml` (on Windows
`%APPDATA%\Block\goose\config\config.yaml`):

```yaml
extensions:
  swiss-tip:
    name: swiss-tip
    type: streamable_http
    uri: https://51-96-83-1.sslip.io/mcp
    enabled: true
    timeout: 300
```

The key is `uri`, and the type is written with an underscore.

### VS Code with GitHub Copilot

In `.vscode/mcp.json` of the workspace, or in the user configuration (command
"MCP: Open User Configuration"):

```json
{"servers": {"swiss-tip": {"type": "http", "url": "https://51-96-83-1.sslip.io/mcp"}}}
```

The top-level key is `servers`, not `mcpServers`. VS Code asks whether to
trust the server when it first starts it; the tools are used in agent mode.

### Cursor

In `~/.cursor/mcp.json`, or `.cursor/mcp.json` in the project:

```json
{"mcpServers": {"swiss-tip": {"url": "https://51-96-83-1.sslip.io/mcp"}}}
```

Enable the server in Cursor's settings; tool calls ask for approval by
default.

### Windsurf, now Devin Desktop

Its default agent, Devin Local:

```shell
devin mcp add swiss-tip https://51-96-83-1.sslip.io/mcp
```

or in `~/.config/devin/mcp_config.json` (on Windows
`%APPDATA%\devin\mcp_config.json`; `.devin/mcp_config.json` in a project):

```json
{"mcpServers": {"swiss-tip": {"url": "https://51-96-83-1.sslip.io/mcp", "transport": "http"}}}
```

The legacy Cascade agent and older Windsurf installs read
`~/.codeium/windsurf/mcp_config.json`, with `serverUrl` in place of `url`.

### Zed

In `settings.json`, or Settings, AI, MCP Servers, Add Remote Server:

```json
{"context_servers": {"swiss-tip": {"url": "https://51-96-83-1.sslip.io/mcp"}}}
```

Zed's documentation says it offers an OAuth sign-in for a remote server
without an `Authorization` header; this server needs none.

### Continue

In `.continue/mcpServers/swiss-tip.yaml`:

```yaml
name: swiss-tip
version: 0.0.1
schema: v1
mcpServers:
  - name: swiss-tip
    type: streamable-http
    url: https://51-96-83-1.sslip.io/mcp
```

Continue uses MCP tools in agent mode only.

## Chat applications

### Claude Desktop and claude.ai

**Remote, as a custom connector.** Customize, Connectors, "+", Add custom
connector, with the hosted URL and no authentication (some of Anthropic's
pages name the menu Settings, Connectors). A connector belongs to the
account, so it appears in Claude Desktop, on claude.ai and on mobile alike.
The Free plan allows one custom connector; on Team and Enterprise an owner
adds it first under Organization settings, Connectors.

**Local, in `claude_desktop_config.json`** (Settings, Developer, Edit
Config), which takes commands only: the image over stdio, or the bridge
`mcp-remote` (it needs Node.js) in front of an HTTP endpoint. Restart
Claude Desktop after editing the file.

```json
{"mcpServers": {"swiss-tip": {"command": "docker", "args": ["run", "--rm", "-i", "ghcr.io/swisstip/swiss-tip:mvp-zurich", "--transport", "stdio"]}}}
```

```json
{"mcpServers": {"swiss-tip": {"command": "npx", "args": ["-y", "mcp-remote", "https://51-96-83-1.sslip.io/mcp", "--transport", "http-only"]}}}
```

### ChatGPT

In developer mode (Plus, Pro, Business, Enterprise and Education, on the
web): turn developer mode on under Settings, Security and login; add an app
at chatgpt.com/plugins with the hosted URL as its connection URL and "No
Authentication"; then choose it in a chat from the plus menu, Developer mode.

### Open WebUI

From version 0.6.31, an administrator adds the server under the admin
settings, Integrations, External Tool Servers: type "MCP (Streamable HTTP)",
the endpoint URL, authentication None. Bearer with an empty key sends an
empty `Authorization` header and fails. Open WebUI in Docker reaches a
server on the same host at `http://host.docker.internal:8000/mcp`, and the
model needs native tool calling.

### LibreChat

In `librechat.yaml`:

```yaml
mcpServers:
  swiss-tip:
    type: streamable-http
    url: https://51-96-83-1.sslip.io/mcp
```

LibreChat blocks local and private addresses by default. For a server on
the Docker host, allow it and use its host name:

```yaml
mcpSettings:
  allowedAddresses: ["host.docker.internal:8000"]
mcpServers:
  swiss-tip:
    type: streamable-http
    url: http://host.docker.internal:8000/mcp
```

### Mistral Le Chat

Connectors, Add Connector, Custom MCP Connector: a name without spaces or
special characters (`swisstip`) and the hosted URL; Le Chat detects that no
authentication is needed.

### Microsoft Copilot Studio

Tools, Add a tool, New tool, Model Context Protocol: a name, a description,
the hosted URL and authentication None.

### n8n

The MCP Client Tool node: the endpoint URL, Server Transport "HTTP
Streamable", Authentication None, all tools included. Node versions before
1.1 know SSE only.

## Testing and debugging

### MCP Inspector

The interface in the browser, and the same checks from the command line
(Node.js 22.19 or newer):

```shell
npx @modelcontextprotocol/inspector --server-url https://51-96-83-1.sslip.io/mcp --transport http
npx @modelcontextprotocol/inspector --cli https://51-96-83-1.sslip.io/mcp --transport http --method tools/list
npx @modelcontextprotocol/inspector --cli https://51-96-83-1.sslip.io/mcp --transport http --method tools/call --tool-name search --tool-arg query="Karton Abfuhr" --tool-arg limit=1
```

In CLI mode the endpoint comes first; always pass `--transport http`.

The server itself prints a client configuration:
`swisstip-server --print-client-config --url <endpoint>` for the generic
`mcpServers` form, and `swisstip-server --print-client-config` with
`--release` for a stdio server started from a Python environment (see
[developer setup](developer-setup.md)).

## Agent frameworks

### MCP Python SDK

Version 2, the current release of the `mcp` package:

```python
import asyncio
from mcp import Client

async def main():
    async with Client("https://51-96-83-1.sslip.io/mcp") as client:
        print([tool.name for tool in (await client.list_tools()).tools])
        result = await client.call_tool("search", {"query": "Anmeldung Zuzug Frist", "jurisdiction": {"city": "Zürich"}})
        print(result.structured_content["match_strength"])

asyncio.run(main())
```

Version 1 (`mcp<2`), with a session of its own:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    async with streamable_http_client("https://51-96-83-1.sslip.io/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search", {"query": "Anmeldung Zuzug Frist", "jurisdiction": {"city": "Zürich"}})
            print(result.structuredContent["match_strength"])

asyncio.run(main())
```

### OpenAI Agents SDK

```python
from agents import Agent
from agents.mcp import MCPServerStreamableHttp

async with MCPServerStreamableHttp(name="swiss-tip", params={"url": "https://51-96-83-1.sslip.io/mcp", "timeout": 30},
                                   cache_tools_list=True) as server:
    agent = Agent(name="Assistant", instructions="Answer questions about Switzerland with the swiss-tip tools.",
                  mcp_servers=[server])
```

`HostedMCPTool` with `server_url` set to the hosted endpoint lets OpenAI call
the server itself instead; that works with the public URL only.

### LangChain and LangGraph

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({"swiss-tip": {"transport": "http", "url": "https://51-96-83-1.sslip.io/mcp"}})
tools = await client.get_tools()
```

### Pydantic AI

Version 2 (`pydantic-ai-slim[mcp]`):

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

agent = Agent("<model>", toolsets=[MCPToolset("https://51-96-83-1.sslip.io/mcp")])
```

## stdio for any client

A client that starts its servers as local processes runs the image:

```json
{"mcpServers": {"swiss-tip": {"command": "docker", "args": ["run", "--rm", "-i", "ghcr.io/swisstip/swiss-tip:mvp-zurich", "--transport", "stdio"]}}}
```

The image carries the release, its semantic index and the embedding model,
so search is hybrid over stdio as well, with no network. A client that only
speaks stdio reaches the hosted endpoint through the bridge instead:
`npx -y mcp-remote https://51-96-83-1.sslip.io/mcp --transport http-only`.
Running the server from a Python environment, without Docker, is in
[developer setup](developer-setup.md).
