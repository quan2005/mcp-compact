"""Public MCPX surfaces."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from mcpx.native import NativeSurface
from mcpx.snapshot import CatalogSnapshot

__all__ = ["NativeSurface", "ProjectionSurface"]


class ProjectionSurface:
    """Agent-oriented invoke/read surface."""

    surface_kind = "projection"

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.server = FastMCP("MCPX Projection")
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
