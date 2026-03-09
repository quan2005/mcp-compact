"""MCP Compact integration tests."""
# mypy: disable-error-code="arg-type"

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import mcp.types as mcp_types
import pytest
from fastmcp import Client
from pydantic import AnyUrl, TypeAdapter

from mcp_compact.__main__ import (
    MCPCompactRuntime,
    McpServerConfig,
    ProxyConfig,
    create_projection_server,
)

_URL_ADAPTER = TypeAdapter(AnyUrl)


@dataclass
class FakeUpstreamState:
    """Mutable upstream catalog and behavior for projection-only tests."""

    tools: dict[str, mcp_types.Tool] = field(default_factory=dict)
    resources: dict[str, mcp_types.Resource] = field(default_factory=dict)
    resource_templates: dict[str, mcp_types.ResourceTemplate] = field(default_factory=dict)
    resource_contents: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tools:
            return
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
        self.resource_contents = {
            "memo://static": "static-note",
            "memo://notes/welcome": "note:welcome",
        }

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

    async def list_tools(self) -> list[mcp_types.Tool]:
        return [self.tools[name] for name in sorted(self.tools)]

    async def list_resources(self) -> list[mcp_types.Resource]:
        return [self.resources[uri] for uri in sorted(self.resources)]

    async def list_resource_templates(self) -> list[mcp_types.ResourceTemplate]:
        return [
            self.resource_templates[uri_template]
            for uri_template in sorted(self.resource_templates)
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        if name == "echo_note":
            return {"title": arguments["title"], "body": arguments["body"]}
        if name == "delete_note":
            return {"deleted": arguments["slug"]}
        if name == "search_notes":
            return {"query": arguments["query"]}
        if name == "lookup_doc":
            return {"doc": arguments["doc"]}
        raise ValueError(f"Unknown tool: {name}")

    async def read_resource(self, uri: str) -> list[mcp_types.TextResourceContents]:
        return [
            mcp_types.TextResourceContents(
                uri=_URL_ADAPTER.validate_python(uri),
                text=self.resource_contents[uri],
                mimeType="text/plain",
            )
        ]


class FakeUpstreamClient:
    """Async client compatible with UpstreamRegistry."""

    def __init__(self, state: FakeUpstreamState) -> None:
        self._state = state

    async def __aenter__(self) -> "FakeUpstreamClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        return None

    async def list_tools(self) -> list[mcp_types.Tool]:
        return await self._state.list_tools()

    async def list_resources(self) -> list[mcp_types.Resource]:
        return await self._state.list_resources()

    async def list_resource_templates(self) -> list[mcp_types.ResourceTemplate]:
        return await self._state.list_resource_templates()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._state.call_tool(name, arguments)

    async def read_resource(self, uri: str) -> list[mcp_types.TextResourceContents]:
        return await self._state.read_resource(uri)


def _client_factory(state: FakeUpstreamState) -> Any:
    def build(message_handler: Any | None = None) -> FakeUpstreamClient:
        del message_handler
        return FakeUpstreamClient(state)

    return build


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


async def _build_runtime(states: dict[str, FakeUpstreamState]) -> MCPCompactRuntime:
    config = ProxyConfig(
        mcpServers={
            server_name: McpServerConfig(
                type="stdio",
                command="unused-for-tests",
                args=[],
            )
            for server_name in states
        }
    )
    runtime = MCPCompactRuntime(
        config,
        client_factory_overrides={
            server_name: _client_factory(state)
            for server_name, state in states.items()
        },
    )
    await runtime.initialize()
    return runtime


@pytest.mark.asyncio
async def test_projection_surface_only_exposes_invoke_and_read() -> None:
    runtime = await _build_runtime({"notes": FakeUpstreamState()})
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
    assert "uriTemplate" in (read.description or "")
    assert "memo://notes/{slug}" in (read.description or "")


@pytest.mark.asyncio
async def test_projection_validate_call_preview_and_read_are_canonical() -> None:
    runtime = await _build_runtime({"notes": FakeUpstreamState()})
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
    assert validate_payload["example"] == {"title": "<string>", "body": "<string>"}

    assert call_payload["ok"] is True
    assert call_payload["summary"] == "notes.echo_note"
    assert call_payload["output"] == {"title": "hello", "body": "world"}

    assert preview_payload["ok"] is True
    assert preview_payload["selector"] == {"server": "notes", "uriTemplate": "memo://notes/{slug}"}
    assert preview_payload["resource_template"]["arguments"] == ["slug"]

    assert read_payload["ok"] is True
    assert read_payload["contents"][0]["text"] == "note:welcome"


@pytest.mark.asyncio
async def test_invalid_selectors_return_stable_suggestions() -> None:
    runtime = await _build_runtime({"notes": FakeUpstreamState()})
    projection = create_projection_server(runtime.config, runtime=runtime)

    try:
        async with Client(projection) as client:
            missing_tool = await client.call_tool(
                "invoke",
                arguments={
                    "ref": {"server": "notes", "name": "echo_not"},
                    "mode": "call",
                },
            )
            missing_resource = await client.call_tool(
                "read",
                arguments={
                    "ref": {"server": "notes", "uri": "memo://statc"},
                    "mode": "read",
                },
            )
    finally:
        await runtime.close()

    missing_tool_payload = _parse_tool_result(missing_tool)
    missing_resource_payload = _parse_tool_result(missing_resource)

    assert missing_tool_payload["ok"] is False
    assert {"server": "notes", "name": "echo_note"} in missing_tool_payload["suggestions"]

    assert missing_resource_payload["ok"] is False
    assert {"server": "notes", "uri": "memo://static"} in missing_resource_payload["suggestions"]


@pytest.mark.asyncio
async def test_manual_refresh_rebuilds_projection_descriptions() -> None:
    state = FakeUpstreamState()
    runtime = await _build_runtime({"notes": state})
    projection = create_projection_server(runtime.config, runtime=runtime)

    try:
        state.add_tool("search_notes", "Search notes by keyword.")
        await runtime.refresh()

        async with Client(projection) as client:
            tools = await client.list_tools()
            validated = await client.call_tool(
                "invoke",
                arguments={
                    "ref": {"server": "notes", "name": "search_notes"},
                    "mode": "validate",
                },
            )
    finally:
        await runtime.close()

    invoke = next(tool for tool in tools if tool.name == "invoke")
    validate_payload = _parse_tool_result(validated)

    assert "notes.search_notes" in (invoke.description or "")
    assert validate_payload["ok"] is True
    assert validate_payload["selector"] == {"server": "notes", "name": "search_notes"}


@pytest.mark.asyncio
async def test_multiple_servers_share_one_projection_surface() -> None:
    notes = FakeUpstreamState()
    docs = FakeUpstreamState(
        tools={
            "lookup_doc": mcp_types.Tool(
                name="lookup_doc",
                description="Lookup a document by id.",
                inputSchema={
                    "type": "object",
                    "properties": {"doc": {"type": "string"}},
                    "required": ["doc"],
                },
            )
        },
        resources={},
        resource_templates={},
        resource_contents={},
    )
    runtime = await _build_runtime({"notes": notes, "docs": docs})
    projection = create_projection_server(runtime.config, runtime=runtime)

    try:
        async with Client(projection) as client:
            called = await client.call_tool(
                "invoke",
                arguments={
                    "ref": {"server": "docs", "name": "lookup_doc"},
                    "arguments": {"doc": "guide"},
                    "mode": "call",
                },
            )
            read_static = await client.call_tool(
                "read",
                arguments={
                    "ref": {"server": "notes", "uri": "memo://static"},
                    "mode": "read",
                },
            )
    finally:
        await runtime.close()

    call_payload = _parse_tool_result(called)
    read_payload = _parse_tool_result(read_static)

    assert call_payload["ok"] is True
    assert call_payload["summary"] == "docs.lookup_doc"
    assert call_payload["output"] == {"doc": "guide"}
    assert read_payload["ok"] is True
    assert read_payload["contents"][0]["text"] == "static-note"
