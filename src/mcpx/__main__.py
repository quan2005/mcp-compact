"""MCPX 2.1 entrypoints."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

from mcpx.config import McpServerConfig, ProxyConfig
from mcpx.runtime import MCPXRuntime
from mcpx.surfaces import NativeSurface, ProjectionSurface

logger = logging.getLogger(__name__)

__all__ = [
    "McpServerConfig",
    "ProxyConfig",
    "MCPXRuntime",
    "create_http_app",
    "create_native_server",
    "create_projection_server",
    "create_server",
    "load_config",
    "main",
]


def load_config(config_path: Path) -> ProxyConfig:
    """Load MCPX configuration from disk."""
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    try:
        with open(config_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in config file: %s", exc)
        sys.exit(1)

    try:
        return ProxyConfig(**data)
    except Exception as exc:
        logger.error("Invalid config structure: %s", exc)
        sys.exit(1)


def _sync_surface_if_ready(runtime: MCPXRuntime, surface: Any) -> None:
    try:
        snapshot = runtime.snapshot
    except RuntimeError:
        return

    if hasattr(surface, "apply_snapshot"):
        surface.apply_snapshot(snapshot)


def create_projection_server(
    config: ProxyConfig,
    *,
    runtime: MCPXRuntime | None = None,
    app_overrides: dict[str, FastMCP] | None = None,
) -> FastMCP:
    """Create the agent-facing projection surface."""
    active_runtime = runtime or MCPXRuntime(config, app_overrides=app_overrides)
    surface = ProjectionSurface(active_runtime)
    _sync_surface_if_ready(active_runtime, surface)
    return surface.server


def create_native_server(
    config: ProxyConfig,
    *,
    runtime: MCPXRuntime | None = None,
    app_overrides: dict[str, FastMCP] | None = None,
) -> NativeSurface:
    """Create the native aggregated MCP surface."""
    active_runtime = runtime or MCPXRuntime(config, app_overrides=app_overrides)
    surface = NativeSurface(active_runtime)
    _sync_surface_if_ready(active_runtime, surface)
    return surface


def create_server(
    config: ProxyConfig,
    runtime: MCPXRuntime | None = None,
    registry: Any = None,
) -> Any:
    """Deprecated alias for the projection surface."""
    active_runtime = runtime or registry
    if active_runtime is not None and not isinstance(active_runtime, MCPXRuntime):
        raise TypeError("create_server expects MCPXRuntime for the runtime/registry parameter")
    return create_projection_server(config, runtime=active_runtime)


def create_http_app(
    config: ProxyConfig,
    *,
    runtime: MCPXRuntime | None = None,
) -> Starlette:
    """Create an ASGI app that serves both projection and native surfaces."""
    active_runtime = runtime or MCPXRuntime(config)
    projection = create_projection_server(config, runtime=active_runtime)
    native = create_native_server(config, runtime=active_runtime)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with native.lifespan():
            await active_runtime.initialize()
            try:
                yield
            finally:
                await active_runtime.close()

    return Starlette(
        routes=[
            Mount("/mcp", app=projection.http_app(path="/")),
            Mount("/native", app=native),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="MCPX 2.1 dual-surface proxy")
    parser.add_argument("config", type=Path, help="Path to config.json")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    config = load_config(args.config)
    app = create_http_app(config)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
