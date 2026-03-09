"""Projection and native execution routing."""

from __future__ import annotations

import json
from typing import Any, Protocol, cast

import mcp.types as mcp_types
from fastmcp.client.client import CallToolResult as FastMcpCallToolResult

from mcpx.catalog import (
    ResourceRecord,
    ResourceTemplateRecord,
    ServerCapabilitiesRecord,
    example_from_schema,
    expand_uri_template,
)
from mcpx.resolver import Resolver
from mcpx.snapshot import CatalogSnapshot

__all__ = ["ExecutionRouter"]


class ExecutionBackend(Protocol):
    """Execution backend protocol implemented by upstream pools/hubs."""

    async def call_tool(self, server: str, name: str, arguments: dict[str, Any] | None = None) -> Any:
        ...

    async def call_tool_request(
        self,
        server: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        ...

    async def read_resource(self, server: str, uri: str) -> list[Any]:
        ...

    async def get_prompt(
        self, server: str, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.GetPromptResult:
        ...

    async def complete(
        self,
        server: str,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        ...

    async def get_task_status(self, server: str, task_id: str) -> mcp_types.GetTaskResult:
        ...

    async def get_task_result(self, server: str, task_id: str) -> dict[str, Any]:
        ...

    async def cancel_task(self, server: str, task_id: str) -> mcp_types.CancelTaskResult:
        ...


class ExecutionRouter:
    """Projection and native execution router."""

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

        capabilities = snapshot.server_capabilities.get(tool.server, ServerCapabilitiesRecord())
        if mode == "validate":
            return {
                "ok": True,
                "mode": "validate",
                "summary": tool.display_name,
                "selector": tool.selector,
                "snapshot_version": snapshot.version,
                "expected_input": tool.input_schema,
                "example": example_from_schema(tool.input_schema),
                "completions": {"supported": capabilities.completions},
                "tasks_supported": capabilities.tasks,
                "meta": {
                    "selector": tool.selector,
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
                "tasks_supported": capabilities.tasks,
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

    async def native_call_tool(
        self,
        server: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        ttl: int | None = None,
    ) -> mcp_types.CreateTaskResult | mcp_types.CallToolResult:
        return await self._backend.call_tool_request(server, name, arguments or {}, ttl)

    async def native_read_resource(self, server: str, uri: str) -> list[Any]:
        return await self._backend.read_resource(server, uri)

    async def native_get_prompt(
        self, server: str, name: str, arguments: dict[str, Any] | None = None
    ) -> mcp_types.GetPromptResult:
        return await self._backend.get_prompt(server, name, arguments or {})

    async def native_complete(
        self,
        server: str,
        ref: mcp_types.ResourceTemplateReference | mcp_types.PromptReference,
        argument: dict[str, str],
        context_arguments: dict[str, Any] | None = None,
    ) -> mcp_types.Completion:
        return await self._backend.complete(server, ref, argument, context_arguments)

    async def native_get_task_status(self, server: str, task_id: str) -> mcp_types.GetTaskResult:
        return await self._backend.get_task_status(server, task_id)

    async def native_get_task_result(self, server: str, task_id: str) -> dict[str, Any]:
        return await self._backend.get_task_result(server, task_id)

    async def native_cancel_task(self, server: str, task_id: str) -> mcp_types.CancelTaskResult:
        return await self._backend.cancel_task(server, task_id)

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
                "snapshot_version": snapshot.version,
                "resource": {
                    "server": resource.server,
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mimeType": resource.mime_type,
                },
                "completions": {"supported": False},
                "tasks_supported": False,
                "meta": {
                    "selector": resource.selector,
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
        capabilities = snapshot.server_capabilities.get(
            resource_template.server, ServerCapabilitiesRecord()
        )
        if mode == "preview":
            return {
                "ok": True,
                "mode": "preview",
                "selector": resource_template.selector,
                "snapshot_version": snapshot.version,
                "resource_template": {
                    "server": resource_template.server,
                    "uriTemplate": resource_template.uri_template,
                    "name": resource_template.name,
                    "description": resource_template.description,
                    "arguments": list(resource_template.arguments),
                },
                "completions": {"supported": capabilities.completions},
                "tasks_supported": capabilities.tasks,
                "meta": {
                    "selector": resource_template.selector,
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
