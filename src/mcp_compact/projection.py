"""Agent-facing projection surface, compiler, resolver, and router."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

import mcp.types as mcp_types
from fastmcp import FastMCP
from fastmcp.client.client import CallToolResult as FastMcpCallToolResult

from mcp_compact.catalog import (
    CatalogSnapshot,
    ResourceRecord,
    ResourceTemplateRecord,
    ToolRecord,
    example_from_schema,
    expand_uri_template,
    tokenize,
)

__all__ = [
    "ExecutionBackend",
    "ExecutionRouter",
    "ProjectionBudget",
    "ProjectionCompiler",
    "ProjectionSurface",
    "Resolver",
]


@dataclass(frozen=True)
class ProjectionBudget:
    """Fixed description budgets for the projection surface."""

    max_tool_families: int = 12
    max_tools_per_family: int = 6
    max_direct_resources: int = 16
    max_template_resources: int = 16


class ProjectionCompiler:
    """Compile invoke/read descriptions from the catalog snapshot."""

    def __init__(self, budget: ProjectionBudget | None = None) -> None:
        self._budget = budget or ProjectionBudget()

    def compile_invoke_description(self, snapshot: CatalogSnapshot) -> str:
        lines = [
            "Invoke an MCP tool through the compact projection surface.",
            "",
            "Use `ref.server` and `ref.name` with canonical upstream identifiers.",
            'Use `mode="validate"` to inspect input requirements before calling.',
            "",
            "Available tool families:",
        ]

        selected_families = list(snapshot.tool_families.items())[: self._budget.max_tool_families]
        for family, tools in selected_families:
            lines.append(f"- {family}:")
            visible_tools = tools[: self._budget.max_tools_per_family]
            for tool in visible_tools:
                required = ", ".join(tool.required_args) if tool.required_args else "none"
                side_effect = "mutating" if tool.mutating else "read-mostly"
                lines.append(
                    f"  {tool.display_name} | required: {required} | {side_effect} | {_truncate(tool.description, 96)}"
                )
            hidden_count = len(tools) - len(visible_tools)
            if hidden_count > 0:
                lines.append(f"  (+{hidden_count} more in this family)")

        lines.extend(
            [
                "",
                'Example: invoke(ref={"server": "notes", "name": "echo_note"}, arguments={"title": "...", "body": "..."})',
            ]
        )
        return "\n".join(lines)

    def compile_read_description(self, snapshot: CatalogSnapshot) -> str:
        lines = [
            "Read MCP resources through the compact projection surface.",
            "",
            "Use `ref.server` with either `ref.uri` for direct resources or",
            "`ref.uriTemplate` plus template `arguments` for resource templates.",
            'Use `mode="preview"` before reading when you need metadata first.',
            "",
            "Direct resources:",
        ]

        direct_resources = snapshot.resources[: self._budget.max_direct_resources]
        if direct_resources:
            for resource in direct_resources:
                lines.append(
                    f"- {resource.server}:{resource.uri} | {resource.name} | {_truncate(resource.description, 96)}"
                )
        else:
            lines.append("- none")

        lines.extend(["", "Resource templates:"])
        templates = snapshot.resource_templates[: self._budget.max_template_resources]
        if templates:
            for resource_template in templates:
                args = ", ".join(resource_template.arguments) if resource_template.arguments else "none"
                lines.append(
                    f"- {resource_template.server}:{resource_template.uri_template} | args: {args} | {_truncate(resource_template.description, 96)}"
                )
        else:
            lines.append("- none")

        lines.extend(
            [
                "",
                'Example: read(ref={"server": "notes", "uriTemplate": "memo://notes/{slug}", "arguments": {"slug": "welcome"}}, mode="read")',
            ]
        )
        return "\n".join(lines)


class Resolver:
    """Resolve canonical refs and generate stable suggestions."""

    def resolve_tool(self, snapshot: CatalogSnapshot, ref: dict[str, Any] | None) -> ToolRecord | None:
        normalized = _normalize_tool_selector(ref)
        if normalized is None:
            return None
        return snapshot.tool(normalized["server"], normalized["name"])

    def suggest_tools(self, snapshot: CatalogSnapshot, server: str, name: str) -> list[dict[str, str]]:
        query = f"{server} {name}"
        ranked = self._rank(
            query,
            snapshot.tools,
            field_values=lambda tool: {
                "name": tool.name,
                "title": tool.title or "",
                "description": tool.description,
                "required_args": " ".join(tool.required_args),
                "server": tool.server,
            },
            stable_key=lambda tool: (tool.family, tool.server, tool.name),
        )
        return [tool.selector for tool in ranked[:5]]

    def suggest_resources(self, snapshot: CatalogSnapshot, server: str, uri: str) -> list[dict[str, str]]:
        query = f"{server} {uri}"
        ranked = self._rank(
            query,
            snapshot.resources,
            field_values=lambda resource: {
                "name": f"{resource.name} {resource.uri}",
                "title": resource.title or "",
                "description": resource.description,
                "required_args": "",
                "server": resource.server,
            },
            stable_key=lambda resource: (resource.server, resource.uri),
        )
        return [resource.selector for resource in ranked[:5]]

    def suggest_resource_templates(
        self, snapshot: CatalogSnapshot, server: str, uri_template: str
    ) -> list[dict[str, str]]:
        query = f"{server} {uri_template}"
        ranked = self._rank(
            query,
            snapshot.resource_templates,
            field_values=lambda resource_template: {
                "name": f"{resource_template.name} {resource_template.uri_template}",
                "title": resource_template.title or "",
                "description": resource_template.description,
                "required_args": " ".join(resource_template.arguments),
                "server": resource_template.server,
            },
            stable_key=lambda resource_template: (
                resource_template.server,
                resource_template.uri_template,
            ),
        )
        return [resource_template.selector for resource_template in ranked[:5]]

    def _rank(
        self,
        query: str,
        items: tuple[Any, ...],
        *,
        field_values: Any,
        stable_key: Any,
    ) -> list[Any]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return list(items)

        scored: list[tuple[int, Any]] = []
        weights = {
            "name": 8,
            "title": 5,
            "description": 3,
            "required_args": 2,
            "server": 1,
        }
        for item in items:
            score = 0
            values: dict[str, str] = field_values(item)
            for field_name, field_weight in weights.items():
                overlap = len(query_tokens & set(tokenize(values.get(field_name, ""))))
                score += field_weight * overlap
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda pair: (-pair[0], *stable_key(pair[1])))
        return [item for _, item in scored]


class ExecutionBackend(Protocol):
    """Execution backend protocol implemented by the runtime."""

    async def call_tool(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        ...

    async def read_resource(self, server: str, uri: str) -> list[Any]:
        ...


class ExecutionRouter:
    """Projection execution router."""

    def __init__(
        self,
        *,
        backend: ExecutionBackend,
        resolver: Resolver,
        snapshot_provider: Any,
    ) -> None:
        self._backend = backend
        self._resolver = resolver
        self._snapshot_provider = snapshot_provider

    async def invoke(
        self,
        ref: dict[str, Any] | None,
        arguments: dict[str, Any] | None = None,
        *,
        mode: str = "call",
    ) -> dict[str, Any]:
        snapshot = cast(CatalogSnapshot, self._snapshot_provider())
        tool = self._resolver.resolve_tool(snapshot, ref)
        normalized_ref = _normalize_tool_selector(ref)
        if normalized_ref is None:
            return self._error(
                snapshot,
                "INVALID_SELECTOR",
                "invoke.ref must be {'server': str, 'name': str}",
                [],
            )
        if tool is None:
            return self._error(
                snapshot,
                "TOOL_NOT_FOUND",
                f"Tool '{normalized_ref['server']}.{normalized_ref['name']}' not found",
                self._resolver.suggest_tools(snapshot, normalized_ref["server"], normalized_ref["name"]),
            )

        if mode == "validate":
            return {
                "ok": True,
                "mode": "validate",
                "summary": tool.display_name,
                "selector": tool.selector,
                "expected_input": tool.input_schema,
                "example": example_from_schema(tool.input_schema),
                "meta": {
                    "snapshot_version": snapshot.version,
                },
            }

        try:
            result = await self._backend.call_tool(tool.server, tool.name, arguments or {})
        except Exception as exc:
            return self._error(
                snapshot,
                "TOOL_CALL_FAILED",
                str(exc),
                self._resolver.suggest_tools(snapshot, tool.server, tool.name),
            )

        return {
            "ok": True,
            "mode": "call",
            "summary": tool.display_name,
            "output": _normalize_tool_output(result),
            "meta": {
                "selector": tool.selector,
                "snapshot_version": snapshot.version,
            },
        }

    async def read(
        self,
        ref: dict[str, Any] | None,
        *,
        mode: str = "read",
    ) -> dict[str, Any]:
        snapshot = cast(CatalogSnapshot, self._snapshot_provider())
        normalized = _normalize_read_selector(ref)
        if normalized is None:
            return self._error(
                snapshot,
                "INVALID_SELECTOR",
                "read.ref must contain {'server', 'uri'} or {'server', 'uriTemplate'}",
                [],
            )

        if "uri" in normalized:
            resource = snapshot.resource(normalized["server"], normalized["uri"])
            if resource is None:
                return self._error(
                    snapshot,
                    "RESOURCE_NOT_FOUND",
                    f"Resource '{normalized['server']}:{normalized['uri']}' not found",
                    self._resolver.suggest_resources(snapshot, normalized["server"], normalized["uri"]),
                )
            return await self._read_resource(snapshot, resource, mode=mode)

        resource_template = snapshot.resource_template(
            normalized["server"], normalized["uriTemplate"]
        )
        if resource_template is None:
            return self._error(
                snapshot,
                "RESOURCE_TEMPLATE_NOT_FOUND",
                f"Resource template '{normalized['server']}:{normalized['uriTemplate']}' not found",
                self._resolver.suggest_resource_templates(
                    snapshot, normalized["server"], normalized["uriTemplate"]
                ),
            )
        return await self._read_resource_template(
            snapshot,
            resource_template,
            arguments=normalized.get("arguments", {}),
            mode=mode,
        )

    async def _read_resource(
        self,
        snapshot: CatalogSnapshot,
        resource: ResourceRecord,
        *,
        mode: str,
    ) -> dict[str, Any]:
        if mode == "preview":
            return {
                "ok": True,
                "mode": "preview",
                "selector": resource.selector,
                "resource": {
                    "server": resource.server,
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mimeType": resource.mime_type,
                },
                "meta": {
                    "snapshot_version": snapshot.version,
                },
            }

        try:
            contents = await self._backend.read_resource(resource.server, resource.uri)
        except Exception as exc:
            return self._error(
                snapshot,
                "RESOURCE_READ_FAILED",
                str(exc),
                self._resolver.suggest_resources(snapshot, resource.server, resource.uri),
            )

        return {
            "ok": True,
            "mode": "read",
            "summary": f"{resource.server}:{resource.uri}",
            "contents": _normalize_resource_contents(contents),
            "meta": {
                "selector": resource.selector,
                "snapshot_version": snapshot.version,
            },
        }

    async def _read_resource_template(
        self,
        snapshot: CatalogSnapshot,
        resource_template: ResourceTemplateRecord,
        *,
        arguments: dict[str, str],
        mode: str,
    ) -> dict[str, Any]:
        if mode == "preview":
            return {
                "ok": True,
                "mode": "preview",
                "selector": resource_template.selector,
                "resource_template": {
                    "server": resource_template.server,
                    "uriTemplate": resource_template.uri_template,
                    "name": resource_template.name,
                    "description": resource_template.description,
                    "arguments": list(resource_template.arguments),
                },
                "meta": {
                    "snapshot_version": snapshot.version,
                },
            }

        try:
            concrete_uri = expand_uri_template(resource_template.uri_template, arguments)
        except ValueError as exc:
            return self._error(snapshot, "INVALID_TEMPLATE_ARGUMENTS", str(exc), [])

        try:
            contents = await self._backend.read_resource(resource_template.server, concrete_uri)
        except Exception as exc:
            return self._error(
                snapshot,
                "RESOURCE_READ_FAILED",
                str(exc),
                self._resolver.suggest_resource_templates(
                    snapshot, resource_template.server, resource_template.uri_template
                ),
            )

        return {
            "ok": True,
            "mode": "read",
            "summary": f"{resource_template.server}:{concrete_uri}",
            "contents": _normalize_resource_contents(contents),
            "meta": {
                "selector": resource_template.selector,
                "resolved_uri": concrete_uri,
                "snapshot_version": snapshot.version,
            },
        }

    def _error(
        self,
        snapshot: CatalogSnapshot,
        code: str,
        message: str,
        suggestions: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "code": code,
            "message": message,
            "suggestions": suggestions,
            "meta": {"snapshot_version": snapshot.version},
        }


class ProjectionSurface:
    """Agent-oriented invoke/read surface."""

    surface_kind = "projection"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

        @asynccontextmanager
        async def server_lifespan(_: FastMCP[Any]) -> AsyncIterator[dict[str, Any]]:
            await self.runtime.initialize()
            try:
                yield {}
            finally:
                await self.runtime.close()

        self.server = FastMCP("MCP Compact", lifespan=server_lifespan)
        self.runtime.register_surface(self)

        @self.server.tool(name="invoke")
        async def invoke(
            ref: dict[str, Any],
            arguments: dict[str, Any] | None = None,
            mode: str = "call",
        ) -> str:
            payload = await self.runtime.invoke(ref, arguments, mode=mode)
            return json.dumps(payload, ensure_ascii=False)

        @self.server.tool(name="read")
        async def read(
            ref: dict[str, Any],
            mode: str = "read",
        ) -> str:
            payload = await self.runtime.read(ref, mode=mode)
            return json.dumps(payload, ensure_ascii=False)

    async def sync(self, snapshot: CatalogSnapshot) -> None:
        self.apply_snapshot(snapshot)

    def apply_snapshot(self, snapshot: CatalogSnapshot) -> None:
        del snapshot
        invoke = _find_component_by_name(self.server, "invoke")
        read = _find_component_by_name(self.server, "read")
        if invoke is not None:
            invoke.description = self.runtime.compile_invoke_description()
        if read is not None:
            read.description = self.runtime.compile_read_description()


def _find_component_by_name(server: FastMCP, name: str) -> Any | None:
    for component in server._local_provider._components.values():
        if getattr(component, "name", None) == name:
            return component
    return None


def _normalize_tool_selector(ref: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(ref, dict):
        return None
    server = ref.get("server")
    name = ref.get("name")
    if not isinstance(server, str) or not isinstance(name, str):
        return None
    return {"server": server, "name": name}


def _normalize_read_selector(ref: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(ref, dict):
        return None

    server = ref.get("server")
    if not isinstance(server, str):
        return None

    uri = ref.get("uri")
    if isinstance(uri, str):
        return {"server": server, "uri": uri}

    uri_template = ref.get("uriTemplate")
    if isinstance(uri_template, str):
        arguments = ref.get("arguments") or {}
        if not isinstance(arguments, dict):
            return None
        return {
            "server": server,
            "uriTemplate": uri_template,
            "arguments": {
                key: str(value)
                for key, value in arguments.items()
                if isinstance(key, str)
            },
        }

    return None


def _normalize_tool_output(result: Any) -> Any:
    if isinstance(result, FastMcpCallToolResult):
        if result.data is not None:
            return result.data
        if result.structured_content is not None:
            return result.structured_content
        return _normalize_content_blocks(result.content)

    if isinstance(result, mcp_types.CallToolResult):
        if result.structuredContent is not None:
            return result.structuredContent
        return _normalize_content_blocks(result.content)

    if hasattr(result, "structuredContent") and getattr(result, "structuredContent") is not None:
        return getattr(result, "structuredContent")
    if hasattr(result, "data") and getattr(result, "data") is not None:
        return getattr(result, "data")
    if hasattr(result, "content"):
        return _normalize_content_blocks(getattr(result, "content"))
    return result


def _normalize_content_blocks(content_blocks: list[Any]) -> Any:
    normalized: list[Any] = []
    for block in content_blocks:
        if isinstance(block, mcp_types.TextContent):
            normalized.append({"type": "text", "text": block.text})
        elif isinstance(block, mcp_types.ImageContent):
            normalized.append(
                {
                    "type": "image",
                    "mimeType": block.mimeType,
                    "data": block.data,
                }
            )
        elif isinstance(block, mcp_types.EmbeddedResource):
            normalized.append(
                {
                    "type": "resource",
                    "resource": block.resource.model_dump(by_alias=True, exclude_none=True),
                }
            )
        elif hasattr(block, "text"):
            normalized.append({"type": "text", "text": getattr(block, "text")})
        else:
            normalized.append(str(block))

    if len(normalized) == 1 and isinstance(normalized[0], dict) and normalized[0].get("type") == "text":
        return normalized[0]["text"]
    return normalized


def _normalize_resource_contents(contents: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for content in contents:
        if isinstance(content, mcp_types.TextResourceContents):
            normalized.append(
                {
                    "uri": str(content.uri),
                    "text": content.text,
                    "mimeType": content.mimeType,
                }
            )
        elif isinstance(content, mcp_types.BlobResourceContents):
            normalized.append(
                {
                    "uri": str(content.uri),
                    "blob": content.blob,
                    "mimeType": content.mimeType,
                }
            )
        elif isinstance(content, dict):
            normalized.append(content)
        elif hasattr(content, "text"):
            normalized.append(
                {
                    "uri": str(getattr(content, "uri", "")),
                    "text": getattr(content, "text"),
                    "mimeType": getattr(content, "mimeType", None),
                }
            )
        elif hasattr(content, "blob"):
            normalized.append(
                {
                    "uri": str(getattr(content, "uri", "")),
                    "blob": getattr(content, "blob"),
                    "mimeType": getattr(content, "mimeType", None),
                }
            )
        else:
            normalized.append({"text": json.dumps(content, ensure_ascii=False)})
    return normalized


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
