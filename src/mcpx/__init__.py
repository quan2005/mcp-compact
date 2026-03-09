"""MCPX 2.1 public API."""

from __future__ import annotations

from mcpx.__main__ import (
    McpServerConfig,
    MCPXRuntime,
    ProxyConfig,
    create_http_app,
    create_native_server,
    create_projection_server,
    create_server,
    load_config,
)
from mcpx.catalog import (
    PromptRecord,
    ResourceRecord,
    ResourceTemplateRecord,
    ServerCapabilitiesRecord,
    ServerCatalog,
    ToolRecord,
)
from mcpx.snapshot import CatalogSnapshot
from mcpx.upstreams import DiscoveryHub, ExecutionPools, RefreshCoordinator

__all__ = [
    "CatalogSnapshot",
    "DiscoveryHub",
    "ExecutionPools",
    "MCPXRuntime",
    "McpServerConfig",
    "PromptRecord",
    "ProxyConfig",
    "RefreshCoordinator",
    "ResourceRecord",
    "ResourceTemplateRecord",
    "ServerCapabilitiesRecord",
    "ServerCatalog",
    "ToolRecord",
    "create_http_app",
    "create_native_server",
    "create_projection_server",
    "create_server",
    "load_config",
]
