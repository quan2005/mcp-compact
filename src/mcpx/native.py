"""Low-level native MCP surface."""
# mypy: disable-error-code="arg-type,no-any-return,no-untyped-call,untyped-decorator"

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import mcp.types as mcp_types
from mcp.server import NotificationOptions, Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.session import ServerSession
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl, TypeAdapter
from starlette.types import Receive, Scope, Send

from mcpx.catalog import ServerCapabilitiesRecord
from mcpx.snapshot import CatalogSnapshot

__all__ = ["AggregatedTaskRecord", "NativeSessionRegistry", "NativeSurface"]

_URI_ADAPTER = TypeAdapter(AnyUrl)


@dataclass
class AggregatedTaskRecord:
    """Proxy task metadata tracked by the native surface."""

    native_task_id: str
    server: str
    upstream_task_id: str
    tool_name: str
    session: ServerSession | None
    status: mcp_types.Task


class NativeSessionRegistry:
    """Tracks native sessions, subscriptions and task ownership."""

    def __init__(self) -> None:
        self._sessions: dict[int, ServerSession] = {}
        self._subscriptions_by_uri: dict[str, set[int]] = {}
        self._task_records: dict[str, AggregatedTaskRecord] = {}

    def register(self, session: ServerSession) -> None:
        self._sessions[id(session)] = session

    def unregister(self, session: ServerSession) -> None:
        session_id = id(session)
        self._sessions.pop(session_id, None)

        empty_uris: list[str] = []
        for exposed_uri, subscribers in self._subscriptions_by_uri.items():
            subscribers.discard(session_id)
            if not subscribers:
                empty_uris.append(exposed_uri)
        for exposed_uri in empty_uris:
            self._subscriptions_by_uri.pop(exposed_uri, None)

        for record in self._task_records.values():
            if record.session is not None and id(record.session) == session_id:
                record.session = None

    def subscribe(self, session: ServerSession, exposed_uri: str) -> bool:
        self.register(session)
        subscribers = self._subscriptions_by_uri.setdefault(exposed_uri, set())
        previous_count = len(subscribers)
        subscribers.add(id(session))
        return previous_count == 0

    def unsubscribe(self, session: ServerSession, exposed_uri: str) -> bool:
        subscribers = self._subscriptions_by_uri.get(exposed_uri)
        if subscribers is None:
            return False
        subscribers.discard(id(session))
        if not subscribers:
            self._subscriptions_by_uri.pop(exposed_uri, None)
            return True
        return False

    async def broadcast_tool_list_changed(self) -> None:
        for session in list(self._sessions.values()):
            try:
                await session.send_tool_list_changed()
            except Exception:
                self.unregister(session)

    async def broadcast_resource_list_changed(self) -> None:
        for session in list(self._sessions.values()):
            try:
                await session.send_resource_list_changed()
            except Exception:
                self.unregister(session)

    async def broadcast_prompt_list_changed(self) -> None:
        for session in list(self._sessions.values()):
            try:
                await session.send_prompt_list_changed()
            except Exception:
                self.unregister(session)

    async def notify_resource_updated(self, exposed_uri: str) -> None:
        subscribers = self._subscriptions_by_uri.get(exposed_uri, set())
        if not subscribers:
            return

        validated_uri = _URI_ADAPTER.validate_python(exposed_uri)
        for session_id in list(subscribers):
            session = self._sessions.get(session_id)
            if session is None:
                continue
            try:
                await session.send_resource_updated(validated_uri)
            except Exception:
                self.unregister(session)

    async def notify_server_resource_updates(self, server: str) -> None:
        prefix = f"mcpx://{server}/"
        for exposed_uri in list(self._subscriptions_by_uri):
            if exposed_uri.startswith(prefix):
                await self.notify_resource_updated(exposed_uri)

    def register_task(
        self,
        *,
        server: str,
        tool_name: str,
        upstream_result: mcp_types.CreateTaskResult,
        session: ServerSession | None,
    ) -> mcp_types.CreateTaskResult:
        native_task_id = self._native_task_id(server, upstream_result.task.taskId)
        native_task = _rewrite_task_identifier(upstream_result.task, native_task_id)
        self._task_records[native_task_id] = AggregatedTaskRecord(
            native_task_id=native_task_id,
            server=server,
            upstream_task_id=upstream_result.task.taskId,
            tool_name=tool_name,
            session=session,
            status=native_task,
        )
        return mcp_types.CreateTaskResult(task=native_task)

    def get_task(self, native_task_id: str) -> AggregatedTaskRecord | None:
        return self._task_records.get(native_task_id)

    def list_tasks(self) -> list[mcp_types.Task]:
        return [record.status for record in self._task_records.values()]

    async def update_task_status(
        self,
        *,
        server: str,
        upstream_status: mcp_types.GetTaskResult,
    ) -> None:
        native_task_id = self._native_task_id(server, upstream_status.taskId)
        record = self._task_records.get(native_task_id)
        if record is None:
            return

        native_status = self.rewrite_get_task_result(server, upstream_status)
        record.status = _task_from_status(native_status)
        if record.session is None:
            return

        params = mcp_types.TaskStatusNotificationParams.model_validate(
            native_status.model_dump(mode="json", by_alias=True)
        )
        try:
            await record.session.send_notification(
                mcp_types.ServerNotification(
                    mcp_types.TaskStatusNotification(params=params)
                )
            )
        except Exception:
            self.unregister(record.session)

    def rewrite_get_task_result(
        self, server: str, status: mcp_types.GetTaskResult
    ) -> mcp_types.GetTaskResult:
        native_task_id = self._native_task_id(server, status.taskId)
        return mcp_types.GetTaskResult.model_validate(
            {
                **status.model_dump(mode="json", by_alias=True),
                "taskId": native_task_id,
            }
        )

    def rewrite_cancel_result(
        self, server: str, result: mcp_types.CancelTaskResult
    ) -> mcp_types.CancelTaskResult:
        native_task_id = self._native_task_id(server, result.taskId)
        return mcp_types.CancelTaskResult.model_validate(
            {
                **result.model_dump(mode="json", by_alias=True),
                "taskId": native_task_id,
            }
        )

    def update_task_from_cancel(
        self, server: str, result: mcp_types.CancelTaskResult
    ) -> mcp_types.CancelTaskResult:
        rewritten = self.rewrite_cancel_result(server, result)
        record = self._task_records.get(rewritten.taskId)
        if record is not None:
            record.status = _task_from_status(rewritten)
        return rewritten

    def _native_task_id(self, server: str, upstream_task_id: str) -> str:
        return f"{server}:{upstream_task_id}"


class NativeSurface:
    """Protocol-faithful low-level native MCP surface."""

    surface_kind = "native"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._notification_options = NotificationOptions(
            prompts_changed=True,
            resources_changed=True,
            tools_changed=True,
        )
        self.server: Server[Any, Any] = Server("MCPX Native")
        self._install_initialization_options()
        self.registry = NativeSessionRegistry()
        self.session_manager = StreamableHTTPSessionManager(self.server)
        self._snapshot: CatalogSnapshot | None = None
        self.runtime.register_surface(self)
        self._register_handlers()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.session_manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self.session_manager.run():
            yield

    def apply_snapshot(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot = snapshot

    async def sync(self, snapshot: CatalogSnapshot) -> None:
        previous = self._snapshot
        self._snapshot = snapshot
        if previous is None:
            return
        if _tool_keys(previous) != _tool_keys(snapshot):
            await self.registry.broadcast_tool_list_changed()
        if _resource_keys(previous) != _resource_keys(snapshot):
            await self.registry.broadcast_resource_list_changed()
            changed_servers = {
                resource.server
                for resource in snapshot.native_index.resources
                if resource.exposed_uri not in _resource_keys(previous)
            } | {
                resource.server
                for resource in previous.native_index.resources
                if resource.exposed_uri not in _resource_keys(snapshot)
            }
            for server in changed_servers:
                await self.registry.notify_server_resource_updates(server)
        if _prompt_keys(previous) != _prompt_keys(snapshot):
            await self.registry.broadcast_prompt_list_changed()

    def _install_initialization_options(self) -> None:
        original = self.server.create_initialization_options

        def create_initialization_options(
            notification_options: NotificationOptions | None = None,
            experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        ) -> Any:
            return original(
                notification_options=notification_options or self._notification_options,
                experimental_capabilities=experimental_capabilities,
            )

        self.server.create_initialization_options = create_initialization_options  # type: ignore[method-assign]

    def _register_handlers(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[mcp_types.Tool]:
            snapshot = self._require_snapshot()
            self._register_current_session()
            return [
                mcp_types.Tool(
                    name=tool.exposed_name,
                    title=tool.title,
                    description=tool.description,
                    inputSchema=tool.input_schema,
                    annotations=tool.annotations,
                    icons=list(tool.icons) or None,
                    execution=_tool_execution(snapshot, tool.server),
                    _meta={
                        "canonical": {"server": tool.server, "name": tool.name},
                        "surface": "native",
                    },
                )
                for tool in snapshot.native_index.tools
            ]

        @self.server.call_tool()
        async def call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> mcp_types.CallToolResult | mcp_types.CreateTaskResult:
            snapshot = self._require_snapshot()
            session = self._register_current_session()
            tool = snapshot.native_index.tools_by_exposed_name.get(name)
            if tool is None:
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"Unknown native tool: {name}",
                    )
                )

            experimental = getattr(self.server.request_context, "experimental", None)
            task_metadata = getattr(experimental, "task_metadata", None)
            result = await self.runtime.native_call_tool(
                tool.server,
                tool.name,
                arguments or {},
                ttl=getattr(task_metadata, "ttl", None),
            )
            if isinstance(result, mcp_types.CreateTaskResult):
                return self.registry.register_task(
                    server=tool.server,
                    tool_name=tool.name,
                    upstream_result=result,
                    session=session,
                )
            return result

        @self.server.list_resources()
        async def list_resources() -> list[mcp_types.Resource]:
            snapshot = self._require_snapshot()
            self._register_current_session()
            return [
                mcp_types.Resource(
                    uri=_URI_ADAPTER.validate_python(resource.exposed_uri),
                    name=resource.name,
                    title=resource.title,
                    description=resource.description,
                    mimeType=resource.mime_type,
                    size=resource.size,
                    annotations=resource.annotations,
                    icons=list(resource.icons) or None,
                    _meta={
                        "canonical": {"server": resource.server, "uri": resource.uri},
                        "surface": "native",
                    },
                )
                for resource in snapshot.native_index.resources
            ]

        @self.server.list_resource_templates()
        async def list_resource_templates() -> list[mcp_types.ResourceTemplate]:
            snapshot = self._require_snapshot()
            self._register_current_session()
            return [
                mcp_types.ResourceTemplate(
                    uriTemplate=resource_template.exposed_uri_template,
                    name=resource_template.name,
                    title=resource_template.title,
                    description=resource_template.description,
                    mimeType=resource_template.mime_type,
                    annotations=resource_template.annotations,
                    icons=list(resource_template.icons) or None,
                    _meta={
                        "canonical": {
                            "server": resource_template.server,
                            "uriTemplate": resource_template.uri_template,
                        },
                        "surface": "native",
                    },
                )
                for resource_template in snapshot.native_index.resource_templates
            ]

        @self.server.read_resource()
        async def read_resource(uri: Any) -> list[ReadResourceContents]:
            snapshot = self._require_snapshot()
            self._register_current_session()
            resource = snapshot.native_index.resources_by_exposed_uri.get(str(uri))
            if resource is None:
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"Unknown native resource: {uri}",
                    )
                )
            contents = await self.runtime.native_read_resource(resource.server, resource.uri)
            return _to_read_resource_contents(contents)

        @self.server.subscribe_resource()
        async def subscribe_resource(uri: Any) -> None:
            snapshot = self._require_snapshot()
            session = self._register_current_session()
            resource = snapshot.native_index.resources_by_exposed_uri.get(str(uri))
            if resource is None:
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"Unknown native resource: {uri}",
                    )
                )
            first_local_subscriber = self.registry.subscribe(session, resource.exposed_uri)
            if first_local_subscriber:
                await self.runtime.subscribe_resource(resource.server, resource.uri)

        @self.server.unsubscribe_resource()
        async def unsubscribe_resource(uri: Any) -> None:
            snapshot = self._require_snapshot()
            session = self._register_current_session()
            resource = snapshot.native_index.resources_by_exposed_uri.get(str(uri))
            if resource is None:
                return
            last_local_subscriber = self.registry.unsubscribe(session, resource.exposed_uri)
            if last_local_subscriber:
                await self.runtime.unsubscribe_resource(resource.server, resource.uri)

        @self.server.list_prompts()
        async def list_prompts() -> list[mcp_types.Prompt]:
            snapshot = self._require_snapshot()
            self._register_current_session()
            return [
                mcp_types.Prompt(
                    name=prompt.exposed_name,
                    title=prompt.title,
                    description=prompt.description,
                    arguments=[
                        mcp_types.PromptArgument(
                            name=argument.name,
                            description=argument.description,
                            required=argument.required,
                        )
                        for argument in prompt.arguments
                    ],
                    icons=list(prompt.icons) or None,
                    _meta={
                        "canonical": {"server": prompt.server, "name": prompt.name},
                        "surface": "native",
                    },
                )
                for prompt in snapshot.native_index.prompts
            ]

        @self.server.get_prompt()
        async def get_prompt(
            name: str, arguments: dict[str, str] | None
        ) -> mcp_types.GetPromptResult:
            snapshot = self._require_snapshot()
            self._register_current_session()
            prompt = snapshot.native_index.prompts_by_exposed_name.get(name)
            if prompt is None:
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"Unknown native prompt: {name}",
                    )
                )
            return await self.runtime.native_get_prompt(
                prompt.server,
                prompt.name,
                arguments or {},
            )

        @self.server.completion()
        async def complete(
            ref: mcp_types.PromptReference | mcp_types.ResourceTemplateReference,
            argument: mcp_types.CompletionArgument,
            context: mcp_types.CompletionContext | None,
        ) -> mcp_types.Completion | None:
            snapshot = self._require_snapshot()
            self._register_current_session()
            if isinstance(ref, mcp_types.PromptReference):
                prompt = snapshot.native_index.prompts_by_exposed_name.get(ref.name)
                if prompt is None:
                    return mcp_types.Completion(values=[], total=0, hasMore=False)
                return await self.runtime.native_complete(
                    prompt.server,
                    mcp_types.PromptReference(type="ref/prompt", name=prompt.name),
                    {"name": argument.name, "value": argument.value},
                    context.arguments if context is not None else None,
                )

            resource_template = snapshot.native_index.resource_templates_by_exposed_uri.get(ref.uri)
            if resource_template is None:
                return mcp_types.Completion(values=[], total=0, hasMore=False)
            return await self.runtime.native_complete(
                resource_template.server,
                mcp_types.ResourceTemplateReference(
                    type="ref/resource",
                    uri=resource_template.uri_template,
                ),
                {"name": argument.name, "value": argument.value},
                context.arguments if context is not None else None,
            )

        @self.server.experimental.list_tasks()
        async def list_tasks(
            request: mcp_types.ListTasksRequest,
        ) -> mcp_types.ListTasksResult:
            del request
            self._register_current_session()
            return mcp_types.ListTasksResult(tasks=self.registry.list_tasks(), nextCursor=None)

        @self.server.experimental.get_task()
        async def get_task(
            request: mcp_types.GetTaskRequest,
        ) -> mcp_types.GetTaskResult:
            self._register_current_session()
            record = self.registry.get_task(request.params.taskId)
            if record is None:
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"Task not found: {request.params.taskId}",
                    )
                )
            status = await self.runtime.native_get_task_status(record.server, record.upstream_task_id)
            rewritten = self.registry.rewrite_get_task_result(record.server, status)
            record.status = _task_from_status(rewritten)
            return rewritten

        @self.server.experimental.get_task_result()
        async def get_task_result(
            request: mcp_types.GetTaskPayloadRequest,
        ) -> mcp_types.GetTaskPayloadResult:
            self._register_current_session()
            record = self.registry.get_task(request.params.taskId)
            if record is None:
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"Task not found: {request.params.taskId}",
                    )
                )
            payload = await self.runtime.native_get_task_result(record.server, record.upstream_task_id)
            return mcp_types.GetTaskPayloadResult.model_validate(payload)

        @self.server.experimental.cancel_task()
        async def cancel_task(
            request: mcp_types.CancelTaskRequest,
        ) -> mcp_types.CancelTaskResult:
            self._register_current_session()
            record = self.registry.get_task(request.params.taskId)
            if record is None:
                raise McpError(
                    mcp_types.ErrorData(
                        code=mcp_types.INVALID_PARAMS,
                        message=f"Task not found: {request.params.taskId}",
                    )
                )
            cancelled = await self.runtime.native_cancel_task(record.server, record.upstream_task_id)
            return self.registry.update_task_from_cancel(record.server, cancelled)

    def _require_snapshot(self) -> CatalogSnapshot:
        if self._snapshot is None:
            raise RuntimeError("Native surface not synchronized with runtime snapshot")
        return self._snapshot

    def _register_current_session(self) -> ServerSession:
        session = self.server.request_context.session
        self.registry.register(session)
        return session


def _tool_keys(snapshot: CatalogSnapshot) -> set[str]:
    return {tool.exposed_name for tool in snapshot.native_index.tools}


def _resource_keys(snapshot: CatalogSnapshot) -> set[str]:
    return {resource.exposed_uri for resource in snapshot.native_index.resources}


def _prompt_keys(snapshot: CatalogSnapshot) -> set[str]:
    return {prompt.exposed_name for prompt in snapshot.native_index.prompts}


def _tool_execution(
    snapshot: CatalogSnapshot, server: str
) -> mcp_types.ToolExecution | None:
    capabilities = snapshot.server_capabilities.get(server, ServerCapabilitiesRecord())
    if not capabilities.tasks:
        return None
    return mcp_types.ToolExecution(taskSupport="optional")


def _rewrite_task_identifier(task: mcp_types.Task, task_id: str) -> mcp_types.Task:
    return mcp_types.Task.model_validate(
        {
            **task.model_dump(mode="json", by_alias=True),
            "taskId": task_id,
        }
    )


def _task_from_status(
    status: mcp_types.GetTaskResult | mcp_types.CancelTaskResult,
) -> mcp_types.Task:
    return mcp_types.Task.model_validate(status.model_dump(mode="json", by_alias=True))


def _to_read_resource_contents(contents: list[Any]) -> list[ReadResourceContents]:
    normalized: list[ReadResourceContents] = []
    for content in contents:
        if isinstance(content, mcp_types.TextResourceContents):
            normalized.append(
                ReadResourceContents(
                    content=content.text,
                    mime_type=content.mimeType,
                    meta=content.meta,
                )
            )
            continue
        if isinstance(content, mcp_types.BlobResourceContents):
            normalized.append(
                ReadResourceContents(
                    content=content.blob.encode("utf-8"),
                    mime_type=content.mimeType,
                    meta=content.meta,
                )
            )
            continue
        if isinstance(content, dict):
            if "text" in content:
                normalized.append(
                    ReadResourceContents(
                        content=str(content["text"]),
                        mime_type=content.get("mimeType"),
                        meta=content.get("_meta"),
                    )
                )
                continue
            if "blob" in content:
                blob = content["blob"]
                payload = blob if isinstance(blob, bytes) else str(blob).encode("utf-8")
                normalized.append(
                    ReadResourceContents(
                        content=payload,
                        mime_type=content.get("mimeType"),
                        meta=content.get("_meta"),
                    )
                )
                continue
        normalized.append(ReadResourceContents(content=str(content), mime_type="text/plain"))
    return normalized
