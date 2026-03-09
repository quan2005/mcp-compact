---
name: mcpx-documentation
description: Use when code changes affect user-facing features or architecture in the MCPX project. REQUIRED with every code change.
---

# MCPX Documentation

## Overview

**代码变更必须同步更新文档**

## Document Update Matrix

| 代码变更 | 必须更新 | 路径 |
|----------|----------|------|
| 新功能 | README.md, AGENTS.md, docs/roadmap.md | 项目根目录 |
| 架构变更 | AGENTS.md | 项目根目录 |
| Bug 修复 | docs/roadmap.md (如影响功能状态) | docs/ |
| 配置变更 | README.md, AGENTS.md | 项目根目录 |

## Documentation Files

### AGENTS.md
- **用途**: AI Agent 开发指南
- **更新时机**: 架构变更、新功能、开发命令
- **关键内容**: 核心架构、常用命令、项目结构

### README.md
- **用途**: 用户文档
- **更新时机**: 用户可见功能、配置变更
- **关键内容**: 安装、配置、使用方式

### docs/roadmap.md
- **用途**: 功能状态追踪
- **更新时机**: 功能完成、状态变更
- **关键内容**: 已完成/待办事项

## Update Checklist

```
□ AGENTS.md - 架构说明已更新
□ README.md - 用户文档已更新
□ docs/roadmap.md - 功能状态已更新
□ 代码注释 - 复杂逻辑有说明
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| "代码即文档" | AI 需要文字描述，不是自己读代码 |
| "稍后更新" | 代码变更时立即更新文档 |
| "文档太麻烦" | 没有文档 = 没有完成 |

## Iron Law

```
代码变了文档没变？变更未完成。

NO EXCEPTIONS:
- "小改动不写" → 小改动累积成文档腐烂
- "AI 能读懂代码" → 文档提供上下文和意图
- "下次一并更新" → 下次 = 永远不
```
