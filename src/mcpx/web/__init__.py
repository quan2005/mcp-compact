"""Web Dashboard - SPA 静态文件服务和应用创建。"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from mcpx.config_manager import ConfigManager
from mcpx.server import ServerManager
from mcpx.web.api import create_api_routes

logger = logging.getLogger(__name__)

__all__ = ["SpaStaticFiles", "create_dashboard_app", "DashboardApp"]


class SpaStaticFiles:
    """SPA 静态文件处理器，支持前端路由回退。"""

    # 不处理的路径前缀（交给其他路由处理）
    SKIP_PREFIXES = ("/mcp", "/api")

    def __init__(self, directory: Path) -> None:
        """初始化静态文件处理器。

        Args:
            directory: 静态文件目录
        """
        self._directory = directory
        self._index_file = directory / "index.html"

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """处理请求。

        静态文件存在则返回，否则回退到 index.html（SPA 路由）。
        注意：/mcp 和 /api 路径不处理，交给其他路由。
        """
        if scope["type"] != "http":
            return

        request = Request(scope, receive)
        path = request.url.path

        # 跳过 API 和 MCP 路径
        if path.startswith(self.SKIP_PREFIXES):
            return

        path = path.lstrip("/")

        # 安全路径检查
        if ".." in path:
            response = Response("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        # 尝试返回静态文件
        if path:
            file_path = self._directory / path
            if file_path.exists() and file_path.is_file():
                mime_type, _ = mimetypes.guess_type(str(file_path))
                response = FileResponse(
                    file_path, media_type=mime_type or "application/octet-stream"
                )
                await response(scope, receive, send)
                return

        # 回退到 index.html
        if self._index_file.exists():
            response = FileResponse(self._index_file, media_type="text/html")
            await response(scope, receive, send)
        else:
            response = Response(
                "Dashboard not built. Run 'cd gui && npm run build' first.",
                status_code=404,
                media_type="text/plain",
            )
            await response(scope, receive, send)


class DashboardApp:
    """Dashboard 应用，包含 API 和静态文件服务。"""

    def __init__(
        self, manager: ServerManager, config_manager: ConfigManager, static_dir: Path | None = None
    ) -> None:
        """初始化 Dashboard 应用。

        Args:
            manager: 服务器管理器
            config_manager: 配置管理器
            static_dir: 静态文件目录，None 则使用默认路径
        """
        self._manager = manager
        self._config_manager = config_manager

        # 确定静态文件目录
        if static_dir is None:
            # 默认路径：web/static/
            self._static_dir = Path(__file__).parent / "static"
        else:
            self._static_dir = static_dir

        # 创建 API 路由
        api_routes = create_api_routes(manager, config_manager)

        # 创建 API 应用
        self._api = Starlette(routes=api_routes)

        # 创建静态文件处理器
        self._static = SpaStaticFiles(self._static_dir)

    @property
    def api(self) -> Starlette:
        """获取 API 应用。"""
        return self._api

    @property
    def static(self) -> SpaStaticFiles:
        """获取静态文件处理器。"""
        return self._static

    async def health_check(self, request: Request) -> JSONResponse:
        """Dashboard 健康检查。"""
        return JSONResponse(
            {
                "status": "ok",
                "dashboard": True,
                "static_dir": str(self._static_dir),
                "static_exists": self._static_dir.exists(),
            }
        )


def create_dashboard_app(
    manager: ServerManager, config_manager: ConfigManager, static_dir: Path | None = None
) -> DashboardApp:
    """创建 Dashboard 应用。

    Args:
        manager: 服务器管理器
        config_manager: 配置管理器
        static_dir: 静态文件目录

    Returns:
        Dashboard 应用实例
    """
    return DashboardApp(manager, config_manager, static_dir)
