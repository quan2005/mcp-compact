# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 核心目标

MCP 代理服务器，将多个 MCP 服务器聚合为 `invoke` 和 `read` 两个工具，AI 按需执行工具和读取资源，减少初始上下文。

## 常用命令

```bash
# 运行测试
uv run pytest tests/ -v --cov=src/mcpx

# 运行单个测试
uv run pytest tests/test_mcpx.py -v

# 代码检查
uv run ruff check src/mcpx tests/

# 类型检查
uv run mypy src/mcpx

# HTTP/SSE 模式运行（默认端口 8000）
uv run mcpx-toolkit config.json

# 指定端口和主机
uv run mcpx-toolkit --port 3000 --host 127.0.0.1 config.json

# Dashboard 模式（启用 Web 界面）
uv run mcpx-toolkit --gui --open config.json

# Desktop 模式（原生窗口）
uv run mcpx-toolkit --gui --desktop config.json
```

## 开发流程

本项目使用 **skills** 规范 AI 开发流程（位于 `.claude/skills/`）。

### 开始开发前的第一步

**告诉 AI：加载 mcpx-getting-started skill**

这会加载项目的所有开发流程规范。

### 完整开发流程

```
1. TDD 开发 (mcpx-tdd-workflow)
   ├─ RED:   先写失败的测试
   ├─ GREEN: 写最小代码使测试通过
   └─ REFACTOR: 优化代码结构

2. 代码检查 (mcpx-code-quality)
   ├─ uv run ruff format src/mcpx tests/
   ├─ uv run ruff check src/mcpx tests/
   ├─ uv run mypy src/mcpx
   └─ uv run pytest tests/ -v --cov=src/mcpx (覆盖率 ≥ 70%)

3. 文档更新 (mcpx-documentation)
   ├─ CLAUDE.md (AI 开发指南)
   ├─ README.md (用户文档)
   └─ docs/roadmap.md (功能状态)

4. 提交代码
   └─ git commit -m "<type>: <中文描述>"
```

### Skills 快速参考

| Skill | 用途 | 触发时机 |
|-------|------|----------|
| `mcpx-getting-started` | 入口，加载所有 skills | 开始任何工作前 |
| `mcpx-tdd-workflow` | TDD 开发（RED-GREEN-REFACTOR） | 实现功能或 bug 修复 |
| `mcpx-code-quality` | 代码质量检查（lint/types/test） | 提交代码前 |
| `mcpx-documentation` | 文档更新规范 | 代码变更影响功能时 |
| `mcpx-release` | 版本发布流程 | 准备发布版本时 |

### 提交规范

```
<type>: <中文描述>

类型：feat, fix, docs, style, refactor, perf, test, chore
```

## 技术栈

- **FastMCP 3.0** - MCP 服务器框架（`@mcp.tool` 装饰器注册工具）
- **Pydantic v2** - 数据模型和校验
- **uv** - 依赖管理和运行
- **toons** - TOON 压缩格式

## 项目结构

```
src/mcpx/
├── __init__.py          # 导出公共 API
├── __main__.py          # 入口、invoke/read 工具定义（大幅简化）
├── server.py            # ServerManager 核心类（合并 Registry + Executor）
├── pool.py              # ConnectionPool 连接池实现
├── config.py            # ProxyConfig、McpServerConfig 配置模型
├── config_manager.py    # ConfigManager：配置管理器（支持增量启停）
├── description.py       # 工具/资源描述生成
├── errors.py            # 统一错误类型
├── compression.py       # ToonCompressor：TOON 压缩实现
├── schema_ts.py         # json_schema_to_typescript：Schema 压缩
├── content.py           # 多模态内容处理（TextContent/ImageContent/EmbeddedResource）
├── health.py            # HealthChecker：健康检查和重连
├── port_utils.py        # find_available_port：端口可用性检测和自动切换
├── registry.py          # （已弃用，保留向后兼容）
├── executor.py          # （已弃用，保留向后兼容）
└── web/                 # Dashboard Web 界面
    ├── __init__.py      # DashboardApp、SpaStaticFiles
    └── api.py           # REST API 14 个端点
```

## 核心架构

### 设计模式

MCPX 将多个 MCP 服务器的工具和资源收敛为两个入口点：
- **invoke**: 执行工具（使用 method 参数，格式为 "server.tool"），出错时返回可用服务器/工具/schema
- **read**: 读取 MCP 服务器的资源（支持文本和二进制内容）

工具列表和资源列表在服务器启动时生成描述文本，作为 tool description 暴露给 AI，无需额外的 describe 工具。

### GUI 路由架构

MCPX 在 GUI 模式下使用自定义 ASGI app 而不是 Starlette Router/Mount，原因如下：

**FastMCP 限制：**
- FastMCP 的 `http_app()` 内部硬编码路由 `Route("/mcp", ...)`
- 无法通过参数配置内部路由路径

**Starlette Mount 行为：**
- `Mount("/prefix", app=sub_app)` 会拼接路径
- 如果子应用定义 `Route("/mcp")`，最终路径变成 `/prefix/mcp`

**解决方案：**
- 自定义 ASGI app 手动路由分发
- `/api/*` → Dashboard API（去掉 `/api` 前缀）
- `/mcp/*` → MCP 端点（直接传递）
- 其他 → 静态文件/SPA

**Lifespan 处理：**
- 自定义 ASGI app 需要手动处理 lifespan 事件
- `combined_lifespan_app` 负责 startup/shutdown 事件分发
- 包含完整的异常处理和日志记录

**参考：** `src/mcpx/__main__.py:370-450`（详细架构说明注释）

### 核心类

**ServerManager（服务器管理器）**
- 合并了原来的 Registry 和 Executor 功能
- 使用 **ConnectionPool 连接池**替代 Session Isolation，提升性能
- 启动时连接所有 MCP 服务器获取工具/资源 Schema
- 缓存工具 Schema `_tools: dict[str, ToolInfo]`
- 缓存资源信息 `_resources: dict[str, ResourceInfo]`
- 缓存服务器信息 `_server_infos: dict[str, ServerInfo]`
- 集成健康检查 `HealthChecker`

**ConnectionPool（连接池）**
- 管理每个服务器的 MCP 客户端连接
- 连接复用，减少频繁创建/销毁连接的开销
- 自动连接上限管理和回收
- 使用 `async with pool.acquire() as client:` 模式

**ToonCompressor（压缩器）**
- 将 JSON 数据压缩为 TOON 格式
- 返回 `content`（压缩后）和 `structured_content`（原始）
- 可配置最小压缩阈值

### 数据流

```
AI → invoke(method="server.tool", arguments={...}) → ServerManager → pool.acquire() → MCP Server → TOON 压缩结果
AI → read(server_name, uri) → ServerManager._resources (缓存) / read_resource() → 资源内容
```

### 连接池模式

使用连接池替代原来的 Session Isolation：

```python
# ServerManager 初始化时创建连接池
for server_name, server_config in self._config.mcpServers.items():
    factory = self._create_client_factory(server_config)
    pool = ConnectionPool(factory, max_size=10, name=server_name)
    self._pools[server_name] = pool

# 每次请求从池中获取连接
async with self._pools[server_name].acquire() as client:
    result = await client.call_tool(tool_name, arguments)
# 连接自动归还到池中
```

## 关键实现细节

### 1. invoke method 参数格式

使用 `method` 参数指定目标工具，格式为 `server.tool`：

```python
# 执行工具
invoke(method="filesystem.read_file", arguments={"path": "/tmp/file.txt"})
```

**错误处理**：当调用失败时，`invoke` 会返回有用的信息：
- 服务器不存在：返回 `error` + `available_servers` 列表（如果没有服务器则返回 `hint`）
- 工具不存在：返回 `error` + `available_tools` 列表
- 参数无效：返回 `error` + `tool_schema`

### 2. 双返回格式

`invoke` 返回 `ToolResult`，包含：
- `content`: TOON 压缩后的字符串（供 AI 阅读）
- `structured_content`: 原始 JSON（供程序解析）

### 3. 多模态内容透传

`invoke` 返回类型支持多种内容：
- `ToolResult`: 普通数据（压缩 + 原始）
- `TextContent | ImageContent | EmbeddedResource`: 单项多模态内容
- `list[TextContent | ImageContent | EmbeddedResource]`: 多项多模态内容

透传逻辑在 `ServerManager._extract_result_data()`。

### 4. HTTP/SSE 模式的事件循环

`main()` 简化为单一初始化：
- 创建 ServerManager 在 lifespan 中初始化
- 避免双重初始化和跨事件循环问题

### 5. 参数校验

`invoke` 执行前校验：
- 必填字段（`required`）
- 未知参数（不在 `properties` 中）

### 6. 健康检查

`HealthChecker` 在后台定期探测服务器健康状态：
- 使用临时会话进行健康检查
- 检查完成后自动关闭临时会话
- 失败阈值达到后标记服务器不健康
- 提供 `get_server_health()` 查询状态
- **已修复**: `_health_check_loop` 真正执行定期检查

### 7. 端口自动切换

`main()` 启动前自动检测端口可用性：
- 使用 `find_available_port(port, host)` 检测端口是否被占用
- 如果端口被占用，自动尝试下一个可用端口（最多 100 次）
- 启动日志会显示实际使用的端口
- 实现位于 `port_utils.py` 模块

### 8. Web Dashboard

MCPX 提供 Web Dashboard 用于直观管理服务器、工具和资源：

**架构**：
```
Starlette App
├── /mcp     - MCP 协议端点
├── /api     - REST API (14 个端点)
└── /        - React SPA Dashboard
```

**REST API 端点**：

| 端点 | 方法 | 描述 |
|------|------|------|
| `/servers` | GET | 列出所有服务器 |
| `/servers/{name}` | GET | 获取服务器详情 |
| `/servers/{name}/toggle` | POST | 启停服务器 |
| `/tools` | GET | 列出所有工具 |
| `/tools/{server}/{tool}` | GET | 获取工具详情（含 schema） |
| `/tools/{server}/{tool}/toggle` | POST | 启停工具 |
| `/invoke` | POST | 执行工具 |
| `/resources` | GET | 列出所有资源 |
| `/read` | POST | 读取资源内容 |
| `/health` | GET | 健康总览 |
| `/health/{server}` | GET | 服务器健康状态 |
| `/health/{server}/check` | POST | 手动健康检查 |
| `/config` | GET | 获取配置 |
| `/config` | PUT | 保存配置并热重载 |

**前端组件**（`gui/src/components/`）：
- `Toast.tsx` - Toast 通知组件（success/error/warning/info）
- `ConfirmDialog.tsx` - 确认对话框（danger/warning/info 变体）
- `ToolDetailModal.tsx` - 工具详情模态框（schema + 测试界面）
- `ToolTester.tsx` - 工具执行测试界面（动态参数表单）
- `ServerEditor.tsx` - 服务器配置编辑器（添加/编辑/删除）

**CLI 参数**：
- `--gui` - 启用 Dashboard
- `--open` - 启动后打开浏览器
- `--desktop` - 使用 pywebview 创建原生窗口

**前端技术栈**：React 19 + TypeScript + Vite + Tailwind CSS v4
- 构建产物输出到 `src/mcpx/web/static/`
- Python 后端直接服务静态文件，无需 Node.js 运行时

## 配置

MCPX 使用 Claude Code 兼容的配置格式：

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "http-server": {
      "type": "http",
      "url": "http://localhost:3000/mcp",
      "headers": {"Authorization": "Bearer xxx"}
    }
  },
  "schema_compression_enabled": true,
  "toon_compression_enabled": true,
  "toon_compression_min_size": 3,
  "health_check_enabled": true,
  "health_check_interval": 30
}
```

### 配置说明

- `mcpServers`: 服务器配置字典（key 为服务器名称）
  - `type`: 传输类型，`"stdio"` 或 `"http"`（默认 `"stdio"`）
    - `"http"` 类型会自动检测：URL 包含 `/sse` 使用 `SSETransport`，否则使用 `StreamableHttpTransport`
  - `command`: stdio 类型的命令
  - `args`: 命令参数数组
  - `env`: 环境变量字典（可选）
  - `url`: http 类型的 URL（支持 SSE 和 Streamable HTTP）
  - `headers`: HTTP 请求头（可选）
  - `enabled`: 是否启用该服务器（默认 `true`，Dashboard 可修改）
- `disabled_tools`: 禁用的工具列表，格式为 `["server.tool"]`（Dashboard 管理）

## 测试要求

- 覆盖率 ≥ 70%
- 使用 pytest-asyncio 运行异步测试
- E2E 测试覆盖核心流程

## 当前分支

分支名与当前任务相关，PR 合并目标为 `main`。
