"""Tests for MCPX."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from mcpx.__main__ import McpServerConfig, ProxyConfig, create_server, load_config


def _parse_response(content: str) -> Any:
    """Parse response, trying JSON first then TOON as fallback."""
    # Try JSON first (for error messages and uncompressed responses)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try TOON format (for compressed responses)
    try:
        import toons

        return toons.loads(content)
    except Exception:
        pass

    # Return as-is if both fail
    return content


def test_load_config_from_file():
    """Test loading configuration from a file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "mcpServers": {
                    "test": {"type": "stdio", "command": "echo", "args": ["hello"]},
                    "test2": {"type": "stdio", "command": "cat", "args": []},
                }
            },
            f,
        )
        config_path = Path(f.name)

    try:
        config = load_config(config_path)
        assert len(config.mcpServers) == 2
        assert "test" in config.mcpServers
        assert config.mcpServers["test"].command == "echo"
        assert config.mcpServers["test"].args == ["hello"]
    finally:
        config_path.unlink()


def test_load_config_file_not_found():
    """Test loading from non-existent file."""
    with pytest.raises(SystemExit):
        load_config(Path("/nonexistent/config.json"))


def test_load_config_invalid_json():
    """Test loading with invalid JSON."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{invalid json}")
        config_path = Path(f.name)

    try:
        with pytest.raises(SystemExit):
            load_config(config_path)
    finally:
        config_path.unlink()


def test_load_config_invalid_structure():
    """Test loading with invalid structure (mcpServers not a dict)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"mcpServers": "not-a-dict"}, f)
        config_path = Path(f.name)

    try:
        with pytest.raises(SystemExit):
            load_config(config_path)
    finally:
        config_path.unlink()


def test_proxy_config_validation():
    """Test ProxyConfig validation."""
    data = {
        "mcpServers": {
            "test": {"type": "stdio", "command": "echo", "args": ["hello"]},
        }
    }
    config = ProxyConfig(**data)
    assert len(config.mcpServers) == 1
    assert "test" in config.mcpServers


def test_proxy_config_empty_servers():
    """Test ProxyConfig with no servers."""
    config = ProxyConfig()
    assert config.mcpServers == {}


def test_mcp_server_config_validation():
    """Test McpServerConfig validation."""
    # Valid config (name is no longer a field, it's the key in mcpServers)
    config = McpServerConfig(type="stdio", command="echo", args=["hello"])
    assert config.type == "stdio"
    assert config.args == ["hello"]

    # Missing required field for stdio type
    config = McpServerConfig(type="stdio")
    with pytest.raises(ValueError, match="stdio type requires 'command' field"):
        config.validate_for_server("test")


def test_mcp_server_config_with_env():
    """Test McpServerConfig with environment variables."""
    config = McpServerConfig(
        type="stdio",
        command="node",
        args=["server.js"],
        env={"API_KEY": "secret", "DEBUG": "true"},
    )
    assert config.type == "stdio"
    assert config.env == {"API_KEY": "secret", "DEBUG": "true"}


def test_create_server():
    """Test creating FastMCP server."""
    config = ProxyConfig(
        mcpServers={
            "test": McpServerConfig(type="stdio", command="echo", args=["hello"]),
        }
    )
    mcp = create_server(config)

    assert mcp is not None
    assert hasattr(mcp, "_config")
    assert hasattr(mcp, "_registry")
    assert hasattr(mcp, "_executor")


def test_create_server_multiple_servers():
    """Test creating server with multiple MCP servers."""
    config = ProxyConfig(
        mcpServers={
            "s1": McpServerConfig(type="stdio", command="cmd1", args=[]),
            "s2": McpServerConfig(type="stdio", command="cmd2", args=[]),
            "s3": McpServerConfig(type="stdio", command="cmd3", args=[]),
        }
    )
    mcp = create_server(config)

    assert mcp is not None
    assert len(mcp._config.mcpServers) == 3


def _extract_text_content(result) -> str:
    """Extract text content from CallToolResult."""
    # FastMCP returns content in result.content, not result.data
    if hasattr(result, "content"):
        content_list = result.content
        if content_list and len(content_list) > 0:
            first_item = content_list[0]
            if hasattr(first_item, "text"):
                return first_item.text
    if hasattr(result, "data") and result.data is not None:
        return result.data
    return str(result)


async def test_call_validation_returns_tool_schema():
    """Test: call returns tool schema on argument validation error."""
    from mcpx.server import ServerManager, ToolInfo

    config = ProxyConfig()
    manager = ServerManager(config)
    manager._initialized = True

    # Add a dummy pool
    class DummyPool:
        pass

    manager._pools["dummy"] = DummyPool()

    tool_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "mode": {"type": "string", "enum": ["fast", "safe"]},
        },
        "required": ["path"],
    }
    manager._tools["dummy:read_file"] = ToolInfo(
        server_name="dummy",
        name="read_file",
        description="Read file content",
        input_schema=tool_schema,
    )

    mcp_server = create_server(config, manager=manager)

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "invoke",
            arguments={
                "method": "dummy.read_file",
                "arguments": {"mode": "fast"},
            },
        )

    content = _extract_text_content(result)
    call_result = _parse_response(content)

    # New simplified format: no "success" key
    assert "error" in call_result
    assert "Argument validation failed" in call_result["error"]
    # tool_schema is now compressed to TypeScript format (default enabled)
    assert "tool_schema" in call_result
    # TypeScript format: {path: string; mode?: "fast" | "safe"}
    assert "path: string" in call_result["tool_schema"]
    assert "mode?" in call_result["tool_schema"]  # optional field


@pytest.mark.asyncio
async def test_update_tool_descriptions():
    """测试工具描述动态更新。"""
    from fastmcp import FastMCP

    from mcpx.description import (
        update_tool_descriptions,
    )
    from mcpx.server import ServerManager, ToolInfo

    # 创建模拟 manager
    config = ProxyConfig()
    manager = ServerManager(config)
    manager._initialized = True

    # 添加模拟工具
    class DummyPool:
        pass

    manager._pools["test"] = DummyPool()
    manager._tools["test:tool1"] = ToolInfo(
        server_name="test",
        name="tool1",
        description="Test tool description",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )

    # 创建 FastMCP 并添加工具
    mcp = FastMCP("test")

    @mcp.tool()
    async def invoke(method: str, arguments: dict | None = None) -> str:
        """Invoke tool placeholder."""
        return "ok"

    @mcp.tool()
    async def read(server_name: str, uri: str) -> str:
        """Read resource placeholder."""
        return "ok"

    # 更新描述
    await update_tool_descriptions(mcp, manager)

    # 验证 invoke 工具描述已更新
    invoke_tool = await mcp.get_tool("invoke")
    assert "test.tool1" in invoke_tool.description
    assert "Available tools:" in invoke_tool.description

    # 验证描述格式符合模板
    assert "Invoke an MCP tool" in invoke_tool.description


@pytest.mark.asyncio
async def test_update_tool_descriptions_empty_manager():
    """测试空 manager 时工具描述更新（应显示 "No tools available"）。"""
    from fastmcp import FastMCP

    from mcpx.description import update_tool_descriptions
    from mcpx.server import ServerManager

    # 创建空 manager
    config = ProxyConfig()
    manager = ServerManager(config)
    manager._initialized = True

    # 创建 FastMCP 并添加工具
    mcp = FastMCP("test")

    @mcp.tool()
    async def invoke(method: str, arguments: dict | None = None) -> str:
        """Invoke tool placeholder."""
        return "ok"

    @mcp.tool()
    async def read(server_name: str, uri: str) -> str:
        """Read resource placeholder."""
        return "ok"

    # 更新描述
    await update_tool_descriptions(mcp, manager)

    # 验证 invoke 工具描述已更新
    invoke_tool = await mcp.get_tool("invoke")
    # 空服务器列表时，只有标题没有工具
    assert "Available tools:" in invoke_tool.description

    # 验证 read 工具描述
    read_tool = await mcp.get_tool("read")
    assert "No resources available" in read_tool.description


@pytest.mark.asyncio
async def test_description_templates():
    """测试描述模板常量格式正确。"""
    from mcpx.description import (
        INVOKE_DESCRIPTION_TEMPLATE,
        READ_DESCRIPTION_TEMPLATE,
    )

    # 验证模板包含占位符
    assert "{tools_description}" in INVOKE_DESCRIPTION_TEMPLATE
    assert "{resources_description}" in READ_DESCRIPTION_TEMPLATE

    # 验证模板包含关键说明
    assert "server.tool" in INVOKE_DESCRIPTION_TEMPLATE
    assert "Error Handling" in INVOKE_DESCRIPTION_TEMPLATE
    assert "server_name" in READ_DESCRIPTION_TEMPLATE
    assert "uri" in READ_DESCRIPTION_TEMPLATE


@pytest.mark.asyncio
async def test_gui_app_path_routing():
    """测试自定义 ASGI app 的路径路由逻辑。"""
    from starlette.testclient import TestClient

    from mcpx.config import ProxyConfig
    from mcpx.config_manager import ConfigManager
    from mcpx.server import ServerManager
    from mcpx.web import SpaStaticFiles, create_dashboard_app

    # 创建模拟 manager 和 config
    config = ProxyConfig()
    manager = ServerManager(config)
    manager._initialized = True
    config_manager = ConfigManager(config)

    # 创建 dashboard app
    dashboard = create_dashboard_app(manager, config_manager)

    # 测试静态文件处理器的 SKIP_PREFIXES
    assert SpaStaticFiles.SKIP_PREFIXES == ("/mcp", "/api")

    # 测试 API 路由
    api_client = TestClient(dashboard.api)
    response = api_client.get("/servers")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_static_files_skip_prefixes():
    """测试静态文件处理器跳过 /mcp 和 /api 路径。"""
    # 创建临时目录
    import tempfile
    from pathlib import Path

    from mcpx.web import SpaStaticFiles

    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = Path(tmpdir)

        # 创建 index.html
        (static_dir / "index.html").write_text("<html>Test</html>")

        # 创建静态文件处理器
        static_handler = SpaStaticFiles(static_dir)

        # 测试 /mcp 路径被跳过
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp",
            "query_string": b"",
            "headers": [],
        }

        received = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            received.append(message)

        # 调用静态处理器
        await static_handler(scope, receive, send)

        # 应该没有发送任何响应（跳过了）
        assert len(received) == 0

        # 测试 /api/test 路径被跳过
        scope["path"] = "/api/test"
        received.clear()
        await static_handler(scope, receive, send)
        assert len(received) == 0

        # 测试正常静态文件路径不被跳过
        scope["path"] = "/index.html"
        received.clear()
        await static_handler(scope, receive, send)
        # 应该返回文件内容
        assert len(received) > 0
        assert received[0]["type"] == "http.response.start"
