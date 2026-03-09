---
name: mcpx-code-quality
description: Use when submitting code changes in the MCPX project. REQUIRED before any commit or PR.
---

# MCPX Code Quality

## Overview

**提交前必须通过所有检查**：lint → types → test

## The Check Sequence

```bash
# 1. 格式化代码
uv run ruff format src/mcpx tests/
uv run ruff check --fix src/mcpx tests/

# 2. 类型检查
uv run mypy src/mcpx

# 3. 运行测试
uv run pytest tests/ -v --cov=src/mcpx
```

## Exit Codes Matter

```bash
# 如果任何命令失败，停止！
uv run ruff check src/mcpx tests/ || exit 1
uv run mypy src/mcpx || exit 1
uv run pytest tests/ -v || exit 1
```

## Quality Standards

| 工具 | 用途 | 配置 | 标准 |
|------|------|------|------|
| ruff | 代码检查和格式化 | line-length=100 | 0 errors |
| mypy | 类型检查 | strict 模式 | 0 errors |
| pytest | 测试 | pytest-asyncio | 覆盖率 ≥ 70% |

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| 跳过类型检查 | `mypy` 发现的类型错误会在运行时爆炸 |
| 忽略 ruff 警告 | 警告通常是潜在 bug |
| 覆盖率 < 70% | 添加更多测试用例 |

## Pre-Commit Checklist

```
□ ruff format 完成
□ ruff check 0 错误
□ mypy 0 错误
□ pytest 全部通过
□ 覆盖率 ≥ 70%
```

## Iron Law

```
任何检查失败？不要提交。修复后再检查。

NO EXCEPTIONS:
- "这只是个小改动" → 小改动也引入 bug
- "我稍后修复" → 稍后 = 永远不
- "CI 会检查的" → 本地先检查，不要浪费 CI 时间
```
