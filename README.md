# MCPX 2.1

> 原生 MCP 聚合内核 + 面向 Agent 的 `invoke/read` 投影层

## 现在的模型

MCPX 2.1 同时提供两个 surface：

| Surface | 用途 | 入口 |
|---|---|---|
| `projection` | 给 Agent/LLM 使用，只暴露 `invoke` 和 `read` 两个工具 | `/mcp` |
| `native` | 给开发者和高级客户端使用，聚合原生 MCP primitives | `/native` |

设计原则：

- 内核原生建模 `tools`、`resources`、`resource templates`、`prompts`
- `invoke/read` 的动态描述从原生 catalog 编译出来
- 不再把 `invoke/read` 当成协议真相，它们只是更适合 Agent 的压缩投影

## 关键能力

- 双 surface：同一个进程同时提供 `projection` 和 `native`
- 原生 catalog：统一缓存 upstream 的 tools/resources/templates/prompts/capabilities
- 原子 snapshot：刷新时整体替换 catalog，避免共享字典上的增量拼接
- 动态描述编译：`invoke/read` 描述按能力目录生成，而不是硬编码
- 原生 prompt/resource template/completion 透传
- native tasks、`resources/subscribe`、`list_changed` 通知
- discovery watcher + execution pool 分离
- in-memory upstream 覆盖测试，方便做聚合层回归

## 安装

```bash
uv sync
```

## 运行

```bash
uv run mcpx-toolkit config.json
```

默认会同时启动两个 HTTP endpoint：

- `http://127.0.0.1:8000/mcp`：projection surface
- `http://127.0.0.1:8000/native`：native surface

可选参数：

```bash
uv run mcpx-toolkit --host 0.0.0.0 --port 9000 config.json
```

## 配置

```json
{
  "mcpServers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "search": {
      "type": "http",
      "url": "http://localhost:3000/mcp"
    }
  }
}
```

支持：

- `stdio`
- `http`

## Projection Surface

Projection surface 只暴露两个工具：

### `invoke`

```python
invoke(
  ref={"server": "filesystem", "name": "read_file"},
  arguments={"path": "/tmp/demo.txt"},
  mode="call"
)
```

先做校验而不执行：

```python
invoke(
  ref={"server": "filesystem", "name": "read_file"},
  mode="validate"
)
```

### `read`

读取 direct resource：

```python
read(
  ref={"server": "filesystem", "uri": "file:///tmp/demo.txt"},
  mode="read"
)
```

先预览 resource template：

```python
read(
  ref={"server": "notes", "uriTemplate": "memo://notes/{slug}"},
  mode="preview"
)
```

再读取 template：

```python
read(
  ref={
    "server": "notes",
    "uriTemplate": "memo://notes/{slug}",
    "arguments": {"slug": "welcome"}
  },
  mode="read"
)
```

## Native Surface

Native surface 聚合 upstream MCP primitives：

- tools
- resources
- resource templates
- prompts
- completion
- tasks
- `resources/subscribe`
- `list_changed` notifications

为解决多 server 重名，native surface 会暴露 server-prefixed 名称：

- tool: `notes.echo_note`
- prompt: `notes.summarize_note`
- resource URI: `mcpx://notes/memo://static`
- resource template URI: `mcpx://notes/memo://notes/{slug}`

原始 canonical 标识保存在组件 `_meta.canonical` 中。

## 架构

```text
upstreams
  -> DiscoveryHub + ExecutionPools
  -> CatalogSnapshot
  -> ProjectionCompiler + Resolver
  -> ExecutionRouter
  -> native surface + projection surface
```

核心模块：

- [`src/mcpx/catalog.py`](/Users/francis/Projects/github/mcpx/src/mcpx/catalog.py)
- [`src/mcpx/upstreams.py`](/Users/francis/Projects/github/mcpx/src/mcpx/upstreams.py)
- [`src/mcpx/snapshot.py`](/Users/francis/Projects/github/mcpx/src/mcpx/snapshot.py)
- [`src/mcpx/execution.py`](/Users/francis/Projects/github/mcpx/src/mcpx/execution.py)
- [`src/mcpx/native.py`](/Users/francis/Projects/github/mcpx/src/mcpx/native.py)
- [`src/mcpx/runtime.py`](/Users/francis/Projects/github/mcpx/src/mcpx/runtime.py)
- [`src/mcpx/surfaces.py`](/Users/francis/Projects/github/mcpx/src/mcpx/surfaces.py)
- [`src/mcpx/__main__.py`](/Users/francis/Projects/github/mcpx/src/mcpx/__main__.py)

## 开发

```bash
uv run pytest tests/ -q
uv run ruff check src/mcpx tests
uv run mypy src/mcpx
```

## 当前范围

2.1 当前实现聚焦核心代理，不包含 Dashboard/desktop 管理界面。

native surface 使用 low-level MCP server 暴露原生协议面；
projection surface 继续只暴露 `invoke` / `read`。

1.x 遗留的 server/config/web/registry/executor 路径已经从主仓库移除。
