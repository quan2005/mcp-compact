# MCP Compact

MCP Compact aggregates multiple upstream MCP servers into one stdio-only runtime with a single compact surface.

## What It Is

MCP Compact runs as one MCP server over `stdio` and exposes only `invoke` and `read`.

## Use From An MCP Client

MCP Compact runs as a stdio MCP server. It does not expose HTTP or `/mcp`.

Configure your MCP client to launch it like this:

```json
{
  "mcpServers": {
    "mcp_compact": {
      "command": "uv",
      "args": ["run", "mcp-compact", "config.example.json"]
    }
  }
}
```

If you want to start it manually, run:

```bash
uv run mcp-compact config.example.json
```

## Upstream Config

`config.example.json` is the config file consumed by `mcp-compact` itself:

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
