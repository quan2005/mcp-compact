"""Canonical resolution and deterministic suggestions."""

from __future__ import annotations

from typing import Any

from mcpx.catalog import ResourceRecord, ResourceTemplateRecord, ToolRecord, tokenize
from mcpx.snapshot import CatalogSnapshot

__all__ = ["Resolver"]


class Resolver:
    """Resolve canonical refs and generate stable suggestions."""

    def resolve_tool(self, snapshot: CatalogSnapshot, ref: dict[str, Any] | None) -> ToolRecord | None:
        if not isinstance(ref, dict):
            return None
        server = ref.get("server")
        name = ref.get("name")
        if not isinstance(server, str) or not isinstance(name, str):
            return None
        return snapshot.tool(server, name)

    def resolve_read_ref(
        self, snapshot: CatalogSnapshot, ref: dict[str, Any] | None
    ) -> ResourceRecord | ResourceTemplateRecord | None:
        if not isinstance(ref, dict):
            return None
        server = ref.get("server")
        if not isinstance(server, str):
            return None

        uri = ref.get("uri")
        if isinstance(uri, str):
            return snapshot.resource(server, uri)

        uri_template = ref.get("uriTemplate")
        if isinstance(uri_template, str):
            return snapshot.resource_template(server, uri_template)

        return None

    def suggest_tools(self, snapshot: CatalogSnapshot, server: str, name: str) -> list[dict[str, str]]:
        query = f"{server} {name}"
        ranked = self._rank(
            query,
            snapshot.projection_index.tools,
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
            snapshot.projection_index.resources,
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
            snapshot.projection_index.resource_templates,
            field_values=lambda resource_template: {
                "name": f"{resource_template.name} {resource_template.uri_template}",
                "title": resource_template.title or "",
                "description": resource_template.description,
                "required_args": " ".join(resource_template.arguments),
                "server": resource_template.server,
            },
            stable_key=lambda resource_template: (resource_template.server, resource_template.uri_template),
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
