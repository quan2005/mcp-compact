"""Upstream execution pools, discovery watchers and refresh coordination."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import mcp.types as mcp_types
from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport
from fastmcp.mcp_config import infer_transport_type_from_url
from pydantic import AnyUrl, RootModel, TypeAdapter

from mcpx.catalog import (
    ALL_CATEGORIES,
    Category,
    ServerCapabilitiesRecord,
    ServerCatalog,
    WatchMode,
    build_prompt_record,
    build_resource_record,
    build_resource_template_record,
    build_tool_record,
    extract_capabilities,
)
from mcpx.config import ProxyConfig
from mcpx.pool import ConnectionPool

logger = logging.getLogger(__name__)

BuildClientFn = Callable[[MessageHandler | None], Any]
RefreshCallback = Callable[[str, frozenset[Category] | None], Awaitable[None]]
ResourceUpdatedCallback = Callable[[str, str], Awaitable[None]]
TaskStatusCallback = Callable[[str, mcp_types.GetTaskResult], Awaitable[None]]
StateCallback = Callable[[str, bool, WatchMode, ServerCapabilitiesRecord | None], Awaitable[None]]

_URL_ADAPTER = TypeAdapter(AnyUrl)


class ToolTaskResponseUnion(RootModel[mcp_types.CreateTaskResult | mcp_types.CallToolResult]):
    """Union wrapper for task-augmented tool calls."""


@dataclass
class DiscoveryEvent:
    """Queued notification from an upstream watcher."""

    kind: str
    categories: frozenset[Category] = frozenset()
    uri: str | None = None
    task_status: mcp_types.GetTaskResult | None = None


@dataclass
class DiscoveryServerState:
    """Runtime state for a watched upstream server."""

    server: str
    watch_mode: WatchMode = "watcher"
    degraded: bool = False
    successful_connection: bool = False
    client: Any | None = None
    capabilities: ServerCapabilitiesRecord = field(default_factory=ServerCapabilitiesRecord)
    queue: asyncio.Queue[DiscoveryEvent] = field(default_factory=asyncio.Queue)
    subscribed_uris: dict[str, int] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None


class ExecutionPools:
    """Request/response execution pools for upstream calls."""

    def __init__(
        self,
        config: ProxyConfig,
        *,
        app_overrides: dict[str, FastMCP] | None = None,
        client_factory_overrides: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self._config = config
        self._app_overrides = app_overrides or {}
        self._client_factory_overrides = client_factory_overrides or {}
        self._builders: dict[str, BuildClientFn] = {}
        self._pools: dict[str, ConnectionPool] = {}

    def client_builder(self, server_name: str) -> BuildClientFn:
        server_config = self._config.mcpServers[server_name]
        if server_name in self._client_factory_overrides:
            override = self._client_factory_overrides[server_name]

            def build_override(message_handler: MessageHandler | None = None) -> Any:
                try:
                    return override(message_handler=message_handler)
                except TypeError:
                    try:
                        return override(message_handler)
                    except TypeError:
                        return override()

            return build_override

        app_override = self._app_overrides.get(server_name)
        if app_override is not None:
            def build_app_override(message_handler: MessageHandler | None = None) -> Any:
                return Client(
                    app_override,
                    auto_initialize=True,
                    message_handler=message_handler,
                )

            return build_app_override

        if server_config.type == "http":
            assert server_config.url is not None, "HTTP type requires url"
            server_url = server_config.url
            if infer_transport_type_from_url(server_config.url) == "sse":
                def build_sse_client(message_handler: MessageHandler | None = None) -> Any:
                    return Client(
                        SSETransport(
                            url=server_url,
                            headers=server_config.headers or {},
                        ),
                        auto_initialize=True,
                        message_handler=message_handler,
                    )

                return build_sse_client

            def build_http_client(message_handler: MessageHandler | None = None) -> Any:
                return Client(
                    StreamableHttpTransport(
                        url=server_url,
                        headers=server_config.headers or {},
                    ),
                    auto_initialize=True,
                    message_handler=message_handler,
                )

            return build_http_client

        assert server_config.command is not None, "stdio type requires command"
        command = server_config.command

        def build_stdio_client(message_handler: MessageHandler | None = None) -> Any:
            return Client(
                StdioTransport(
                    command=command,
                    args=server_config.args,
                    env=server_config.env or {},
                ),
                auto_initialize=True,
                message_handler=message_handler,
            )

        return build_stdio_client

    @property
    def builders(self) -> dict[str, BuildClientFn]:
        return dict(self._builders)

    async def initialize(self) -> None:
        if self._pools:
            return

        for server_name, server_config in self._config.mcpServers.items():
            if not server_config.enabled:
                continue
            if server_name not in self._app_overrides and server_name not in self._client_factory_overrides:
                server_config.validate_for_server(server_name)
            builder = self.client_builder(server_name)
            self._builders[server_name] = builder

            def create_client(builder: BuildClientFn = builder) -> Any:
                return builder(None)

            self._pools[server_name] = ConnectionPool(
                factory=create_client,
                max_size=10,
                name=server_name,
            )

    async def close(self) -> None:
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()
        self._builders.clear()

    async def fetch_server_catalog(
        self,
        server_name: str,
        *,
        categories: frozenset[Category] | None = None,
        base_catalog: ServerCatalog | None = None,
        degraded: bool = False,
        watch_mode: WatchMode = "watcher",
        capabilities: ServerCapabilitiesRecord | None = None,
    ) -> ServerCatalog:
        requested = categories or ALL_CATEGORIES
        existing = base_catalog or ServerCatalog.empty(server_name)
        pool = self._pools[server_name]
        async with pool.acquire() as client:
            initialize_result = getattr(client, "initialize_result", None)
            resolved_capabilities = capabilities or extract_capabilities(initialize_result)
            tools = existing.tools
            resources = existing.resources
            resource_templates = existing.resource_templates
            prompts = existing.prompts

            if "tools" in requested:
                tools = tuple(
                    build_tool_record(server_name, tool) for tool in await client.list_tools()
                )
            if "resources" in requested:
                resources = tuple(
                    build_resource_record(server_name, resource)
                    for resource in await client.list_resources()
                )
            if "resource_templates" in requested:
                resource_templates = tuple(
                    build_resource_template_record(server_name, resource_template)
                    for resource_template in await client.list_resource_templates()
                )
            if "prompts" in requested:
                prompts = tuple(
                    build_prompt_record(server_name, prompt) for prompt in await client.list_prompts()
                )

        return ServerCatalog(
            server=server_name,
            capabilities=resolved_capabilities,
            tools=tuple(sorted(tools, key=lambda item: (item.family, item.server, item.name))),
            resources=tuple(sorted(resources, key=lambda item: (item.server, item.uri))),
            resource_templates=tuple(
                sorted(resource_templates, key=lambda item: (item.server, item.uri_template))
            ),
            prompts=tuple(sorted(prompts, key=lambda item: (item.server, item.name))),
            degraded=degraded,
            watch_mode=watch_mode,
        )

    async def call_tool(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return await client.call_tool(name, arguments or {})

    async def call_tool_request(
        self,
        server: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        pool = self._pools[server]
        async with pool.acquire() as client:
            if hasattr(client, "call_tool_task_request"):
                return cast(
                    mcp_types.CreateTaskResult | mcp_types.CallToolResult,
                    await client.call_tool_task_request(name, arguments or {}, ttl),
                )

            task_metadata = mcp_types.TaskMetadata(ttl=ttl) if ttl is not None else None
            request = mcp_types.CallToolRequest(
                params=mcp_types.CallToolRequestParams(
                    name=name,
                    arguments=arguments or {},
                    task=task_metadata,
                )
            )
            wrapped_result = await client._await_with_session_monitoring(
                client.session.send_request(
                    request=request,
                    result_type=ToolTaskResponseUnion,
                )
            )
            return cast(
                mcp_types.CreateTaskResult | mcp_types.CallToolResult,
                wrapped_result.root,
            )

    async def read_resource(self, server: str, uri: str) -> list[Any]:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return cast(list[Any], await client.read_resource(uri))

    async def get_prompt(
        self, server: str, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.GetPromptResult:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return cast(mcp_types.GetPromptResult, await client.get_prompt(name, arguments or {}))

    async def complete(
        self,
        server: str,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return cast(
                mcp_types.Completion,
                await client.complete(ref, argument, context_arguments=context_arguments),
            )

    async def get_task_status(self, server: str, task_id: str) -> mcp_types.GetTaskResult:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return cast(mcp_types.GetTaskResult, await client.get_task_status(task_id))

    async def get_task_result(self, server: str, task_id: str) -> dict[str, Any]:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return cast(dict[str, Any], await client.get_task_result(task_id))

    async def cancel_task(self, server: str, task_id: str) -> mcp_types.CancelTaskResult:
        pool = self._pools[server]
        async with pool.acquire() as client:
            return cast(mcp_types.CancelTaskResult, await client.cancel_task(task_id))


class RefreshCoordinator:
    """Debounced partial/full refresh scheduling."""

    def __init__(
        self,
        refresh_callback: RefreshCallback,
        *,
        debounce_ms: int = 200,
    ) -> None:
        self._refresh_callback = refresh_callback
        self._debounce_seconds = debounce_ms / 1000
        self._pending: dict[str, frozenset[Category] | None] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, server: str, categories: frozenset[Category] | None) -> None:
        current = self._pending.get(server)
        if current is None or categories is None:
            self._pending[server] = categories
        else:
            self._pending[server] = frozenset(set(current) | set(categories))

        task = self._tasks.get(server)
        if task is not None:
            task.cancel()
        self._tasks[server] = asyncio.create_task(self._run(server))

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._pending.clear()

    async def _run(self, server: str) -> None:
        try:
            await asyncio.sleep(self._debounce_seconds)
            categories = self._pending.pop(server, None)
            await self._refresh_callback(server, categories)
        except asyncio.CancelledError:
            raise
        finally:
            self._tasks.pop(server, None)


class DiscoveryMessageHandler(MessageHandler):
    """Routes upstream notifications into the discovery queue."""

    def __init__(self, queue: asyncio.Queue[DiscoveryEvent]) -> None:
        super().__init__()
        self._queue = queue

    async def on_tool_list_changed(
        self, message: mcp_types.ToolListChangedNotification
    ) -> None:
        await self._queue.put(DiscoveryEvent(kind="list_changed", categories=frozenset({"tools"})))

    async def on_resource_list_changed(
        self, message: mcp_types.ResourceListChangedNotification
    ) -> None:
        await self._queue.put(
            DiscoveryEvent(kind="list_changed", categories=frozenset({"resources", "resource_templates"}))
        )

    async def on_prompt_list_changed(
        self, message: mcp_types.PromptListChangedNotification
    ) -> None:
        await self._queue.put(DiscoveryEvent(kind="list_changed", categories=frozenset({"prompts"})))

    async def on_resource_updated(
        self, message: mcp_types.ResourceUpdatedNotification
    ) -> None:
        await self._queue.put(
            DiscoveryEvent(kind="resource_updated", uri=str(message.params.uri))
        )

    async def on_notification(self, message: mcp_types.ServerNotification) -> None:
        if isinstance(message.root, mcp_types.TaskStatusNotification):
            status = mcp_types.GetTaskResult.model_validate(message.root.params.model_dump())
            await self._queue.put(DiscoveryEvent(kind="task_status", task_status=status))


class DiscoveryHub:
    """Long-lived discovery watchers for upstream notifications."""

    def __init__(
        self,
        builders: dict[str, BuildClientFn],
        *,
        on_refresh: RefreshCallback,
        on_resource_updated: ResourceUpdatedCallback,
        on_task_status: TaskStatusCallback,
        on_state_change: StateCallback,
    ) -> None:
        self._builders = builders
        self._on_refresh = on_refresh
        self._on_resource_updated = on_resource_updated
        self._on_task_status = on_task_status
        self._on_state_change = on_state_change
        self._stop_event = asyncio.Event()
        self._states = {
            server: DiscoveryServerState(server=server) for server in sorted(builders)
        }

    @property
    def states(self) -> dict[str, DiscoveryServerState]:
        return self._states

    async def start(self) -> None:
        if not self._states:
            return
        self._stop_event.clear()
        for state in self._states.values():
            if state.task is None or state.task.done():
                state.task = asyncio.create_task(self._watch_server(state))

    async def close(self) -> None:
        self._stop_event.set()
        for state in self._states.values():
            if state.task is not None:
                state.task.cancel()
        for state in self._states.values():
            if state.task is None:
                continue
            try:
                await state.task
            except asyncio.CancelledError:
                pass
            state.task = None
            if state.client is not None and hasattr(state.client, "close"):
                await _maybe_await(state.client.close())
            state.client = None

    def has_active_client(self, server: str) -> bool:
        return self._states[server].client is not None

    async def call_tool_task_request(
        self,
        server: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        state = self._states[server]
        client = state.client
        if client is None:
            raise RuntimeError(f"No active discovery client for server '{server}'")
        if hasattr(client, "call_tool_task_request"):
            return cast(
                mcp_types.CreateTaskResult | mcp_types.CallToolResult,
                await client.call_tool_task_request(name, arguments or {}, ttl),
            )

        task_metadata = mcp_types.TaskMetadata(ttl=ttl) if ttl is not None else None
        request = mcp_types.CallToolRequest(
            params=mcp_types.CallToolRequestParams(
                name=name,
                arguments=arguments or {},
                task=task_metadata,
            )
        )
        wrapped_result = await client._await_with_session_monitoring(
            client.session.send_request(
                request=request,
                result_type=ToolTaskResponseUnion,
            )
        )
        return cast(
            mcp_types.CreateTaskResult | mcp_types.CallToolResult,
            wrapped_result.root,
        )

    async def subscribe_resource(self, server: str, uri: str) -> None:
        state = self._states[server]
        state.subscribed_uris[uri] = state.subscribed_uris.get(uri, 0) + 1
        if not state.capabilities.resources_subscribe or state.client is None:
            return
        await state.client.session.subscribe_resource(_URL_ADAPTER.validate_python(uri))

    async def unsubscribe_resource(self, server: str, uri: str) -> None:
        state = self._states[server]
        if uri not in state.subscribed_uris:
            return
        if state.subscribed_uris[uri] <= 1:
            del state.subscribed_uris[uri]
        else:
            state.subscribed_uris[uri] -= 1
            return

        if not state.capabilities.resources_subscribe or state.client is None:
            return
        await state.client.session.unsubscribe_resource(_URL_ADAPTER.validate_python(uri))

    async def _watch_server(self, state: DiscoveryServerState) -> None:
        backoff = (1, 2, 5, 10, 30)
        attempts = 0
        while not self._stop_event.is_set():
            handler = DiscoveryMessageHandler(state.queue)
            client = self._builders[state.server](handler)
            try:
                async with client:
                    state.client = client
                    state.capabilities = extract_capabilities(getattr(client, "initialize_result", None))
                    state.degraded = False
                    state.watch_mode = "watcher"
                    state.successful_connection = True
                    attempts = 0
                    await self._restore_subscriptions(state)
                    await self._on_state_change(
                        state.server,
                        False,
                        "watcher",
                        state.capabilities,
                    )
                    await self._on_refresh(state.server, None)
                    while not self._stop_event.is_set():
                        try:
                            event = await asyncio.wait_for(state.queue.get(), timeout=15)
                        except asyncio.TimeoutError:
                            await client.ping()
                            continue
                        await self._handle_event(state.server, event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Discovery watcher failed for '%s': %s", state.server, exc)
                state.client = None
                state.degraded = True
                attempts += 1
                await self._on_state_change(state.server, True, state.watch_mode, state.capabilities)
                if not state.successful_connection and attempts >= 3:
                    await self._poll_server(state)
                    return
                await asyncio.sleep(backoff[min(attempts - 1, len(backoff) - 1)])

    async def _poll_server(self, state: DiscoveryServerState) -> None:
        state.watch_mode = "poll"
        while not self._stop_event.is_set():
            try:
                await self._on_state_change(state.server, state.degraded, "poll", state.capabilities)
                await self._on_refresh(state.server, None)
                state.degraded = False
                await self._on_state_change(state.server, False, "poll", state.capabilities)
            except Exception as exc:
                logger.warning("Polling refresh failed for '%s': %s", state.server, exc)
                state.degraded = True
                await self._on_state_change(state.server, True, "poll", state.capabilities)
            await asyncio.sleep(30)

    async def _restore_subscriptions(self, state: DiscoveryServerState) -> None:
        if not state.capabilities.resources_subscribe or state.client is None:
            return
        for uri in state.subscribed_uris:
            await state.client.session.subscribe_resource(_URL_ADAPTER.validate_python(uri))

    async def _handle_event(self, server: str, event: DiscoveryEvent) -> None:
        if event.kind == "list_changed":
            await self._on_refresh(server, event.categories)
            return
        if event.kind == "resource_updated" and event.uri is not None:
            await self._on_resource_updated(server, event.uri)
            return
        if event.kind == "task_status" and event.task_status is not None:
            await self._on_task_status(server, event.task_status)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
