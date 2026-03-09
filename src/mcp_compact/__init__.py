"""MCP Compact public API."""

from __future__ import annotations

from mcp_compact.__main__ import (
    MCPCompactRuntime,
    McpServerConfig,
    ProxyConfig,
    create_http_app,
    create_projection_server,
    load_config,
)

__all__ = [
    "MCPCompactRuntime",
    "McpServerConfig",
    "ProxyConfig",
    "create_http_app",
    "create_projection_server",
    "load_config",
]
