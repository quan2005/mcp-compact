"""Canonical catalog records, helpers, and snapshot building."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PLACEHOLDER_PATTERN = re.compile(r"\{([^}]+)\}")
_TOKEN_PATTERN = re.compile(r"[\s._:/{}-]+")
_MUTATING_VERBS = (
    "create",
    "delete",
    "remove",
    "update",
    "write",
    "save",
    "set",
    "rename",
    "move",
)

__all__ = [
    "CatalogSnapshot",
    "ResourceRecord",
    "ResourceTemplateRecord",
    "ServerCatalog",
    "ToolRecord",
    "build_resource_record",
    "build_resource_template_record",
    "build_snapshot",
    "build_tool_record",
    "example_from_schema",
    "expand_uri_template",
    "extract_placeholders",
    "tokenize",
]


@dataclass(frozen=True)
class ToolRecord:
    """Canonical tool metadata harvested from an upstream server."""

    server: str
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    family: str
    required_args: tuple[str, ...]
    mutating: bool

    @property
    def selector(self) -> dict[str, str]:
        return {"server": self.server, "name": self.name}

    @property
    def display_name(self) -> str:
        return f"{self.server}.{self.name}"


@dataclass(frozen=True)
class ResourceRecord:
    """Canonical direct resource metadata."""

    server: str
    uri: str
    name: str
    title: str | None
    description: str
    mime_type: str | None

    @property
    def selector(self) -> dict[str, str]:
        return {"server": self.server, "uri": self.uri}


@dataclass(frozen=True)
class ResourceTemplateRecord:
    """Canonical resource template metadata."""

    server: str
    uri_template: str
    name: str
    title: str | None
    description: str
    mime_type: str | None
    arguments: tuple[str, ...]

    @property
    def selector(self) -> dict[str, str]:
        return {"server": self.server, "uriTemplate": self.uri_template}


@dataclass(frozen=True)
class ServerCatalog:
    """Per-server canonical catalog."""

    server: str
    tools: tuple[ToolRecord, ...] = ()
    resources: tuple[ResourceRecord, ...] = ()
    resource_templates: tuple[ResourceTemplateRecord, ...] = ()

    @classmethod
    def empty(cls, server: str) -> "ServerCatalog":
        return cls(server=server)


@dataclass(frozen=True)
class CatalogSnapshot:
    """Immutable runtime snapshot."""

    version: int
    servers: dict[str, ServerCatalog]
    tools: tuple[ToolRecord, ...]
    tool_families: dict[str, tuple[ToolRecord, ...]]
    resources: tuple[ResourceRecord, ...]
    resource_templates: tuple[ResourceTemplateRecord, ...]

    def tool(self, server: str, name: str) -> ToolRecord | None:
        for tool in self.tools:
            if tool.server == server and tool.name == name:
                return tool
        return None

    def resource(self, server: str, uri: str) -> ResourceRecord | None:
        for resource in self.resources:
            if resource.server == server and resource.uri == uri:
                return resource
        return None

    def resource_template(self, server: str, uri_template: str) -> ResourceTemplateRecord | None:
        for resource_template in self.resource_templates:
            if (
                resource_template.server == server
                and resource_template.uri_template == uri_template
            ):
                return resource_template
        return None


def build_snapshot(version: int, server_catalogs: dict[str, ServerCatalog]) -> CatalogSnapshot:
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

    tool_families: dict[str, list[ToolRecord]] = {}
    for tool in tools:
        tool_families.setdefault(tool.family, []).append(tool)

    return CatalogSnapshot(
        version=version,
        servers={catalog.server: catalog for catalog in ordered_catalogs},
        tools=tools,
        tool_families={
            family: tuple(sorted(family_tools, key=lambda item: (item.server, item.name)))
            for family, family_tools in sorted(tool_families.items())
        },
        resources=resources,
        resource_templates=resource_templates,
    )


def tokenize(text: str) -> tuple[str, ...]:
    return tuple(token for token in _TOKEN_PATTERN.split(text.lower()) if token)


def extract_placeholders(uri_template: str) -> tuple[str, ...]:
    arguments: list[str] = []
    for placeholder in _PLACEHOLDER_PATTERN.findall(uri_template):
        if placeholder.startswith("?"):
            arguments.extend(part.strip() for part in placeholder[1:].split(",") if part.strip())
            continue
        arguments.append(placeholder.rstrip("*"))

    deduped: list[str] = []
    for name in arguments:
        if name not in deduped:
            deduped.append(name)
    return tuple(deduped)


def expand_uri_template(uri_template: str, arguments: dict[str, str]) -> str:
    required_arguments = extract_placeholders(uri_template)
    missing = [name for name in required_arguments if name not in arguments]
    if missing:
        raise ValueError(f"Missing template arguments: {missing}")

    expanded = uri_template
    for name in required_arguments:
        expanded = expanded.replace(f"{{{name}}}", arguments[name])
        expanded = expanded.replace(f"{{{name}*}}", arguments[name])

    query_placeholders = re.findall(r"\{\?([^}]+)\}", expanded)
    for placeholder in query_placeholders:
        names = [item.strip() for item in placeholder.split(",") if item.strip()]
        pairs = [f"{name}={arguments[name]}" for name in names if name in arguments]
        expanded = expanded.replace(f"{{?{placeholder}}}", f"?{'&'.join(pairs)}" if pairs else "")

    return expanded


def build_tool_record(server: str, raw_tool: Any) -> ToolRecord:
    input_schema = getattr(raw_tool, "inputSchema", {}) or {}
    required_args = tuple(input_schema.get("required", []))
    description = getattr(raw_tool, "description", None) or ""
    name = getattr(raw_tool, "name")
    return ToolRecord(
        server=server,
        name=name,
        title=getattr(raw_tool, "title", None),
        description=description,
        input_schema=input_schema,
        family=_derive_family(name, description),
        required_args=required_args,
        mutating=_is_mutating_tool(name),
    )


def build_resource_record(server: str, raw_resource: Any) -> ResourceRecord:
    return ResourceRecord(
        server=server,
        uri=str(getattr(raw_resource, "uri")),
        name=getattr(raw_resource, "name"),
        title=getattr(raw_resource, "title", None),
        description=getattr(raw_resource, "description", None) or "",
        mime_type=getattr(raw_resource, "mimeType", None),
    )


def build_resource_template_record(server: str, raw_template: Any) -> ResourceTemplateRecord:
    uri_template = str(getattr(raw_template, "uriTemplate"))
    return ResourceTemplateRecord(
        server=server,
        uri_template=uri_template,
        name=getattr(raw_template, "name"),
        title=getattr(raw_template, "title", None),
        description=getattr(raw_template, "description", None) or "",
        mime_type=getattr(raw_template, "mimeType", None),
        arguments=extract_placeholders(uri_template),
    )


def example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return {}

    example: dict[str, Any] = {}
    for key in required:
        if not isinstance(key, str):
            continue
        field_schema = properties.get(key, {})
        example[key] = _placeholder_for_field(field_schema)
    return example


def _placeholder_for_field(field_schema: Any) -> Any:
    if not isinstance(field_schema, dict):
        return "<value>"

    field_type = field_schema.get("type")
    if field_type == "string":
        return "<string>"
    if field_type == "integer":
        return 0
    if field_type == "number":
        return 0
    if field_type == "boolean":
        return False
    if field_type == "array":
        item_schema = field_schema.get("items", {})
        return [_placeholder_for_field(item_schema)]
    if field_type == "object":
        return {}
    return "<value>"


def _derive_family(name: str, description: str) -> str:
    tokens = list(tokenize(name))
    if tokens:
        return tokens[0]
    description_tokens = list(tokenize(description))
    if description_tokens:
        return description_tokens[0]
    return "misc"


def _is_mutating_tool(name: str) -> bool:
    return any(verb in tokenize(name) for verb in _MUTATING_VERBS)
