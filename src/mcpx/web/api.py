"""REST API 路由 - 提供 Dashboard 所需的端点。"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcpx.config_manager import ConfigManager
from mcpx.description import (
    generate_resources_description,
    generate_tools_description,
    update_tool_descriptions,
)
from mcpx.server import ServerManager

logger = logging.getLogger(__name__)

__all__ = ["create_api_routes"]


class APIHandler:
    """API 处理器。"""

    def __init__(self, manager: ServerManager, config_manager: ConfigManager) -> None:
        """初始化 API 处理器。

        Args:
            manager: 服务器管理器
            config_manager: 配置管理器
        """
        self._manager = manager
        self._config_manager = config_manager

    async def _update_tool_descriptions(self, request: Request) -> None:
        """更新工具描述的辅助方法。

        在服务器启停或配置更新后调用，确保 invoke/read 工具描述同步更新。

        Args:
            request: Starlette 请求对象，用于获取 app.state.mcp
        """
        if hasattr(request.app.state, "mcp"):
            await update_tool_descriptions(request.app.state.mcp, self._manager)

    # 服务器相关

    async def list_servers(self, request: Request) -> JSONResponse:
        """GET /servers - 列出所有服务器。"""
        servers = []
        for name, config in self._config_manager.config.mcpServers.items():
            info = self._manager.get_server_info(name)
            health = self._manager.get_server_health(name)

            server_data: dict[str, Any] = {
                "name": name,
                "enabled": config.enabled,
                "type": config.type,
                "connected": name in self._manager.list_servers(),
            }

            if info:
                server_data.update(
                    {
                        "server_name": info.server_name,
                        "version": info.version,
                        "instructions": info.instructions,
                    }
                )

            if health:
                server_data["health"] = {
                    "status": health["status"],
                    "last_check": health["last_check"],
                    "consecutive_failures": health["consecutive_failures"],
                }

            # 添加工具和资源计数
            tools = self._manager.list_tools(name)
            resources = self._manager.list_resources(name)
            server_data["tools_count"] = len(tools)
            server_data["resources_count"] = len(resources)

            servers.append(server_data)

        return JSONResponse({"servers": servers})

    async def get_server(self, request: Request) -> JSONResponse:
        """GET /servers/{name} - 服务器详情。"""
        name = request.path_params["name"]
        config = self._config_manager.get_server(name)

        if config is None:
            return JSONResponse({"error": f"Server '{name}' not found"}, status_code=404)

        info = self._manager.get_server_info(name)
        health = self._manager.get_server_health(name)

        server_data: dict[str, Any] = {
            "name": name,
            "enabled": config.enabled,
            "type": config.type,
            "connected": name in self._manager.list_servers(),
            "config": {
                "command": config.command,
                "args": config.args,
                "env": config.env,
                "url": config.url,
                "headers": config.headers,
            },
        }

        if info:
            server_data.update(
                {
                    "server_name": info.server_name,
                    "version": info.version,
                    "instructions": info.instructions,
                }
            )

        if health:
            server_data["health"] = {
                "status": health["status"],
                "last_check": health["last_check"],
                "last_success": health["last_success"],
                "consecutive_failures": health["consecutive_failures"],
                "last_error": health["last_error"],
            }

        # 添加工具和资源计数
        tools = self._manager.list_tools(name)
        resources = self._manager.list_resources(name)
        server_data["tools_count"] = len(tools)
        server_data["resources_count"] = len(resources)

        return JSONResponse(server_data)

    async def toggle_server(self, request: Request) -> JSONResponse:
        """POST /servers/{name}/toggle - 启停服务器。"""
        name = request.path_params["name"]
        config = self._config_manager.get_server(name)

        if config is None:
            return JSONResponse({"error": f"Server '{name}' not found"}, status_code=404)

        # 切换状态
        new_enabled = not config.enabled
        self._config_manager.set_server_enabled(name, new_enabled)

        # 保存配置
        await self._config_manager.save()

        # 如果启用，尝试连接
        if new_enabled:
            success = await self._manager.connect_server(name)
            await self._update_tool_descriptions(request)
            return JSONResponse(
                {
                    "name": name,
                    "enabled": new_enabled,
                    "connected": success,
                }
            )
        else:
            # 如果禁用，断开连接
            await self._manager.disconnect_server(name)
            await self._update_tool_descriptions(request)
            return JSONResponse(
                {
                    "name": name,
                    "enabled": new_enabled,
                    "connected": False,
                }
            )

    # 工具相关

    async def list_tools(self, request: Request) -> JSONResponse:
        """GET /tools - 列出所有工具。"""
        server_filter = request.query_params.get("server")

        tools = []
        all_tools = self._manager.list_all_tools()

        for tool in all_tools:
            # 如果指定了服务器过滤
            if server_filter and tool.server_name != server_filter:
                continue

            # 检查工具是否被禁用
            tool_key = f"{tool.server_name}.{tool.name}"
            is_enabled = not self._config_manager.is_tool_disabled(tool_key)

            tools.append(
                {
                    "server": tool.server_name,
                    "name": tool.name,
                    "description": tool.description,
                    "enabled": is_enabled,
                }
            )

        return JSONResponse({"tools": tools})

    async def get_tool(self, request: Request) -> JSONResponse:
        """GET /tools/{server}/{tool} - 工具详情。"""
        server_name = request.path_params["server"]
        tool_name = request.path_params["tool"]

        tool = self._manager.get_tool(server_name, tool_name)
        if tool is None:
            return JSONResponse(
                {"error": f"Tool '{tool_name}' not found in server '{server_name}'"},
                status_code=404,
            )

        tool_key = f"{server_name}.{tool_name}"
        is_enabled = not self._config_manager.is_tool_disabled(tool_key)

        return JSONResponse(
            {
                "server": tool.server_name,
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "enabled": is_enabled,
            }
        )

    async def toggle_tool(self, request: Request) -> JSONResponse:
        """POST /tools/{server}/{tool}/toggle - 启停工具。"""
        server_name = request.path_params["server"]
        tool_name = request.path_params["tool"]

        tool = self._manager.get_tool(server_name, tool_name)
        if tool is None:
            return JSONResponse(
                {"error": f"Tool '{tool_name}' not found in server '{server_name}'"},
                status_code=404,
            )

        tool_key = f"{server_name}.{tool_name}"
        is_disabled = self._config_manager.is_tool_disabled(tool_key)

        # 切换状态
        if is_disabled:
            self._config_manager.enable_tool(tool_key)
        else:
            self._config_manager.disable_tool(tool_key)

        # 保存配置
        await self._config_manager.save()

        return JSONResponse(
            {
                "server": server_name,
                "name": tool_name,
                "enabled": is_disabled,  # 切换后的状态
            }
        )

    async def invoke_tool(self, request: Request) -> JSONResponse:
        """POST /invoke - 执行工具。"""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        method = body.get("method")
        arguments = body.get("arguments", {})

        if not method:
            return JSONResponse({"error": "Missing 'method' field"}, status_code=400)

        # 解析 method
        parts = method.split(".", 1)
        if len(parts) != 2:
            return JSONResponse(
                {"error": f"Invalid method format: '{method}'. Expected 'server.tool'"},
                status_code=400,
            )

        server_name, tool_name = parts

        # 检查工具是否被禁用
        tool_key = f"{server_name}.{tool_name}"
        if self._config_manager.is_tool_disabled(tool_key):
            return JSONResponse({"error": f"Tool '{tool_name}' is disabled"}, status_code=403)

        try:
            result = await self._manager.call(server_name, tool_name, arguments)

            if not result.success:
                return JSONResponse({"error": result.error}, status_code=500)

            return JSONResponse(
                {
                    "success": True,
                    "data": result.raw_data,
                    "compressed": result.compressed,
                }
            )

        except Exception as e:
            logger.error(f"Error invoking tool: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # 资源相关

    async def list_resources(self, request: Request) -> JSONResponse:
        """GET /resources - 列出所有资源。"""
        server_filter = request.query_params.get("server")

        resources = []
        all_resources = self._manager.list_all_resources()

        for resource in all_resources:
            # 如果指定了服务器过滤
            if server_filter and resource.server_name != server_filter:
                continue

            resources.append(
                {
                    "server": resource.server_name,
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mime_type": resource.mime_type,
                    "size": resource.size,
                }
            )

        return JSONResponse({"resources": resources})

    async def read_resource(self, request: Request) -> JSONResponse:
        """POST /read - 读取资源。"""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        server_name = body.get("server")
        uri = body.get("uri")

        if not server_name or not uri:
            return JSONResponse({"error": "Missing 'server' or 'uri' field"}, status_code=400)

        try:
            contents = await self._manager.read(server_name, uri)

            # 转换内容为可序列化的格式
            result = []
            for content in contents:
                item: dict[str, Any] = {"uri": str(content.uri)}
                if hasattr(content, "text"):
                    item["type"] = "text"
                    item["text"] = content.text
                elif hasattr(content, "blob"):
                    item["type"] = "binary"
                    item["mime_type"] = content.mimeType
                    item["blob"] = content.blob
                result.append(item)

            return JSONResponse(
                {
                    "success": True,
                    "contents": result,
                }
            )

        except Exception as e:
            logger.error(f"Error reading resource: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # 健康检查相关

    async def get_health(self, request: Request) -> JSONResponse:
        """GET /health - 健康总览。"""
        status = self._manager.get_health_status()
        return JSONResponse(status.to_dict())

    async def get_server_health(self, request: Request) -> JSONResponse:
        """GET /health/{server} - 服务器健康状态。"""
        server_name = request.path_params["server"]
        health = self._manager.get_server_health(server_name)

        if health is None:
            return JSONResponse({"error": f"Server '{server_name}' not found"}, status_code=404)

        return JSONResponse(health)

    async def check_server_health(self, request: Request) -> JSONResponse:
        """POST /health/{server}/check - 手动触发健康检查。"""
        server_name = request.path_params["server"]

        # 检查服务器是否存在
        if server_name not in self._config_manager.config.mcpServers:
            return JSONResponse({"error": f"Server '{server_name}' not found"}, status_code=404)

        try:
            is_healthy = await self._manager.check_server_health(server_name)
            health = self._manager.get_server_health(server_name)

            return JSONResponse(
                {
                    "server": server_name,
                    "healthy": is_healthy,
                    "status": health["status"] if health else "unknown",
                }
            )
        except Exception as e:
            logger.error(f"Error checking health: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # 配置相关

    async def get_config(self, request: Request) -> JSONResponse:
        """GET /config - 获取配置。"""
        return JSONResponse(self._config_manager.to_dict())

    async def update_config(self, request: Request) -> JSONResponse:
        """PUT /config - 保存配置并热重载。"""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        try:
            # 更新配置
            self._config_manager.update_config(body)

            # 保存到文件
            await self._config_manager.save()

            # 热重载
            await self._manager.reload()

            # 更新工具描述
            await self._update_tool_descriptions(request)

            return JSONResponse(
                {
                    "success": True,
                    "message": "Config saved and reloaded",
                }
            )

        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # MCPX 工具

    async def get_mcpx_tools(self, request: Request) -> JSONResponse:
        """GET /mcpx-tools - 获取 MCPX 工具的真实描述信息。"""
        # 生成动态的工具描述
        tools_desc = generate_tools_description(self._manager)
        resources_desc = generate_resources_description(self._manager)

        # invoke 和 read 工具的 schema 定义
        invoke_schema = {
            "name": "invoke",
            "description": 'Invoke an MCP tool.\n\nArgs:\n    method: Method identifier in "server.tool" format\n    arguments: Tool arguments\n\nExample:\n    invoke(method="filesystem.read_file", arguments={"path": "/tmp/file.txt"})\n\nError Handling:\n    When invoke fails, it returns helpful information:\n    - Server not found: returns error + available_servers list\n    - Tool not found: returns error + available_tools list\n    - Invalid arguments: returns error + tool_schema',
            "input_schema": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": 'Method identifier in "server.tool" format',
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Tool-specific arguments",
                        "additionalProperties": True,
                    },
                },
                "required": ["method"],
            },
            "dynamic_description": tools_desc,  # 动态生成的工具列表
        }

        read_schema = {
            "name": "read",
            "description": 'Read a resource from MCP servers.\n\nArgs:\n    server_name: Server name (required)\n    uri: Resource URI (required)\n\nReturns:\n    - Text resource: string content\n    - Binary resource: dict with uri, mime_type, and blob (base64)\n    - Multiple contents: list of content items\n\nExamples:\n    read(server_name="filesystem", uri="file:///tmp/file.txt")',
            "input_schema": {
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Server name (required)",
                    },
                    "uri": {
                        "type": "string",
                        "description": "Resource URI (required)",
                    },
                },
                "required": ["server_name", "uri"],
            },
            "dynamic_description": resources_desc,  # 动态生成的资源列表
        }

        return JSONResponse(
            {
                "tools": [invoke_schema, read_schema],
                "tools_description": tools_desc,
                "resources_description": resources_desc,
            }
        )


def create_api_routes(manager: ServerManager, config_manager: ConfigManager) -> list[Route]:
    """创建 API 路由列表。

    Args:
        manager: 服务器管理器
        config_manager: 配置管理器

    Returns:
        路由列表
    """
    handler = APIHandler(manager, config_manager)

    return [
        # 服务器
        Route("/servers", handler.list_servers, methods=["GET"]),
        Route("/servers/{name}", handler.get_server, methods=["GET"]),
        Route("/servers/{name}/toggle", handler.toggle_server, methods=["POST"]),
        # 工具
        Route("/tools", handler.list_tools, methods=["GET"]),
        Route("/tools/{server}/{tool}", handler.get_tool, methods=["GET"]),
        Route("/tools/{server}/{tool}/toggle", handler.toggle_tool, methods=["POST"]),
        Route("/invoke", handler.invoke_tool, methods=["POST"]),
        # 资源
        Route("/resources", handler.list_resources, methods=["GET"]),
        Route("/read", handler.read_resource, methods=["POST"]),
        # 健康检查
        Route("/health", handler.get_health, methods=["GET"]),
        Route("/health/{server}", handler.get_server_health, methods=["GET"]),
        Route("/health/{server}/check", handler.check_server_health, methods=["POST"]),
        # 配置
        Route("/config", handler.get_config, methods=["GET"]),
        Route("/config", handler.update_config, methods=["PUT"]),
        # MCPX 工具
        Route("/mcpx-tools", handler.get_mcpx_tools, methods=["GET"]),
    ]
