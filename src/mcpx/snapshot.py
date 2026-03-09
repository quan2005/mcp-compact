"""Immutable catalog snapshots and derived indexes."""

from __future__ import annotations

from dataclasses import dataclass

from mcpx.catalog import (
    PromptRecord,
    ResourceRecord,
    ResourceTemplateRecord,
    ServerCapabilitiesRecord,
    ServerCatalog,
    ToolRecord,
)

__all__ = [
    "CatalogSnapshot",
    "NativeIndex",
    "ProjectionIndex",
    "SnapshotBuilder",
]


@dataclass(frozen=True)
class NativeIndex:
    """Native surface lookup tables."""

    tools: tuple[ToolRecord, ...]
    resources: tuple[ResourceRecord, ...]
    resource_templates: tuple[ResourceTemplateRecord, ...]
    prompts: tuple[PromptRecord, ...]
    tools_by_exposed_name: dict[str, ToolRecord]
    resources_by_exposed_uri: dict[str, ResourceRecord]
    resource_templates_by_exposed_uri: dict[str, ResourceTemplateRecord]
    prompts_by_exposed_name: dict[str, PromptRecord]


@dataclass(frozen=True)
class ProjectionIndex:
    """Projection surface index for compilation and resolution."""

    tools: tuple[ToolRecord, ...]
    tool_families: dict[str, tuple[ToolRecord, ...]]
    resources: tuple[ResourceRecord, ...]
    resource_templates: tuple[ResourceTemplateRecord, ...]
    prompts: tuple[PromptRecord, ...]


@dataclass(frozen=True)
class CatalogSnapshot:
    """Immutable runtime snapshot."""

    version: int
    servers: dict[str, ServerCatalog]
    native_index: NativeIndex
    projection_index: ProjectionIndex
    server_capabilities: dict[str, ServerCapabilitiesRecord]

    def tool(self, server: str, name: str) -> ToolRecord | None:
        for tool in self.native_index.tools:
            if tool.server == server and tool.name == name:
                return tool
        return None

    def resource(self, server: str, uri: str) -> ResourceRecord | None:
        for resource in self.native_index.resources:
            if resource.server == server and resource.uri == uri:
                return resource
        return None

    def resource_template(self, server: str, uri_template: str) -> ResourceTemplateRecord | None:
        for resource_template in self.native_index.resource_templates:
            if (
                resource_template.server == server
                and resource_template.uri_template == uri_template
            ):
                return resource_template
        return None

    def prompt(self, server: str, name: str) -> PromptRecord | None:
        for prompt in self.native_index.prompts:
            if prompt.server == server and prompt.name == name:
                return prompt
        return None


class SnapshotBuilder:
    """Build immutable snapshots from server-local catalogs."""

    def build(self, version: int, server_catalogs: dict[str, ServerCatalog]) -> CatalogSnapshot:
        ordered_catalogs = [server_catalogs[name] for name in sorted(server_catalogs)]

        tools = tuple(
            sorted(
                (tool for catalog in ordered_catalogs for tool in catalog.tools),
                key=lambda item: (item.family, item.server, item.name),
            )
        )
        resources = tuple(
            sorted(
                (resource for catalog in ordered_catalogs for resource in catalog.resources),
                key=lambda item: (item.server, item.uri),
            )
        )
        resource_templates = tuple(
            sorted(
                (
                    resource_template
                    for catalog in ordered_catalogs
                    for resource_template in catalog.resource_templates
                ),
                key=lambda item: (item.server, item.uri_template),
            )
        )
        prompts = tuple(
            sorted(
                (prompt for catalog in ordered_catalogs for prompt in catalog.prompts),
                key=lambda item: (item.server, item.name),
            )
        )

        tool_families: dict[str, list[ToolRecord]] = {}
        for tool in tools:
            tool_families.setdefault(tool.family, []).append(tool)

        native_index = NativeIndex(
            tools=tools,
            resources=resources,
            resource_templates=resource_templates,
            prompts=prompts,
            tools_by_exposed_name={tool.exposed_name: tool for tool in tools},
            resources_by_exposed_uri={resource.exposed_uri: resource for resource in resources},
            resource_templates_by_exposed_uri={
                resource_template.exposed_uri_template: resource_template
                for resource_template in resource_templates
            },
            prompts_by_exposed_name={prompt.exposed_name: prompt for prompt in prompts},
        )
        projection_index = ProjectionIndex(
            tools=tools,
            tool_families={
                family: tuple(
                    sorted(family_tools, key=lambda item: (item.server, item.name))
                )
                for family, family_tools in sorted(tool_families.items())
            },
            resources=resources,
            resource_templates=resource_templates,
            prompts=prompts,
        )

        return CatalogSnapshot(
            version=version,
            servers={catalog.server: catalog for catalog in ordered_catalogs},
            native_index=native_index,
            projection_index=projection_index,
            server_capabilities={
                catalog.server: catalog.capabilities for catalog in ordered_catalogs
            },
        )
