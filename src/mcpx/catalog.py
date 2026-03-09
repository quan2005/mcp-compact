"""Canonical MCPX catalog records and helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Literal

Category = Literal["tools", "resources", "resource_templates", "prompts"]
WatchMode = Literal["watcher", "poll"]

ALL_CATEGORIES: frozenset[Category] = frozenset(
    {"tools", "resources", "resource_templates", "prompts"}
)

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
    "ALL_CATEGORIES",
    "Category",
    "PromptArgumentRecord",
    "PromptRecord",
    "ResourceRecord",
    "ResourceTemplateRecord",
    "ServerCapabilitiesRecord",
    "ServerCatalog",
    "ToolRecord",
    "WatchMode",
    "build_prompt_record",
    "build_resource_record",
    "build_resource_template_record",
    "build_tool_record",
    "example_from_schema",
    "expand_uri_template",
    "extract_capabilities",
    "extract_placeholders",
    "tokenize",
]


@dataclass(frozen=True)
class ServerCapabilitiesRecord:
    """Subset of upstream capabilities surfaced by MCPX."""

    tools_list_changed: bool = False
    resources_subscribe: bool = False
    resources_list_changed: bool = False
    prompts_list_changed: bool = False
    completions: bool = False
    tasks: bool = False


@dataclass(frozen=True)
class ToolRecord:
    """Canonical tool metadata harvested from an upstream server."""

    server: str
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    annotations: Any | None = None
    icons: tuple[Any, ...] = ()
    family: str = "misc"
    required_args: tuple[str, ...] = ()
    mutating: bool = False

    @property
    def selector(self) -> dict[str, str]:
        return {"server": self.server, "name": self.name}

    @property
    def display_name(self) -> str:
        return f"{self.server}.{self.name}"

    @property
    def exposed_name(self) -> str:
        return self.display_name


@dataclass(frozen=True)
class ResourceRecord:
    """Canonical direct resource metadata."""

    server: str
    uri: str
    name: str
    title: str | None
    description: str
    mime_type: str | None
    size: int | None
    annotations: Any | None = None
    icons: tuple[Any, ...] = ()

    @property
    def selector(self) -> dict[str, str]:
        return {"server": self.server, "uri": self.uri}

    @property
    def exposed_uri(self) -> str:
        return f"mcpx://{self.server}/{self.uri}"


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
    annotations: Any | None = None
    icons: tuple[Any, ...] = ()

    @property
    def selector(self) -> dict[str, str]:
        return {"server": self.server, "uriTemplate": self.uri_template}

    @property
    def exposed_uri_template(self) -> str:
        return f"mcpx://{self.server}/{self.uri_template}"


@dataclass(frozen=True)
class PromptArgumentRecord:
    """Canonical prompt argument metadata."""

    name: str
    description: str | None
    required: bool


@dataclass(frozen=True)
class PromptRecord:
    """Canonical prompt metadata."""

    server: str
    name: str
    title: str | None
    description: str
    arguments: tuple[PromptArgumentRecord, ...]
    icons: tuple[Any, ...] = ()

    @property
    def selector(self) -> dict[str, str]:
        return {"server": self.server, "name": self.name}

    @property
    def exposed_name(self) -> str:
        return f"{self.server}.{self.name}"


@dataclass(frozen=True)
class ServerCatalog:
    """Per-server canonical catalog plus runtime state."""

    server: str
    capabilities: ServerCapabilitiesRecord
    tools: tuple[ToolRecord, ...] = ()
    resources: tuple[ResourceRecord, ...] = ()
    resource_templates: tuple[ResourceTemplateRecord, ...] = ()
    prompts: tuple[PromptRecord, ...] = ()
    degraded: bool = False
    watch_mode: WatchMode = "watcher"

    @classmethod
    def empty(cls, server: str) -> "ServerCatalog":
        return cls(server=server, capabilities=ServerCapabilitiesRecord())

    def with_updates(
        self,
        *,
        capabilities: ServerCapabilitiesRecord | None = None,
        tools: tuple[ToolRecord, ...] | None = None,
        resources: tuple[ResourceRecord, ...] | None = None,
        resource_templates: tuple[ResourceTemplateRecord, ...] | None = None,
        prompts: tuple[PromptRecord, ...] | None = None,
        degraded: bool | None = None,
        watch_mode: WatchMode | None = None,
    ) -> "ServerCatalog":
        return replace(
            self,
            capabilities=capabilities if capabilities is not None else self.capabilities,
            tools=tools if tools is not None else self.tools,
            resources=resources if resources is not None else self.resources,
            resource_templates=(
                resource_templates
                if resource_templates is not None
                else self.resource_templates
            ),
            prompts=prompts if prompts is not None else self.prompts,
            degraded=degraded if degraded is not None else self.degraded,
            watch_mode=watch_mode if watch_mode is not None else self.watch_mode,
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


def extract_capabilities(initialize_result: Any) -> ServerCapabilitiesRecord:
    capabilities = getattr(initialize_result, "capabilities", None)
    return ServerCapabilitiesRecord(
        tools_list_changed=bool(_get_capability_value(capabilities, "tools", "listChanged")),
        resources_subscribe=bool(_get_capability_value(capabilities, "resources", "subscribe")),
        resources_list_changed=bool(
            _get_capability_value(capabilities, "resources", "listChanged")
        ),
        prompts_list_changed=bool(_get_capability_value(capabilities, "prompts", "listChanged")),
        completions=bool(getattr(capabilities, "completions", None)),
        tasks=bool(getattr(capabilities, "tasks", None)),
    )


def build_tool_record(server: str, raw_tool: Any) -> ToolRecord:
    input_schema = getattr(raw_tool, "inputSchema", {}) or {}
    required_args = tuple(input_schema.get("required", []))
    description = getattr(raw_tool, "description", None) or ""
    name = getattr(raw_tool, "name")
    annotations = getattr(raw_tool, "annotations", None)
    return ToolRecord(
        server=server,
        name=name,
        title=getattr(raw_tool, "title", None),
        description=description,
        input_schema=input_schema,
        annotations=annotations,
        icons=tuple(getattr(raw_tool, "icons", None) or []),
        family=_derive_family(name, description),
        required_args=required_args,
        mutating=_is_mutating_tool(name, annotations),
    )


def build_resource_record(server: str, raw_resource: Any) -> ResourceRecord:
    return ResourceRecord(
        server=server,
        uri=str(getattr(raw_resource, "uri")),
        name=getattr(raw_resource, "name"),
        title=getattr(raw_resource, "title", None),
        description=getattr(raw_resource, "description", None) or "",
        mime_type=getattr(raw_resource, "mimeType", None),
        size=getattr(raw_resource, "size", None),
        annotations=getattr(raw_resource, "annotations", None),
        icons=tuple(getattr(raw_resource, "icons", None) or []),
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
        annotations=getattr(raw_template, "annotations", None),
        icons=tuple(getattr(raw_template, "icons", None) or []),
    )


def build_prompt_record(server: str, raw_prompt: Any) -> PromptRecord:
    prompt_arguments = tuple(
        PromptArgumentRecord(
            name=getattr(argument, "name"),
            description=getattr(argument, "description", None),
            required=bool(getattr(argument, "required", False)),
        )
        for argument in (getattr(raw_prompt, "arguments", None) or [])
    )
    return PromptRecord(
        server=server,
        name=getattr(raw_prompt, "name"),
        title=getattr(raw_prompt, "title", None),
        description=getattr(raw_prompt, "description", None) or "",
        arguments=prompt_arguments,
        icons=tuple(getattr(raw_prompt, "icons", None) or []),
    )


def example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    example: dict[str, Any] = {}
    if not isinstance(properties, dict):
        return example

    for name, property_schema in properties.items():
        if not isinstance(property_schema, dict):
            example[name] = "<value>"
            continue

        if "enum" in property_schema and property_schema["enum"]:
            example[name] = property_schema["enum"][0]
            continue

        property_type = property_schema.get("type")
        if property_type == "string":
            example[name] = "<string>"
        elif property_type in {"number", "integer"}:
            example[name] = 0
        elif property_type == "boolean":
            example[name] = False
        elif property_type == "array":
            example[name] = []
        elif property_type == "object":
            example[name] = {}
        else:
            example[name] = "<value>"

    return example


def _derive_family(name: str, description: str) -> str:
    normalized_name = name.replace("-", "_")
    if "_" in normalized_name:
        return normalized_name.split("_", 1)[0]

    tokens = tokenize(description)
    return tokens[0] if tokens else "misc"


def _is_mutating_tool(name: str, annotations: Any | None) -> bool:
    if annotations is not None:
        read_only_hint = getattr(annotations, "readOnlyHint", None)
        if read_only_hint is True:
            return False
        if read_only_hint is False:
            return True
    return name.startswith(_MUTATING_VERBS)


def _get_capability_value(capabilities: Any, section: str, attribute: str) -> Any:
    if capabilities is None:
        return None
    section_value = getattr(capabilities, section, None)
    if section_value is None:
        return None
    return getattr(section_value, attribute, None)
