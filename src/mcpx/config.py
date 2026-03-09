"""MCPX 配置模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass

__all__ = ["McpServerConfig", "ProxyConfig"]


class McpServerConfig(BaseModel):
    """MCP server configuration.

    Supports two transport types:
    - stdio: Uses command + args to spawn a subprocess
    - http: Uses url + optional headers for HTTP/SSE transport

    Note: The server name is now the key in the mcpServers dictionary,
    not a field inside this model (Claude Code compatibility).
    """

    type: str = "stdio"  # "stdio" or "http"

    # stdio transport fields
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None

    # http transport fields
    url: str | None = None
    headers: dict[str, str] | None = None

    # Server-level enable switch used during runtime initialization.
    enabled: bool = True

    def validate_for_server(self, server_name: str) -> None:
        """Validate that required fields are present based on type.

        Args:
            server_name: The server name (used for error messages).
        """
        if self.type == "stdio":
            if not self.command:
                raise ValueError(f"Server '{server_name}': stdio type requires 'command' field")
        elif self.type == "http":
            if not self.url:
                raise ValueError(f"Server '{server_name}': http type requires 'url' field")
        else:
            raise ValueError(
                f"Server '{server_name}': unknown type '{self.type}', must be 'stdio' or 'http'"
            )


class ProxyConfig(BaseModel):
    """Proxy configuration."""

    mcpServers: dict[str, McpServerConfig] = Field(  # noqa: N815
        default_factory=dict
    )

    # Legacy operational settings retained for compatibility. The 2.0 runtime
    # does not currently consume them directly, but existing config files may
    # still include them.
    health_check_enabled: bool = True
    health_check_interval: int = 30  # seconds
    health_check_timeout: int = 5  # seconds
    health_check_failure_threshold: int = 2  # consecutive failures

    toon_compression_enabled: bool = True
    toon_compression_min_size: int = 1  # minimum items/keys to compress

    schema_compression_enabled: bool = True

    include_structured_content: bool = False

    disabled_tools: list[str] = Field(default_factory=list)  # legacy 1.x format: "server.tool"

    model_config = {"extra": "ignore"}
