"""MCPX 2.1 orchestration runtime."""

from __future__ import annotations

import logging
from typing import Any, Protocol

import mcp.types as mcp_types
from fastmcp import FastMCP

from mcpx.catalog import Category, ServerCatalog, WatchMode
from mcpx.compiler import ProjectionBudget, ProjectionCompiler
from mcpx.execution import ExecutionRouter
from mcpx.resolver import Resolver
from mcpx.snapshot import CatalogSnapshot, SnapshotBuilder
from mcpx.upstreams import DiscoveryHub, ExecutionPools, RefreshCoordinator

logger = logging.getLogger(__name__)

__all__ = ["MCPXRuntime", "Surface"]


class Surface(Protocol):
    """Surface protocol registered against the runtime."""

    surface_kind: str

    async def sync(self, snapshot: CatalogSnapshot) -> None:
        """Apply a freshly-built snapshot to the surface."""


class MCPXRuntime:
    """2.1 orchestrator shared by projection and native surfaces."""

    def __init__(
        self,
        config: Any,
        *,
        app_overrides: dict[str, FastMCP] | None = None,
        client_factory_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self._version = 0
        self._snapshot: CatalogSnapshot | None = None
        self._surfaces: list[Surface] = []
        self._native_surface: Any | None = None
        self._server_catalogs = {
            server_name: ServerCatalog.empty(server_name)
            for server_name, server_config in sorted(config.mcpServers.items())
            if server_config.enabled
        }

        self._snapshot_builder = SnapshotBuilder()
        self._compiler = ProjectionCompiler(ProjectionBudget())
        self._resolver = Resolver()
        self._execution_pools = ExecutionPools(
            config,
            app_overrides=app_overrides,
            client_factory_overrides=client_factory_overrides,
        )
        self._refresh_coordinator = RefreshCoordinator(self.refresh_server)
        self._discovery_hub: DiscoveryHub | None = None
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

        await self._execution_pools.initialize()
        for server_name in sorted(self._server_catalogs):
            try:
                self._server_catalogs[server_name] = await self._execution_pools.fetch_server_catalog(
                    server_name,
                    base_catalog=self._server_catalogs[server_name],
                )
            except Exception as exc:
                logger.warning("Initial catalog harvest failed for '%s': %s", server_name, exc)
        await self._rebuild_snapshot()

        self._discovery_hub = DiscoveryHub(
            self._execution_pools.builders,
            on_refresh=self.refresh_server,
            on_resource_updated=self.handle_upstream_resource_updated,
            on_task_status=self.handle_upstream_task_status,
            on_state_change=self.apply_server_state,
        )
        await self._discovery_hub.start()
        self._initialized = True

    async def close(self) -> None:
        await self._refresh_coordinator.close()
        if self._discovery_hub is not None:
            await self._discovery_hub.close()
        await self._execution_pools.close()
        self._initialized = False

    async def refresh(self) -> CatalogSnapshot:
        for server_name in sorted(self._server_catalogs):
            self._server_catalogs[server_name] = await self._execution_pools.fetch_server_catalog(
                server_name,
                base_catalog=self._server_catalogs[server_name],
            )
        return await self._rebuild_snapshot()

    def register_surface(self, surface: Surface) -> None:
        self._surfaces.append(surface)
        if surface.surface_kind == "native":
            self._native_surface = surface

    async def refresh_server(
        self, server_name: str, categories: frozenset[Category] | None
    ) -> None:
        if server_name not in self._server_catalogs:
            return

        base_catalog = self._server_catalogs[server_name]
        capabilities = base_catalog.capabilities
        degraded = base_catalog.degraded
        watch_mode = base_catalog.watch_mode
        if self._discovery_hub is not None:
            state = self._discovery_hub.states.get(server_name)
            if state is not None:
                capabilities = state.capabilities
                degraded = state.degraded
                watch_mode = state.watch_mode

        refreshed = await self._execution_pools.fetch_server_catalog(
            server_name,
            categories=categories,
            base_catalog=base_catalog,
            degraded=degraded,
            watch_mode=watch_mode,
            capabilities=capabilities,
        )
        self._server_catalogs[server_name] = refreshed
        await self._rebuild_snapshot()

        if (
            categories is not None
            and "resources" in categories
            and self._native_surface is not None
            and not refreshed.capabilities.resources_subscribe
        ):
            await self._native_surface.registry.notify_server_resource_updates(server_name)

    async def apply_server_state(
        self,
        server_name: str,
        degraded: bool,
        watch_mode: WatchMode,
        capabilities: Any | None,
    ) -> None:
        base_catalog = self._server_catalogs.get(server_name)
        if base_catalog is None:
            return

        updated = base_catalog.with_updates(
            capabilities=capabilities if capabilities is not None else base_catalog.capabilities,
            degraded=degraded,
            watch_mode=watch_mode,
        )
        if updated == base_catalog:
            return
        self._server_catalogs[server_name] = updated
        if self._snapshot is not None:
            await self._rebuild_snapshot()

    async def handle_upstream_resource_updated(self, server_name: str, uri: str) -> None:
        if self._native_surface is None or self._snapshot is None:
            return
        resource = self._snapshot.resource(server_name, uri)
        if resource is None:
            return
        await self._native_surface.registry.notify_resource_updated(resource.exposed_uri)

    async def handle_upstream_task_status(
        self, server_name: str, status: mcp_types.GetTaskResult
    ) -> None:
        if self._native_surface is None:
            return
        await self._native_surface.registry.update_task_status(
            server=server_name,
            upstream_status=status,
        )

    async def subscribe_resource(self, server: str, uri: str) -> None:
        if self._discovery_hub is None:
            return
        await self._discovery_hub.subscribe_resource(server, uri)

    async def unsubscribe_resource(self, server: str, uri: str) -> None:
        if self._discovery_hub is None:
            return
        await self._discovery_hub.unsubscribe_resource(server, uri)

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

    async def native_call_tool(
        self,
        server: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        if ttl is not None and self._discovery_hub is not None and self._discovery_hub.has_active_client(server):
            try:
                return await self._discovery_hub.call_tool_task_request(
                    server,
                    name,
                    arguments or {},
                    ttl,
                )
            except Exception as exc:
                logger.warning("Task-augmented watcher call failed for '%s.%s': %s", server, name, exc)
        return await self._execution_router.native_call_tool(server, name, arguments or {}, ttl=ttl)

    async def native_read_resource(self, server: str, uri: str) -> list[Any]:
        return await self._execution_router.native_read_resource(server, uri)

    async def native_get_prompt(
        self, server: str, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.GetPromptResult:
        return await self._execution_router.native_get_prompt(server, name, arguments or {})

    async def native_complete(
        self,
        server: str,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        return await self._execution_router.native_complete(server, ref, argument, context_arguments)

    async def native_get_task_status(self, server: str, task_id: str) -> mcp_types.GetTaskResult:
        return await self._execution_router.native_get_task_status(server, task_id)

    async def native_get_task_result(self, server: str, task_id: str) -> dict[str, Any]:
        return await self._execution_router.native_get_task_result(server, task_id)

    async def native_cancel_task(self, server: str, task_id: str) -> mcp_types.CancelTaskResult:
        return await self._execution_router.native_cancel_task(server, task_id)

    async def call_tool(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self._execution_pools.call_tool(server, name, arguments or {})

    async def call_tool_request(
        self,
        server: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        return await self._execution_pools.call_tool_request(server, name, arguments or {}, ttl)

    async def read_resource(self, server: str, uri: str) -> list[Any]:
        return await self._execution_pools.read_resource(server, uri)

    async def get_prompt(
        self, server: str, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.GetPromptResult:
        return await self._execution_pools.get_prompt(server, name, arguments or {})

    async def complete(
        self,
        server: str,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        return await self._execution_pools.complete(server, ref, argument, context_arguments)

    async def get_task_status(self, server: str, task_id: str) -> mcp_types.GetTaskResult:
        return await self._execution_pools.get_task_status(server, task_id)

    async def get_task_result(self, server: str, task_id: str) -> dict[str, Any]:
        return await self._execution_pools.get_task_result(server, task_id)

    async def cancel_task(self, server: str, task_id: str) -> mcp_types.CancelTaskResult:
        return await self._execution_pools.cancel_task(server, task_id)

    async def _rebuild_snapshot(self) -> CatalogSnapshot:
        self._version += 1
        self._snapshot = self._snapshot_builder.build(self._version, self._server_catalogs)
        for surface in self._surfaces:
            await surface.sync(self._snapshot)
        return self._snapshot
