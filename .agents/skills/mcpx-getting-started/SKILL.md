---
name: mcpx-getting-started
description: Use when starting any work on the MCPX project. Loads all required MCPX workflow skills.
---

# MCPX Getting Started

## Overview

MCPX 项目使用 **skills** 规范 AI 开发流程，而非人类文档。

## Required Skills

开发 MCPX 时必须加载以下 skills：

```bash
# 这些 skills 在 .Codex/skills/ 目录
mcpx-tdd-workflow      # TDD 开发流程（RED-GREEN-REFACTOR）
mcpx-code-quality      # 代码质量检查（lint/types/test）
mcpx-documentation     # 文档更新规范
mcpx-release           # 版本发布流程
```

## Development Flow

```
1. 开始任务
   ↓
2. 加载 mcpx-tdd-workflow
   ↓ 先写失败的测试
3. 实现代码
   ↓ RED → GREEN → REFACTOR
4. 加载 mcpx-code-quality
   ↓ lint + types + test
5. 加载 mcpx-documentation
   ↓ 更新文档
6. 准备发布时加载 mcpx-release
```

## Quick Reference

| 阶段 | Skill | 命令 |
|------|-------|------|
| 开发 | mcpx-tdd-workflow | `uv run pytest tests/ -v` |
| 检查 | mcpx-code-quality | `uv run ruff check && uv run mypy` |
| 文档 | mcpx-documentation | 更新 AGENTS.md/README.md |
| 发布 | mcpx-release | `git tag v0.x.x` |

## Installing Skills

复制项目 skills 到个人目录：

```bash
cp -r .Codex/skills/* ~/.Codex/skills/
```

## Project Context

- **语言**: Python 3.12+
- **框架**: FastMCP 3.0
- **包管理**: uv
- **测试**: pytest + pytest-asyncio
- **覆盖率要求**: ≥ 70%

查看 AGENTS.md 获取完整架构信息。
