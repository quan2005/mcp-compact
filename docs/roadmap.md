# MCPX 2.1 Roadmap

## 目标状态

MCPX 2.1 以原生 MCP catalog 为内核，同时提供两个公开 surface：

- `projection`：给 Agent 使用，只暴露 `invoke` / `read`
- `native`：给开发者和高级客户端使用，聚合原生 MCP primitives

设计约束：

- 原生 catalog 是唯一真实源
- `invoke/read` 描述必须从 catalog 编译
- 不再以 `server.tool` 形式作为核心协议抽象
- 不再以 Dashboard/desktop 作为主产品形态

## 已完成

- 双 surface 运行时已经落地：
  - `/mcp` 暴露 projection surface
  - `/native` 暴露 native surface
- runtime 已拆成明确层次：
  - `catalog`
  - `upstreams`
  - `snapshot`
  - `compiler`
  - `resolver`
  - `execution`
- upstream 访问已分为：
  - `DiscoveryHub`
  - `ExecutionPools`
- native surface 已切到 low-level MCP server，并支持：
  - tools
  - resources
  - resource templates
  - prompts
  - completion
  - tasks
  - `resources/subscribe`
  - `list_changed` notifications
- projection surface 已具备：
  - `invoke(validate|call)`
  - `read(preview|read)`
  - 固定预算 description 编译
  - canonical selector 解析与建议
- 测试已覆盖：
  - projection `invoke(validate|call)`
  - projection `read(preview|read)`
  - native completion/tasks
  - watcher 驱动的 snapshot 刷新
  - resource subscribe / update 通知
  - task status notification 重写

## 当前缺口

- projection resolver 仍是 deterministic BM25F-lite，而不是完整语义检索
- 当前仍是单进程、内存 tasks store，不包含持久化与多节点协同

## 下一阶段

1. 继续增强 projection resolver：
   - family 折叠
   - 候选排序
   - 歧义拒绝
   - 更强的预算外召回
2. 为 native surface 增加 MCP Inspector / conformance fixture 自动化回归
3. 如有需要，再为 tasks 引入持久化存储与跨进程队列

## 非目标

- 不恢复 1.x 的 Dashboard/desktop 主路径
- 不恢复 `invoke(method="server.tool")` / `read(server_name, uri)` 作为主接口
- 不以 TOON 或任意压缩格式反向决定 catalog 结构
