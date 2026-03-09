"""MCPX 2.1 integration tests."""
# mypy: disable-error-code="arg-type"

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal

import mcp.types as mcp_types
import pytest
from fastmcp import Client
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl, RootModel, TypeAdapter

from mcpx.__main__ import (
    McpServerConfig,
    ProxyConfig,
    create_native_server,
    create_projection_server,
)
from mcpx.runtime import MCPXRuntime

_URL_ADAPTER = TypeAdapter(AnyUrl)
TaskState = Literal["working", "input_required", "completed", "failed", "cancelled"]


class ToolTaskResultUnion(RootModel[mcp_types.CreateTaskResult | mcp_types.CallToolResult]):
    """Union wrapper for task-aware tool call assertions."""


@dataclass
class FakeTaskRecord:
    """Stored upstream task metadata."""

    task: mcp_types.Task
    payload: dict[str, Any]


@dataclass
class FakeUpstreamState:
    """Mutable upstream catalog and behavior for 2.1 tests."""

    handlers: list[Any] = field(default_factory=list)
    subscribed_uris: dict[str, int] = field(default_factory=dict)
    tools: dict[str, mcp_types.Tool] = field(default_factory=dict)
    resources: dict[str, mcp_types.Resource] = field(default_factory=dict)
    resource_templates: dict[str, mcp_types.ResourceTemplate] = field(default_factory=dict)
    prompts: dict[str, mcp_types.Prompt] = field(default_factory=dict)
    resource_contents: dict[str, str] = field(default_factory=dict)
    task_records: dict[str, FakeTaskRecord] = field(default_factory=dict)
    next_task_id: int = 1

    def __post_init__(self) -> None:
        self.tools = {
            "echo_note": mcp_types.Tool(
                name="echo_note",
                title="Echo note",
                description="Echo a note by title and body.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["title", "body"],
                },
            ),
            "delete_note": mcp_types.Tool(
                name="delete_note",
                description="Delete a note by slug.",
                inputSchema={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                annotations=mcp_types.ToolAnnotations(readOnlyHint=False),
            ),
        }
        self.resources = {
            "memo://static": mcp_types.Resource(
                uri=_URL_ADAPTER.validate_python("memo://static"),
                name="static_note",
                title="Static note",
                description="A static project note.",
                mimeType="text/plain",
            )
        }
        self.resource_templates = {
            "memo://notes/{slug}": mcp_types.ResourceTemplate(
                uriTemplate="memo://notes/{slug}",
                name="note_template",
                title="Note template",
                description="Read a note by slug.",
                mimeType="text/plain",
            )
        }
        self.prompts = {
            "summarize_note": mcp_types.Prompt(
                name="summarize_note",
                description="Generate a note summary workflow.",
                arguments=[
                    mcp_types.PromptArgument(name="slug", description="Note slug", required=True)
                ],
            )
        }
        self.resource_contents = {
            "memo://static": "static-note",
            "memo://notes/welcome": "note:welcome",
        }

    @property
    def capabilities(self) -> Any:
        return SimpleNamespace(
            tools=SimpleNamespace(listChanged=True),
            resources=SimpleNamespace(subscribe=True, listChanged=True),
            prompts=SimpleNamespace(listChanged=True),
            completions=SimpleNamespace(),
            tasks=SimpleNamespace(),
        )

    def add_tool(self, name: str, description: str) -> None:
        self.tools[name] = mcp_types.Tool(
            name=name,
            description=description,
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    async def emit_tool_list_changed(self) -> None:
        notification = mcp_types.ToolListChangedNotification()
        for handler in list(self.handlers):
            if hasattr(handler, "on_tool_list_changed"):
                await handler.on_tool_list_changed(notification)

    async def emit_resource_list_changed(self) -> None:
        notification = mcp_types.ResourceListChangedNotification()
        for handler in list(self.handlers):
            if hasattr(handler, "on_resource_list_changed"):
                await handler.on_resource_list_changed(notification)

    async def emit_prompt_list_changed(self) -> None:
        notification = mcp_types.PromptListChangedNotification()
        for handler in list(self.handlers):
            if hasattr(handler, "on_prompt_list_changed"):
                await handler.on_prompt_list_changed(notification)

    async def emit_resource_updated(self, uri: str) -> None:
        notification = mcp_types.ResourceUpdatedNotification(
            params=mcp_types.ResourceUpdatedNotificationParams(
                uri=_URL_ADAPTER.validate_python(uri)
            )
        )
        for handler in list(self.handlers):
            if hasattr(handler, "on_resource_updated"):
                await handler.on_resource_updated(notification)

    async def emit_task_status(self, task_id: str, status: TaskState) -> None:
        task = self.task_records[task_id].task
        updated_task = self._new_task(task_id, status, task.ttl)
        self.task_records[task_id].task = updated_task
        notification = mcp_types.ServerNotification(
            mcp_types.TaskStatusNotification(
                params=mcp_types.TaskStatusNotificationParams.model_validate(
                    updated_task.model_dump(mode="json", by_alias=True)
                )
            )
        )
        for handler in list(self.handlers):
            if hasattr(handler, "on_notification"):
                await handler.on_notification(notification)

    async def list_tools(self) -> list[mcp_types.Tool]:
        return [self.tools[name] for name in sorted(self.tools)]

    async def list_resources(self) -> list[mcp_types.Resource]:
        return [self.resources[uri] for uri in sorted(self.resources)]

    async def list_resource_templates(self) -> list[mcp_types.ResourceTemplate]:
        return [
            self.resource_templates[uri_template]
            for uri_template in sorted(self.resource_templates)
        ]

    async def list_prompts(self) -> list[mcp_types.Prompt]:
        return [self.prompts[name] for name in sorted(self.prompts)]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        if name == "echo_note":
            return {"title": arguments["title"], "body": arguments["body"]}
        if name == "delete_note":
            return {"deleted": arguments["slug"]}
        if name == "search_notes":
            return {"query": arguments["query"]}
        raise ValueError(f"Unknown tool: {name}")

    async def call_tool_task_request(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        payload = await self.call_tool(name, arguments)
        if ttl is None:
            return _call_tool_result(payload)

        task_id = f"task-{self.next_task_id}"
        self.next_task_id += 1
        task = self._new_task(task_id, "working", ttl)
        self.task_records[task_id] = FakeTaskRecord(task=task, payload=_call_tool_result(payload).model_dump(mode="json", by_alias=True))
        return mcp_types.CreateTaskResult(task=task)

    async def read_resource(self, uri: str) -> list[mcp_types.TextResourceContents]:
        return [
            mcp_types.TextResourceContents(
                uri=_URL_ADAPTER.validate_python(uri),
                text=self.resource_contents[uri],
                mimeType="text/plain",
            )
        ]

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.GetPromptResult:
        arguments = arguments or {}
        if name != "summarize_note":
            raise ValueError(f"Unknown prompt: {name}")
        return mcp_types.GetPromptResult(
            description="Summarize a note",
            messages=[
                mcp_types.PromptMessage(
                    role="user",
                    content=mcp_types.TextContent(
                        type="text",
                        text=f"Summarize note {arguments['slug']}",
                    ),
                )
            ],
        )

    async def complete(
        self,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        del context_arguments
        if isinstance(ref, mcp_types.PromptReference):
            if ref.name == "summarize_note" and argument["name"] == "slug":
                return mcp_types.Completion(values=["welcome", "draft"], total=2, hasMore=False)
            return mcp_types.Completion(values=[], total=0, hasMore=False)
        if ref.uri == "memo://notes/{slug}" and argument["name"] == "slug":
            return mcp_types.Completion(values=["welcome", "draft"], total=2, hasMore=False)
        return mcp_types.Completion(values=[], total=0, hasMore=False)

    async def get_task_status(self, task_id: str) -> mcp_types.GetTaskResult:
        return mcp_types.GetTaskResult.model_validate(
            self.task_records[task_id].task.model_dump(mode="json", by_alias=True)
        )

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        return dict(self.task_records[task_id].payload)

    async def cancel_task(self, task_id: str) -> mcp_types.CancelTaskResult:
        current = self.task_records[task_id].task
        cancelled = mcp_types.CancelTaskResult.model_validate(
            {
                **current.model_dump(mode="json", by_alias=True),
                "status": "cancelled",
            }
        )
        self.task_records[task_id].task = mcp_types.Task.model_validate(
            cancelled.model_dump(mode="json", by_alias=True)
        )
        return cancelled

    async def ping(self) -> None:
        return None

    def _new_task(self, task_id: str, status: TaskState, ttl: int | None) -> mcp_types.Task:
        timestamp = datetime.now(timezone.utc)
        return mcp_types.Task(
            taskId=task_id,
            status=status,
            createdAt=timestamp,
            lastUpdatedAt=timestamp,
            ttl=ttl,
            statusMessage=None,
            pollInterval=1,
        )


class FakeClientSession:
    """Session object exposed to discovery subscriptions."""

    def __init__(self, state: FakeUpstreamState) -> None:
        self._state = state

    async def subscribe_resource(self, uri: AnyUrl) -> None:
        key = str(uri)
        self._state.subscribed_uris[key] = self._state.subscribed_uris.get(key, 0) + 1

    async def unsubscribe_resource(self, uri: AnyUrl) -> None:
        key = str(uri)
        if key not in self._state.subscribed_uris:
            return
        if self._state.subscribed_uris[key] <= 1:
            del self._state.subscribed_uris[key]
        else:
            self._state.subscribed_uris[key] -= 1


class FakeUpstreamClient:
    """Async client compatible with ExecutionPools and DiscoveryHub."""

    def __init__(
        self,
        state: FakeUpstreamState,
        message_handler: Any | None = None,
    ) -> None:
        self._state = state
        self._message_handler = message_handler
        self.initialize_result = SimpleNamespace(capabilities=state.capabilities)
        self.session = FakeClientSession(state)

    async def __aenter__(self) -> "FakeUpstreamClient":
        if self._message_handler is not None:
            self._state.handlers.append(self._message_handler)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._message_handler is not None and self._message_handler in self._state.handlers:
            self._state.handlers.remove(self._message_handler)

    async def list_tools(self) -> list[mcp_types.Tool]:
        return await self._state.list_tools()

    async def list_resources(self) -> list[mcp_types.Resource]:
        return await self._state.list_resources()

    async def list_resource_templates(self) -> list[mcp_types.ResourceTemplate]:
        return await self._state.list_resource_templates()

    async def list_prompts(self) -> list[mcp_types.Prompt]:
        return await self._state.list_prompts()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._state.call_tool(name, arguments)

    async def call_tool_task_request(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        return await self._state.call_tool_task_request(name, arguments, ttl)

    async def read_resource(self, uri: str) -> list[mcp_types.TextResourceContents]:
        return await self._state.read_resource(uri)

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.GetPromptResult:
        return await self._state.get_prompt(name, arguments)

    async def complete(
        self,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        return await self._state.complete(ref, argument, context_arguments)

    async def get_task_status(self, task_id: str) -> mcp_types.GetTaskResult:
        return await self._state.get_task_status(task_id)

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        return await self._state.get_task_result(task_id)

    async def cancel_task(self, task_id: str) -> mcp_types.CancelTaskResult:
        return await self._state.cancel_task(task_id)

    async def ping(self) -> None:
        await self._state.ping()


def _client_factory(state: FakeUpstreamState) -> Any:
    def build(message_handler: Any | None = None) -> FakeUpstreamClient:
        return FakeUpstreamClient(state, message_handler=message_handler)

    return build


def _call_tool_result(payload: dict[str, Any]) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False),
            )
        ],
        structuredContent=payload,
        isError=False,
    )


def _extract_text_content(result: Any) -> str:
    if hasattr(result, "content"):
        content_list = result.content
        if content_list:
            first_item = content_list[0]
            if hasattr(first_item, "text"):
                return str(first_item.text)
    if hasattr(result, "data") and result.data is not None:
        return str(result.data)
    return str(result)


def _parse_tool_result(result: Any) -> Any:
    return json.loads(_extract_text_content(result))


async def _build_runtime(state: FakeUpstreamState) -> MCPXRuntime:
    config = ProxyConfig(
        mcpServers={
            "notes": McpServerConfig(
                type="stdio",
                command="unused-for-tests",
                args=[],
            )
        }
    )
    runtime = MCPXRuntime(
        config,
        client_factory_overrides={"notes": _client_factory(state)},
    )
    await runtime.initialize()
    return runtime


async def _wait_until(predicate: Any, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met before timeout")


def _unwrap_notification(message: Any) -> Any:
    root = getattr(message, "root", message)
    return getattr(root, "root", root)


@pytest.mark.asyncio
async def test_projection_surface_only_exposes_invoke_and_read() -> None:
    state = FakeUpstreamState()
    runtime = await _build_runtime(state)
    projection = create_projection_server(runtime.config, runtime=runtime)

    try:
        async with Client(projection) as client:
            tools = await client.list_tools()
    finally:
        await runtime.close()

    assert [tool.name for tool in tools] == ["invoke", "read"]

    invoke = next(tool for tool in tools if tool.name == "invoke")
    read = next(tool for tool in tools if tool.name == "read")

    assert "ref.server" in (invoke.description or "")
    assert "notes.echo_note" in (invoke.description or "")
    assert "Suggested workflows from prompts" in (invoke.description or "")
    assert "uriTemplate" in (read.description or "")
    assert "memo://notes/{slug}" in (read.description or "")


@pytest.mark.asyncio
async def test_projection_validate_and_read_paths_are_canonical() -> None:
    state = FakeUpstreamState()
    runtime = await _build_runtime(state)
    projection = create_projection_server(runtime.config, runtime=runtime)

    try:
        async with Client(projection) as client:
            validated = await client.call_tool(
                "invoke",
                arguments={
                    "ref": {"server": "notes", "name": "echo_note"},
                    "mode": "validate",
                },
            )
            called = await client.call_tool(
                "invoke",
                arguments={
                    "ref": {"server": "notes", "name": "echo_note"},
                    "arguments": {"title": "hello", "body": "world"},
                    "mode": "call",
                },
            )
            preview_template = await client.call_tool(
                "read",
                arguments={
                    "ref": {"server": "notes", "uriTemplate": "memo://notes/{slug}"},
                    "mode": "preview",
                },
            )
            read_template = await client.call_tool(
                "read",
                arguments={
                    "ref": {
                        "server": "notes",
                        "uriTemplate": "memo://notes/{slug}",
                        "arguments": {"slug": "welcome"},
                    },
                    "mode": "read",
                },
            )
    finally:
        await runtime.close()

    validate_payload = _parse_tool_result(validated)
    call_payload = _parse_tool_result(called)
    preview_payload = _parse_tool_result(preview_template)
    read_payload = _parse_tool_result(read_template)

    assert validate_payload["ok"] is True
    assert validate_payload["selector"] == {"server": "notes", "name": "echo_note"}
    assert validate_payload["tasks_supported"] is True
    assert validate_payload["completions"]["supported"] is True
    assert validate_payload["example"] == {"title": "<string>", "body": "<string>"}

    assert call_payload["ok"] is True
    assert call_payload["summary"] == "notes.echo_note"
    assert call_payload["output"] == {"title": "hello", "body": "world"}

    assert preview_payload["ok"] is True
    assert preview_payload["selector"] == {"server": "notes", "uriTemplate": "memo://notes/{slug}"}
    assert preview_payload["resource_template"]["arguments"] == ["slug"]
    assert preview_payload["completions"]["supported"] is True

    assert read_payload["ok"] is True
    assert read_payload["contents"][0]["text"] == "note:welcome"


@pytest.mark.asyncio
async def test_native_surface_exposes_canonical_primitives_completion_and_tasks() -> None:
    state = FakeUpstreamState()
    runtime = await _build_runtime(state)
    native = create_native_server(runtime.config, runtime=runtime)

    try:
        async with create_connected_server_and_client_session(native.server) as client:
            tools_result = await client.list_tools()
            resources_result = await client.list_resources()
            templates_result = await client.list_resource_templates()
            prompts_result = await client.list_prompts()
            prompt_result = await client.get_prompt("notes.summarize_note", {"slug": "welcome"})

            prompt_completion = await client.complete(
                mcp_types.PromptReference(type="ref/prompt", name="notes.summarize_note"),
                {"name": "slug", "value": "we"},
            )
            resource_completion = await client.complete(
                mcp_types.ResourceTemplateReference(
                    type="ref/resource",
                    uri="mcpx://notes/memo://notes/{slug}",
                ),
                {"name": "slug", "value": "we"},
            )

            task_call = await client.send_request(
                request=mcp_types.CallToolRequest(
                    params=mcp_types.CallToolRequestParams(
                        name="notes.echo_note",
                        arguments={"title": "native", "body": "task"},
                        task=mcp_types.TaskMetadata(ttl=60),
                    )
                ),
                result_type=ToolTaskResultUnion,
            )
            task_result = task_call.root
            assert isinstance(task_result, mcp_types.CreateTaskResult)

            task_id = task_result.task.taskId
            status = await client.send_request(
                request=mcp_types.GetTaskRequest(
                    params=mcp_types.GetTaskRequestParams(taskId=task_id)
                ),
                result_type=mcp_types.GetTaskResult,
            )
            payload = await client.send_request(
                request=mcp_types.GetTaskPayloadRequest(
                    params=mcp_types.GetTaskPayloadRequestParams(taskId=task_id)
                ),
                result_type=mcp_types.GetTaskPayloadResult,
            )
            cancelled = await client.send_request(
                request=mcp_types.CancelTaskRequest(
                    params=mcp_types.CancelTaskRequestParams(taskId=task_id)
                ),
                result_type=mcp_types.CancelTaskResult,
            )
            listed_tasks = await client.send_request(
                request=mcp_types.ListTasksRequest(params=mcp_types.PaginatedRequestParams()),
                result_type=mcp_types.ListTasksResult,
            )
    finally:
        await runtime.close()

    tool_names = {tool.name for tool in tools_result.tools}
    resource_uris = {str(resource.uri) for resource in resources_result.resources}
    template_uris = {str(template.uriTemplate) for template in templates_result.resourceTemplates}
    prompt_names = {prompt.name for prompt in prompts_result.prompts}

    assert "notes.echo_note" in tool_names
    assert "notes.delete_note" in tool_names
    assert "mcpx://notes/memo://static" in resource_uris
    assert "mcpx://notes/memo://notes/{slug}" in template_uris
    assert "notes.summarize_note" in prompt_names
    first_prompt_content = prompt_result.messages[0].content
    assert isinstance(first_prompt_content, mcp_types.TextContent)
    assert first_prompt_content.text == "Summarize note welcome"
    assert prompt_completion.completion.values == ["welcome", "draft"]
    assert resource_completion.completion.values == ["welcome", "draft"]
    assert task_id.startswith("notes:task-")
    assert status.taskId == task_id
    assert payload.model_dump(mode="json", by_alias=True)["structuredContent"] == {
        "title": "native",
        "body": "task",
    }
    assert cancelled.taskId == task_id
    assert cancelled.status == "cancelled"
    assert task_id in {task.taskId for task in listed_tasks.tasks}


@pytest.mark.asyncio
async def test_watcher_list_changed_rebuilds_projection_and_native_indexes() -> None:
    state = FakeUpstreamState()
    runtime = await _build_runtime(state)
    projection = create_projection_server(runtime.config, runtime=runtime)
    native = create_native_server(runtime.config, runtime=runtime)

    try:
        state.add_tool("search_notes", "Search notes by keyword.")
        await state.emit_tool_list_changed()
        await _wait_until(lambda: runtime.snapshot.tool("notes", "search_notes") is not None)

        async with Client(projection) as projection_client:
            validated = await projection_client.call_tool(
                "invoke",
                arguments={
                    "ref": {"server": "notes", "name": "search_notes"},
                    "mode": "validate",
                },
            )
        async with create_connected_server_and_client_session(native.server) as native_client:
            tools_result = await native_client.list_tools()
    finally:
        await runtime.close()

    validate_payload = _parse_tool_result(validated)

    assert validate_payload["ok"] is True
    assert validate_payload["selector"] == {"server": "notes", "name": "search_notes"}
    assert "notes.search_notes" in {tool.name for tool in tools_result.tools}


@pytest.mark.asyncio
async def test_resource_subscription_forwards_upstream_resource_updated() -> None:
    state = FakeUpstreamState()
    runtime = await _build_runtime(state)
    native = create_native_server(runtime.config, runtime=runtime)
    notifications: list[Any] = []

    async def capture_notification(message: Any) -> None:
        notifications.append(_unwrap_notification(message))

    try:
        async with create_connected_server_and_client_session(
            native.server,
            message_handler=capture_notification,
        ) as client:
            await client.subscribe_resource(_URL_ADAPTER.validate_python("mcpx://notes/memo://static"))
            assert state.subscribed_uris["memo://static"] == 1

            await state.emit_resource_updated("memo://static")
            await _wait_until(
                lambda: any(
                    isinstance(notification, mcp_types.ResourceUpdatedNotification)
                    and str(notification.params.uri) == "mcpx://notes/memo://static"
                    for notification in notifications
                )
            )

            await client.unsubscribe_resource(_URL_ADAPTER.validate_python("mcpx://notes/memo://static"))
    finally:
        await runtime.close()

    assert "memo://static" not in state.subscribed_uris


@pytest.mark.asyncio
async def test_task_status_notifications_are_rewritten_for_native_clients() -> None:
    state = FakeUpstreamState()
    runtime = await _build_runtime(state)
    native = create_native_server(runtime.config, runtime=runtime)
    notifications: list[Any] = []

    async def capture_notification(message: Any) -> None:
        notifications.append(_unwrap_notification(message))

    try:
        async with create_connected_server_and_client_session(
            native.server,
            message_handler=capture_notification,
        ) as client:
            task_call = await client.send_request(
                request=mcp_types.CallToolRequest(
                    params=mcp_types.CallToolRequestParams(
                        name="notes.echo_note",
                        arguments={"title": "notify", "body": "me"},
                        task=mcp_types.TaskMetadata(ttl=30),
                    )
                ),
                result_type=ToolTaskResultUnion,
            )
            created = task_call.root
            assert isinstance(created, mcp_types.CreateTaskResult)
            native_task_id = created.task.taskId

            await state.emit_task_status("task-1", "completed")
            await _wait_until(
                lambda: any(
                    isinstance(notification, mcp_types.TaskStatusNotification)
                    and notification.params.taskId == native_task_id
                    and notification.params.status == "completed"
                    for notification in notifications
                )
            )
    finally:
        await runtime.close()
