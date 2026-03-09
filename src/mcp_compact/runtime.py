"""Single-surface runtime for MCP Compact."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from mcp_compact.catalog import CatalogSnapshot, ServerCatalog, build_snapshot
from mcp_compact.projection import ExecutionRouter, ProjectionBudget, ProjectionCompiler, Resolver
from mcp_compact.upstreams import UpstreamRegistry

logger = logging.getLogger(__name__)

__all__ = ["MCPCompactRuntime", "Surface"]


class Surface(Protocol):
    """Surface protocol registered against the runtime."""

    surface_kind: str

    async def sync(self, snapshot: CatalogSnapshot) -> None:
        """Apply a freshly-built snapshot to the surface."""


class MCPCompactRuntime:
    """Projection-only orchestrator."""

    def __init__(
        self,
        config: Any,
        *,
        client_factory_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self._version = 0
        self._snapshot: CatalogSnapshot | None = None
        self._surfaces: list[Surface] = []
        self._server_catalogs = {
            server_name: ServerCatalog.empty(server_name)
            for server_name, server_config in sorted(config.mcpServers.items())
            if server_config.enabled
        }
        self._upstreams = UpstreamRegistry(
            config,
            client_factory_overrides=client_factory_overrides,
        )
        self._compiler = ProjectionCompiler(ProjectionBudget())
        self._resolver = Resolver()
        self._execution_router = ExecutionRouter(
            backend=self,
            resolver=self._resolver,
            snapshot_provider=lambda: self.snapshot,
        )
        self._initialized = False

    @property
    def snapshot(self) -> CatalogSnapshot:
        if self._snapshot is None:
            raise RuntimeError("Runtime not initialized")
        return self._snapshot

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._upstreams.initialize()
        await self.refresh()
        self._initialized = True

    async def close(self) -> None:
        await self._upstreams.close()
        self._initialized = False

    async def refresh(self) -> CatalogSnapshot:
        for server_name in sorted(self._server_catalogs):
            try:
                self._server_catalogs[server_name] = await self._upstreams.fetch_server_catalog(
                    server_name
                )
            except Exception as exc:
                logger.warning("Catalog harvest failed for '%s': %s", server_name, exc)
        return await self._rebuild_snapshot()

    def register_surface(self, surface: Surface) -> None:
        self._surfaces.append(surface)

    def compile_invoke_description(self) -> str:
        return self._compiler.compile_invoke_description(self.snapshot)

    def compile_read_description(self) -> str:
        return self._compiler.compile_read_description(self.snapshot)

    async def invoke(
        self,
        ref: dict[str, Any] | None,
        arguments: dict[str, Any] | None = None,
        *,
        mode: str = "call",
    ) -> dict[str, Any]:
        return await self._execution_router.invoke(ref, arguments, mode=mode)

    async def read(
        self,
        ref: dict[str, Any] | None,
        *,
        mode: str = "read",
    ) -> dict[str, Any]:
        return await self._execution_router.read(ref, mode=mode)

    async def call_tool(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self._upstreams.call_tool(server, name, arguments or {})

    async def read_resource(self, server: str, uri: str) -> list[Any]:
        return await self._upstreams.read_resource(server, uri)

    async def _rebuild_snapshot(self) -> CatalogSnapshot:
        self._version += 1
        self._snapshot = build_snapshot(self._version, self._server_catalogs)
        for surface in self._surfaces:
            await surface.sync(self._snapshot)
        return self._snapshot
