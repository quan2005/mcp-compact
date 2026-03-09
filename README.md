# MCP Compact

MCP Compact aggregates multiple upstream MCP servers into one stdio-only runtime with a single compact surface.

## What It Is

MCP Compact runs as one MCP server over `stdio` and exposes only `invoke` and `read`.

## Run

```bash
uv run mcp-compact config.example.json
```

The process speaks MCP on stdin/stdout. It does not expose HTTP or `/mcp`.

## Config

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

All upstreams must use `stdio`.

## Test

```bash
uv run ruff check src/mcp_compact tests
uv run mypy src/mcp_compact
uv run pytest tests/ -q
```
