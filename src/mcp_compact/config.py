"""Minimal MCP Compact configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["McpServerConfig", "ProxyConfig"]


class McpServerConfig(BaseModel):
    """Single upstream MCP server config."""

    type: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None

    enabled: bool = True
    model_config = {"extra": "forbid"}

    def validate_for_server(self, server_name: str) -> None:
        """Validate that required fields are present based on type.

        Args:
            server_name: The server name (used for error messages).
        """
        if self.type != "stdio":
            raise ValueError(
                f"Server '{server_name}': mcp-compact only supports 'stdio' transport"
            )
        if not self.command:
            raise ValueError(f"Server '{server_name}': stdio type requires 'command' field")


class ProxyConfig(BaseModel):
    """Proxy configuration."""

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)  # noqa: N815

    model_config = {"extra": "forbid"}
