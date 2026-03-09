# MCP Compact

MCP Compact aggregates multiple upstream MCP servers into one runtime with a single compact surface.

## What It Is

MCP Compact serves a single agent-facing MCP endpoint that exposes only `invoke` and `read`.

## Run

```bash
uv run mcp-compact config.example.json
```

## Config

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

## Surface

- `/mcp` exposes only `invoke` and `read`

## Test

```bash
uv run ruff check src/mcp_compact tests
uv run mypy src/mcp_compact
uv run pytest tests/ -q
```
