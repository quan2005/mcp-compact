"""MCPX - MCP proxy server."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import EmbeddedResource, ImageContent, TextContent

from mcpx.config import McpServerConfig, ProxyConfig
from mcpx.config_manager import ConfigManager
from mcpx.description import generate_tools_description, update_tool_descriptions
from mcpx.errors import MCPXError, ValidationError
from mcpx.port_utils import find_available_port
from mcpx.schema_ts import json_schema_to_typescript
from mcpx.server import ServerManager

logger = logging.getLogger(__name__)

__all__ = ["McpServerConfig", "ProxyConfig", "load_config", "create_server", "main"]


def load_config(config_path: Path) -> ProxyConfig:
    """Load configuration from file.

    Args:
        config_path: Path to config.json file

    Returns:
        ProxyConfig with server list

    Raises:
        SystemExit: If config file not found or invalid
    """
    if not config_path.exists():
        logger.error(f"Config file not found: {config_path}")
        sys.exit(1)

    try:
        with open(config_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        sys.exit(1)

    try:
        return ProxyConfig(**data)
    except Exception as e:
        logger.error(f"Invalid config structure: {e}")
        sys.exit(1)


def _maybe_compress_schema(
    input_schema: dict[str, object], enabled: bool
) -> str | dict[str, object]:
    """Compress input_schema to TypeScript format if enabled."""
    if enabled:
        return json_schema_to_typescript(input_schema, max_description_len=300)
    return input_schema


def create_server(
    config: ProxyConfig,
    manager: ServerManager | None = None,
    registry: Any = None,  # Backward compatibility
) -> FastMCP:
    """Create MCP server from configuration.

    Args:
        config: Proxy configuration
        manager: Optional pre-initialized ServerManager
        registry: Deprecated, use manager instead

    Returns:
        FastMCP server instance

    Note:
        Tool descriptions (invoke/read) are dynamically updated in lifespan
        after manager initialization. See update_tool_descriptions() in description.py.
    """
    mcp = FastMCP("MCPX")

    # Use provided manager or create new one
    # Support deprecated 'registry' parameter for backward compatibility
    active_manager = manager or registry or ServerManager(config)

    # Store for access in tools
    mcp._manager = active_manager  # type: ignore[attr-defined]
    mcp._config = config  # type: ignore[attr-defined]
    # Backward compatibility aliases
    mcp._registry = active_manager  # type: ignore[attr-defined]
    mcp._executor = active_manager  # type: ignore[attr-defined]

    @mcp.tool()
    async def invoke(
        method: str,
        arguments: dict[str, object] | None = None,
    ) -> (
        ToolResult
        | str
        | TextContent
        | ImageContent
        | EmbeddedResource
        | list[TextContent | ImageContent | EmbeddedResource]
    ):
        """Invoke an MCP tool.

        Args:
            method: Method identifier in "server.tool" format
            arguments: Tool arguments

        Example:
            invoke(method="filesystem.read_file", arguments={"path": "/tmp/file.txt"})

        Error Handling:
            When invoke fails, it returns helpful information:
            - Server not found: returns error + available_servers list
            - Tool not found: returns error + available_tools list
            - Invalid arguments: returns error + tool_schema

        Note: Tool list will be populated after server initialization.
        """
        manager: ServerManager = mcp._manager  # type: ignore[attr-defined]
        config: ProxyConfig = mcp._config  # type: ignore[attr-defined]

        # Parse method string
        parts = method.split(".", 1)
        if len(parts) != 2:
            return json.dumps(
                {"error": f"Invalid method format: '{method}'. Expected 'server.tool'"},
                ensure_ascii=False,
            )

        server_name, tool_name = parts

        try:
            result = await manager.call(server_name, tool_name, arguments or {})

            if not result.success:
                return json.dumps({"error": result.error}, ensure_ascii=False)

            raw_data = result.raw_data
            compressed_data = result.data

            # 多模态内容：直接返回
            if isinstance(raw_data, (TextContent, ImageContent, EmbeddedResource)):
                return raw_data

            # 包含多模态内容的列表
            if isinstance(raw_data, list):
                if any(
                    isinstance(item, (TextContent, ImageContent, EmbeddedResource))
                    for item in raw_data
                ):
                    return raw_data

            # 普通数据：返回 ToolResult
            if result.compressed and isinstance(compressed_data, str):
                if config.include_structured_content:
                    return ToolResult(
                        content=compressed_data, structured_content={"result": raw_data}
                    )
                return ToolResult(content=compressed_data)
            else:
                if config.include_structured_content:
                    return ToolResult(content=raw_data, structured_content={"result": raw_data})
                return ToolResult(content=raw_data)

        except MCPXError as e:
            error_dict = e.to_dict()
            # Apply schema compression if it's a validation error with schema
            if (
                isinstance(e, ValidationError)
                and e.tool_schema
                and config.schema_compression_enabled
            ):
                error_dict["tool_schema"] = json_schema_to_typescript(
                    e.tool_schema, max_description_len=300
                )
            return json.dumps(error_dict, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Unexpected error in invoke: {e}")
            return json.dumps({"error": str(e), "code": "UNEXPECTED_ERROR"}, ensure_ascii=False)

    @mcp.tool()
    async def read(
        server_name: str,
        uri: str,
    ) -> Any:
        """Read a resource from MCP servers.

        Args:
            server_name: Server name (required)
            uri: Resource URI (required)

        Returns:
            - Text resource: string content
            - Binary resource: dict with uri, mime_type, and blob (base64)
            - Multiple contents: list of content items

        Examples:
            read(server_name="filesystem", uri="file:///tmp/file.txt")

        Note: Resource list will be populated after server initialization.
        """
        manager: ServerManager = mcp._manager  # type: ignore[attr-defined]

        try:
            contents = await manager.read(server_name, uri)

            if len(contents) == 1:
                single_content = contents[0]
                if hasattr(single_content, "text"):
                    return single_content.text
                if hasattr(single_content, "blob"):
                    return {
                        "uri": str(single_content.uri),
                        "mime_type": single_content.mimeType,
                        "blob": single_content.blob,
                    }

            # Multiple contents
            result_list = []
            for content in contents:
                if hasattr(content, "text"):
                    result_list.append({"uri": str(content.uri), "text": content.text})
                elif hasattr(content, "blob"):
                    result_list.append(
                        {
                            "uri": str(content.uri),
                            "mime_type": content.mimeType,
                            "blob": content.blob,
                        }
                    )
            return result_list

        except MCPXError as e:
            return json.dumps(e.to_dict(), ensure_ascii=False)
        except Exception as e:
            logger.error(f"Unexpected error in read: {e}")
            return json.dumps({"error": str(e), "code": "UNEXPECTED_ERROR"}, ensure_ascii=False)

    return mcp


def main() -> None:
    """Main entry point for HTTP/SSE transport."""
    from contextlib import asynccontextmanager
    from typing import AsyncGenerator

    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.routing import Mount

    # Setup logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Suppress HTTP client noise
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Parse arguments
    parser = argparse.ArgumentParser(
        prog="mcpx-toolkit",
        description="MCPX - MCP proxy server",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument("config", nargs="?", default=None, help="Path to config.json")
    parser.add_argument("--gui", action="store_true", help="Enable web dashboard")
    parser.add_argument(
        "--open", action="store_true", help="Open browser on startup (implies --gui)"
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Run in desktop window (implies --gui, requires pywebview)",
    )

    args = parser.parse_args()

    # --open and --desktop imply --gui
    if args.open or args.desktop:
        args.gui = True

    # Load config
    if args.config:
        config_path = Path(args.config)
    else:
        config_path = Path(__file__).parent.parent.parent / "config.json"

    # Create config manager
    config_manager = ConfigManager.from_file(config_path)

    # Load config
    import asyncio

    asyncio.run(config_manager.load())

    config = config_manager.config
    logger.info(f"Loaded {len(config.mcpServers)} server(s) from {config_path}")

    # Create and initialize manager with config manager
    manager = ServerManager(config_manager)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        """Initialize manager in uvicorn's event loop."""
        logger.info("Initializing MCP server connections...")
        await manager.initialize()

        # 更新工具描述（注入动态工具/资源列表）
        await update_tool_descriptions(mcp, manager)

        tools = manager.list_all_tools()
        resources = manager.list_all_resources()
        logger.info(f"Connected to {len(manager.list_servers())} server(s)")
        logger.info(f"Cached {len(tools)} tool(s), {len(resources)} resource(s)")

        # Log available tools for debugging
        tools_desc = generate_tools_description(manager)
        logger.debug(f"Tools description:\n{tools_desc}")

        # 存储 mcp 引用到 app.state（供热重载使用）
        app.state.mcp = mcp

        yield

        # Cleanup
        logger.info("Shutting down MCP server connections...")
        await manager.close()

    # Create server (manager will be initialized in lifespan)
    mcp = create_server(config, manager=manager)

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "mcp-protocol-version",
                "mcp-session-id",
                "Authorization",
                "Content-Type",
            ],
            expose_headers=["mcp-session-id"],
        )
    ]

    # Get MCP HTTP app
    mcp_app = mcp.http_app(middleware=middleware)

    # Combined lifespan
    @asynccontextmanager
    async def combined_lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        async with mcp_app.lifespan(app):
            async with lifespan(app):
                yield

    # Create routes based on GUI mode

    # ==================== 架构设计说明 ====================
    #
    # 为什么使用自定义 ASGI app 而不是 Starlette Router/Mount？
    #
    # 1. FastMCP 限制：
    #    - FastMCP 的 http_app() 返回的 Starlette 应用内部硬编码路由
    #    - 路由路径为 Route("/mcp", ...)，无法通过参数配置
    #
    # 2. Starlette Mount 行为：
    #    - Mount("/prefix", app=sub_app) 会拼接路径
    #    - 如果 sub_app 定义 Route("/mcp")，最终路径变成 "/prefix/mcp"
    #    - 这会导致 MCP 端点变成 "/mcp/mcp"，与需求不符
    #
    # 3. 需求冲突：
    #    - MCP 端点必须在 http://host:port/mcp（FastMCP 内部路由）
    #    - Dashboard API 必须在 http://host:port/api/...（REST API）
    #    - 静态文件必须在 http://host:port/...（SPA）
    #
    # 4. 唯一可行方案：
    #    - 自定义 ASGI app 手动检查 path 并分发到不同的子应用
    #    - /api/* → dashboard.api（去掉 /api 前缀）
    #    - /mcp/* → mcp_app（直接传递，保留 /mcp）
    #    - 其他 → dashboard.static（SPA 静态文件）
    #
    # 5. Lifespan 处理：
    #    - 自定义 ASGI app 需要手动处理 lifespan 事件
    #    - combined_lifespan_app 负责 startup/shutdown 事件分发
    #
    # 参考：
    # - FastMCP 源码：fastmcp/server/http.py:create_streamable_http_app
    # - Starlette 文档：https://www.starlette.io/routing/
    #
    # ====================================================

    # Note: mcp_app has internal route at /mcp, so we mount it at root
    # to make the MCP endpoint accessible at http://host:port/mcp
    if args.gui:
        from mcpx.web import create_dashboard_app

        dashboard = create_dashboard_app(manager, config_manager)

        # Build the ASGI app manually for proper routing
        # We can't use Mount because FastMCP's http_app has internal routing at /mcp
        async def gui_app(scope: Any, receive: Any, send: Any) -> None:
            """Composite ASGI app for GUI mode with proper routing."""
            if scope["type"] == "http":
                path = scope.get("path", "")
                if path.startswith("/api"):
                    # Strip /api prefix for the API app
                    scope = dict(scope)  # Copy to avoid modifying original
                    scope["path"] = path[4:] or "/"  # Remove /api prefix
                    await dashboard.api(scope, receive, send)
                    return
                if path.startswith("/mcp"):
                    await mcp_app(scope, receive, send)
                    return
                # Static files and SPA fallback
                await dashboard.static(scope, receive, send)
                return
            elif scope["type"] == "lifespan":
                # Handle lifespan events
                await combined_lifespan_app(scope, receive, send)
                return

        # Create a simple lifespan handler
        async def combined_lifespan_app(scope: Any, receive: Any, send: Any) -> None:
            """Handle lifespan events for all sub-apps.

            自定义 ASGI app 架构说明：
            FastMCP 的 http_app 内部有 /mcp 路由，如果使用 Mount 会导致路径冲突。
            因此需要手动处理 lifespan 事件分发。
            """
            from starlette.datastructures import State

            # Create a mock app for lifespan - type: ignore for compatibility
            class MockApp:
                def __init__(self) -> None:
                    self.state = State()

            mock_app = MockApp()

            try:
                async with combined_lifespan(mock_app):  # type: ignore[arg-type]
                    while True:
                        try:
                            message = await receive()
                            if message["type"] == "lifespan.startup":
                                logger.debug("Lifespan startup initiated")
                                await send({"type": "lifespan.startup.complete"})
                                logger.info("Lifespan startup completed")
                            elif message["type"] == "lifespan.shutdown":
                                logger.debug("Lifespan shutdown initiated")
                                await send({"type": "lifespan.shutdown.complete"})
                                logger.info("Lifespan shutdown completed")
                                return
                            elif message["type"] == "lifespan.failure":
                                logger.error(f"Lifespan failure received: {message}")
                                return
                        except Exception as e:
                            logger.error(f"Error processing lifespan message: {e}", exc_info=True)
                            await send({"type": "lifespan.failure", "message": str(e)})
                            raise
            except Exception as e:
                logger.error(f"Critical error in lifespan context: {e}", exc_info=True)
                await send({"type": "lifespan.failure", "message": str(e)})

        app = gui_app
    else:
        # Non-GUI mode: just serve MCP
        app = Starlette(
            lifespan=combined_lifespan,
            routes=[Mount("/", app=mcp_app)],
        )

    # Find available port
    actual_port = find_available_port(args.port, host=args.host)
    if actual_port != args.port:
        logger.warning(f"Port {args.port} is occupied, using port {actual_port}")

    logger.info(f"Starting HTTP server on {args.host}:{actual_port}")
    logger.info(f"MCP endpoint: http://{args.host}:{actual_port}/mcp/")
    if args.gui:
        logger.info(f"Dashboard: http://{args.host}:{actual_port}/")
    logger.info("")
    logger.info("Thanks for using mcpx-toolkit!")
    logger.info("https://github.com/quan2005/mcpx")
    logger.info("")

    # Handle different startup modes
    if args.desktop:
        # Desktop mode: run in pywebview
        _run_desktop_mode(app, args.host, actual_port, manager)
    elif args.open:
        # Browser mode: open browser and run server
        _run_browser_mode(app, args.host, actual_port, manager)
    else:
        # Normal mode: just run server
        uvicorn.run(app, host=args.host, port=actual_port)


def _wait_for_initialization(manager: ServerManager, timeout: float = 60.0) -> bool:
    """Wait for manager initialization to complete.

    Args:
        manager: ServerManager instance
        timeout: Maximum time to wait in seconds

    Returns:
        True if initialized, False if timeout
    """
    import time

    start_time = time.time()
    last_log_time = start_time
    while time.time() - start_time < timeout:
        if manager._initialized:
            return True
        # Log progress every 5 seconds
        if time.time() - last_log_time > 5:
            elapsed = time.time() - start_time
            logger.info(f"Still initializing... ({elapsed:.0f}s elapsed)")
            last_log_time = time.time()
        time.sleep(0.1)

    # Log connected servers for debugging
    connected = manager.list_servers()
    total = len(manager._config.mcpServers)
    logger.warning(f"Initialization timeout: {len(connected)}/{total} servers connected")

    return False


def _run_browser_mode(app: Any, host: str, port: int, manager: ServerManager) -> None:
    """Run server and open browser after initialization."""
    import threading
    import time
    import webbrowser

    import uvicorn

    # Start server in background thread
    def run_server() -> None:
        uvicorn.run(app, host=host, port=port, log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for manager initialization to complete
    logger.info("Waiting for server initialization...")
    if not _wait_for_initialization(manager):
        logger.error("Server initialization timeout")
        return

    # Additional small delay to ensure HTTP server is ready
    time.sleep(0.5)

    # Open browser
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"
    logger.info(f"Opening browser: {url}")
    webbrowser.open(url)

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def _run_desktop_mode(app: Any, host: str, port: int, manager: ServerManager) -> None:
    """Run server in desktop window using pywebview."""
    import threading
    import time

    import uvicorn

    try:
        import webview
    except ImportError:
        logger.error("pywebview not installed. Install with: uv pip install pywebview")
        sys.exit(1)

    # Start server in background thread
    def run_server() -> None:
        uvicorn.run(app, host=host, port=port, log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for manager initialization to complete
    logger.info("Waiting for server initialization...")
    if not _wait_for_initialization(manager):
        logger.error("Server initialization timeout")
        return

    # Additional small delay to ensure HTTP server is ready
    time.sleep(0.5)

    # Create desktop window
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"
    logger.info(f"Opening desktop window: {url}")

    webview.create_window("MCPX Dashboard", url, width=1400, height=900)
    webview.start()


if __name__ == "__main__":
    main()
