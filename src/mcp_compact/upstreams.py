"""Upstream catalog harvesting and request execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastmcp import Client
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport
from fastmcp.mcp_config import infer_transport_type_from_url

from mcp_compact.catalog import (
    ServerCatalog,
    build_resource_record,
    build_resource_template_record,
    build_tool_record,
)
from mcp_compact.config import ProxyConfig

logger = logging.getLogger(__name__)

BuildClientFn = Callable[[], Any]

__all__ = ["ConnectionPool", "UpstreamRegistry"]


class ConnectionPool:
    """Small async connection pool for request/response upstream work."""

    def __init__(
        self,
        factory: BuildClientFn,
        max_size: int = 10,
        name: str = "",
    ) -> None:
        self._factory = factory
        self._max_size = max_size
        self._name = name or "unnamed"
        self._available: asyncio.Queue[Any] = asyncio.Queue()
        self._in_use: set[Any] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    @asynccontextmanager
    async def acquire(self) -> Any:
        client = await self._get_client()
        try:
            async with client:
                yield client
        finally:
            await self._release_client(client)

    async def _get_client(self) -> Any:
        if self._closed:
            raise RuntimeError(f"Connection pool '{self._name}' is closed")

        async with self._lock:
            if not self._available.empty():
                client = await self._available.get()
                self._in_use.add(client)
                return client

            client = self._factory()
            self._in_use.add(client)
            return client

    async def _release_client(self, client: Any) -> None:
        async with self._lock:
            self._in_use.discard(client)

            if self._closed:
                await _close_client(client)
                return

            if self._available.qsize() < self._max_size:
                await self._available.put(client)
                return

            await _close_client(client)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True

            while not self._available.empty():
                client = await self._available.get()
                await _close_client(client)

            for client in list(self._in_use):
                await _close_client(client)
            self._in_use.clear()


class UpstreamRegistry:
    """Connection pools plus startup catalog harvest for enabled upstreams."""

    def __init__(
        self,
        config: ProxyConfig,
        *,
        client_factory_overrides: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._config = config
        self._client_factory_overrides = client_factory_overrides or {}
        self._pools: dict[str, ConnectionPool] = {}

    async def initialize(self) -> None:
        if self._pools:
            return

        for server_name, server_config in self._config.mcpServers.items():
            if not server_config.enabled:
                continue
            if server_name not in self._client_factory_overrides:
                server_config.validate_for_server(server_name)
            self._pools[server_name] = ConnectionPool(
                factory=self._client_builder(server_name),
                max_size=10,
                name=server_name,
            )

    async def close(self) -> None:
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()

    async def fetch_server_catalog(self, server_name: str) -> ServerCatalog:
        pool = self._pools[server_name]
        async with pool.acquire() as client:
            tools = tuple(
                sorted(
                    (
                        build_tool_record(server_name, tool)
                        for tool in await client.list_tools()
                    ),
                    key=lambda item: (item.family, item.server, item.name),
                )
            )
            resources = tuple(
                sorted(
                    (
                        build_resource_record(server_name, resource)
                        for resource in await client.list_resources()
                    ),
                    key=lambda item: (item.server, item.uri),
                )
            )
            resource_templates = tuple(
                sorted(
                    (
                        build_resource_template_record(server_name, resource_template)
                        for resource_template in await client.list_resource_templates()
                    ),
                    key=lambda item: (item.server, item.uri_template),
                )
            )

        return ServerCatalog(
            server=server_name,
            tools=tools,
            resources=resources,
            resource_templates=resource_templates,
        )

    async def call_tool(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return await client.call_tool(name, arguments or {})

    async def read_resource(self, server: str, uri: str) -> list[Any]:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return cast(list[Any], await client.read_resource(uri))

    def _client_builder(self, server_name: str) -> BuildClientFn:
        server_config = self._config.mcpServers[server_name]
        if server_name in self._client_factory_overrides:
            override = self._client_factory_overrides[server_name]

            def build_override() -> Any:
                try:
                    return override(message_handler=None)
                except TypeError:
                    return override()

            return build_override

        if server_config.type == "http":
            assert server_config.url is not None, "HTTP type requires url"
            server_url = server_config.url
            if infer_transport_type_from_url(server_url) == "sse":
                return lambda: Client(
                    SSETransport(url=server_url, headers=server_config.headers or {}),
                    auto_initialize=True,
                )
            return lambda: Client(
                StreamableHttpTransport(
                    url=server_url,
                    headers=server_config.headers or {},
                ),
                auto_initialize=True,
            )

        assert server_config.command is not None, "stdio type requires command"
        command = server_config.command
        return lambda: Client(
            StdioTransport(
                command=command,
                args=server_config.args,
                env=server_config.env or {},
            ),
            auto_initialize=True,
        )


async def _close_client(client: Any) -> None:
    try:
        await client.close()
    except Exception as exc:
        logger.debug("Error closing upstream client: %s", exc)
