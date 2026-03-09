"""Projection surface description compiler."""

from __future__ import annotations

from dataclasses import dataclass

from mcpx.snapshot import CatalogSnapshot

__all__ = ["ProjectionBudget", "ProjectionCompiler"]


@dataclass(frozen=True)
class ProjectionBudget:
    """Fixed description budgets for the projection surface."""

    max_tool_families: int = 12
    max_tools_per_family: int = 6
    max_direct_resources: int = 16
    max_template_resources: int = 16
    max_prompt_hints: int = 8


class ProjectionCompiler:
    """Compile invoke/read descriptions from the projection index."""

    def __init__(self, budget: ProjectionBudget | None = None) -> None:
        self._budget = budget or ProjectionBudget()

    def compile_invoke_description(self, snapshot: CatalogSnapshot) -> str:
        index = snapshot.projection_index
        lines = [
            "Invoke an MCP tool through the projection surface.",
            "",
            "Use `ref.server` and `ref.name` with canonical upstream identifiers.",
            'Use `mode=\"validate\"` to inspect input requirements before calling.',
            "",
            "Available tool families:",
        ]

        selected_families = list(index.tool_families.items())[: self._budget.max_tool_families]
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

        if index.prompts:
            lines.extend(["", "Suggested workflows from prompts (reference only; not executable):"])
            for prompt in index.prompts[: self._budget.max_prompt_hints]:
                lines.append(
                    f"- {prompt.exposed_name}: {_truncate(prompt.description or prompt.name, 100)}"
                )

        lines.extend(
            [
                "",
                'Example: invoke(ref={"server": "notes", "name": "echo_note"}, arguments={"title": "...", "body": "..."})',
            ]
        )
        return "\n".join(lines)

    def compile_read_description(self, snapshot: CatalogSnapshot) -> str:
        index = snapshot.projection_index
        lines = [
            "Read MCP resources through the projection surface.",
            "",
            "Use `ref.server` with either `ref.uri` for direct resources or",
            "`ref.uriTemplate` plus template `arguments` for resource templates.",
            'Use `mode=\"preview\"` before reading when you need metadata first.',
            "",
            "Direct resources:",
        ]

        direct_resources = index.resources[: self._budget.max_direct_resources]
        if direct_resources:
            for resource in direct_resources:
                lines.append(
                    f"- {resource.server}:{resource.uri} | {resource.name} | {_truncate(resource.description, 96)}"
                )
        else:
            lines.append("- none")

        lines.extend(["", "Resource templates:"])
        templates = index.resource_templates[: self._budget.max_template_resources]
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


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
