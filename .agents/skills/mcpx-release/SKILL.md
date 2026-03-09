---
name: mcpx-release
description: Use when preparing a release for the MCPX project. Only after all tests pass and documentation is updated.
---

# MCPX Release Process

## Overview

**版本发布遵循语义化版本**：MAJOR.MINOR.PATCH

## Version Number Format

```
MAJOR.MINOR.PATCH
例：0.1.4
  MAJOR = 0  (开发中)
  MINOR = 1  (新功能)
  PATCH = 4  (bug 修复)
```

## Release Checklist

```
□ 代码检查通过 (make check)
□ 测试全部通过 (make test)
□ 覆盖率 ≥ 70%
□ AGENTS.md 已更新
□ README.md 已更新
□ docs/roadmap.md 已更新
□ pyproject.toml 版本已更新
```

## Release Steps

```bash
# 1. 更新版本号
sed -i.bak 's/^version = "[0-9.]*"/version = "0.2.0"/' pyproject.toml
rm -f pyproject.toml.bak

# 2. 提交变更
git add .
git commit -m "chore: 发布版本 v0.2.0"

# 3. 创建标签
git tag -a v0.2.0 -m "Release v0.2.0"

# 4. 推送标签触发 CI
git push origin main --tags
```

## Auto-Release Flow

推送 tag 后 GitHub Actions 自动执行：
1. 构建包 (uv build)
2. 运行测试
3. 发布到 PyPI
4. 创建 GitHub Release

## Version Bump Rules

| 变更类型 | 版本变化 | 示例 |
|----------|----------|------|
| Bug 修复 | PATCH+1 | 0.1.3 → 0.1.4 |
| 新功能 | MINOR+1, PATCH=0 | 0.1.3 → 0.2.0 |
| 破坏性变更 | MAJOR+1 | 0.1.3 → 1.0.0 |

## Iron Law

```
测试失败或文档未更新？不要发布。

NO EXCEPTIONS:
- "文档稍后补" → 文档是发布的一部分
- "小 bug 不影响" → bug 就是 bug
- "CI 会修复的" → CI 不会修复，只检查
```
