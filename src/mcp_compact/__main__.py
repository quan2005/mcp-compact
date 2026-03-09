"""MCP Compact entrypoints."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from mcp_compact.config import McpServerConfig, ProxyConfig
from mcp_compact.projection import ProjectionSurface
from mcp_compact.runtime import MCPCompactRuntime

logger = logging.getLogger(__name__)

__all__ = [
    "McpServerConfig",
    "ProxyConfig",
    "MCPCompactRuntime",
    "create_projection_server",
    "load_config",
    "main",
]


def load_config(config_path: Path) -> ProxyConfig:
    """Load MCP Compact configuration from disk."""
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


def _sync_surface_if_ready(runtime: MCPCompactRuntime, surface: Any) -> None:
    try:
        snapshot = runtime.snapshot
    except RuntimeError:
        return

    if hasattr(surface, "apply_snapshot"):
        surface.apply_snapshot(snapshot)


def create_projection_server(
    config: ProxyConfig,
    *,
    runtime: MCPCompactRuntime | None = None,
) -> Any:
    """Create the agent-facing projection surface."""
    active_runtime = runtime or MCPCompactRuntime(config)
    surface = ProjectionSurface(active_runtime)
    _sync_surface_if_ready(active_runtime, surface)
    return surface.server


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="MCP Compact stdio runtime")
    parser.add_argument("config", type=Path, help="Path to a config file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    config = load_config(args.config)
    server = create_projection_server(config)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
