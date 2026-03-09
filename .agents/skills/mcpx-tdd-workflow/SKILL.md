---
name: mcpx-tdd-workflow
description: Use when working on the MCPX project to implement features or bugfixes. REQUIRED before writing any implementation code.
---

# MCPX TDD Workflow

## Overview

**RED-GREEN-REFACTOR**: Write failing test first, then write minimal code to pass, then refactor.

## Core Pattern

```python
# 1. RED: 先写失败的测试
@pytest.mark.asyncio
async def test_sse_connection():
    config = ProxyConfig(...)
    registry = Registry(config)
    await registry.initialize()
    assert registry.has_server("sse-server")  # ← 此时测试会失败

# 2. GREEN: 写最小代码使测试通过
# 在 registry.py 中添加 SSE 支持

# 3. REFACTOR: 优化代码结构
# 提取 infer_transport_type_from_url 调用
```

## Quick Reference

| 阶段 | 操作 | 命令 |
|------|------|------|
| RED | 先写测试 | `uv run pytest tests/test_xxx.py -v` |
| GREEN | 实现代码 | `uv run pytest tests/ -v` |
| REFACTOR | 优化结构 | `uv run pytest tests/ -v` |

## MCPX Testing Stack

```bash
# 运行测试
uv run pytest tests/ -v --cov=src/mcpx

# 运行单个测试
uv run pytest tests/test_registry.py::test_sse_connection -v

# 覆盖率要求：≥ 70%
```

## Test File Organization

```
tests/
├── test_<module>.py          # 单元测试
├── test_e2e.py               # 端到端测试
└── test_coverage.py          # 覆盖率测试
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| 先写代码再写测试 | 删除代码，重新开始 |
| 测试就跳过 | TDD 的核心是看到测试失败再通过 |
| "我手动测过了" | 自动测试才是可重复的测试 |
| 测试太简单 | 测试应该覆盖正常和边界情况 |

## Iron Law

```
写代码前没有失败的测试？删除代码。重新开始。

NO EXCEPTIONS:
- 不要保留为"参考"
- 不要"边写边调整"
- 删除意味着删除
```
