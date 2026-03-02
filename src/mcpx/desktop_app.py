"""MCPX Desktop App 入口 - 用于 PyInstaller 打包。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# 设置 logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def get_resource_path(relative_path: str) -> Path:
    """获取资源文件路径，兼容开发环境和打包后环境。"""
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 打包后的路径
        return Path(sys._MEIPASS) / relative_path
    # 开发环境路径
    return Path(__file__).parent / relative_path


def main() -> None:
    """Desktop App 入口。"""
    import json
    from contextlib import asynccontextmanager
    from typing import AsyncGenerator

    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.routing import Mount

    from mcpx.__main__ import create_server
    from mcpx.config_manager import ConfigManager
    from mcpx.port_utils import find_available_port
    from mcpx.server import ServerManager
    from mcpx.web import create_dashboard_app

    # 查找配置文件
    config_paths = [
        Path.cwd() / "config.json",
        Path.home() / ".mcpx" / "config.json",
        Path(__file__).parent.parent.parent / "config.json",
    ]

    config_path = None
    for p in config_paths:
        if p.exists():
            config_path = p
            break

    if config_path is None:
        # 创建默认配置
        default_config_dir = Path.home() / ".mcpx"
        default_config_dir.mkdir(exist_ok=True)
        config_path = default_config_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump({"mcpServers": {}}, f, indent=2)
        logger.info(f"Created default config at {config_path}")

    # 加载配置
    import asyncio

    config_manager = ConfigManager.from_file(config_path)
    asyncio.run(config_manager.load())
    config = config_manager.config
    logger.info(f"Loaded {len(config.mcpServers)} server(s) from {config_path}")

    # 创建 ServerManager
    manager = ServerManager(config_manager)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        logger.info("Initializing MCP server connections...")
        await manager.initialize()
        tools = manager.list_all_tools()
        resources = manager.list_all_resources()
        logger.info(f"Connected to {len(manager.list_servers())} server(s)")
        logger.info(f"Cached {len(tools)} tool(s), {len(resources)} resource(s)")
        yield
        logger.info("Shutting down MCP server connections...")
        await manager.close()

    # 创建 MCP 服务器
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

    mcp_app = mcp.http_app(middleware=middleware)

    @asynccontextmanager
    async def combined_lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        async with mcp_app.lifespan(app):
            async with lifespan(app):
                yield

    # 创建 Dashboard
    static_dir = get_resource_path("web/static")
    dashboard = create_dashboard_app(manager, config_manager, static_dir=static_dir)

    routes = [
        Mount("/api", app=dashboard.api),
        Mount("/mcp", app=mcp_app),
        Mount("/", app=dashboard.static),
    ]

    app = Starlette(lifespan=combined_lifespan, routes=routes)

    # 查找可用端口
    port = find_available_port(8000, host="127.0.0.1")
    host = "127.0.0.1"

    logger.info(f"Starting MCPX Desktop on http://{host}:{port}")

    # 启动服务器和桌面窗口
    import threading
    import time

    def run_server() -> None:
        uvicorn.run(app, host=host, port=port, log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 等待初始化
    logger.info("Waiting for server initialization...")
    start_time = time.time()
    while time.time() - start_time < 60:
        if manager._initialized:
            break
        time.sleep(0.1)

    time.sleep(0.5)

    # 启动桌面窗口
    import webview

    url = f"http://{host}:{port}/"
    logger.info(f"Opening desktop window: {url}")

    webview.create_window("MCPX Dashboard", url, width=1400, height=900)
    webview.start()


if __name__ == "__main__":
    main()
