#!/bin/bash

# MCPX 回归测试验证脚本
# 用于提交代码前进行全面验证

set -e  # 遇到错误立即退出

echo "======================================"
echo "MCPX Regression Test Suite"
echo "======================================"
echo ""

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 测试计数器
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# 测试函数
run_test() {
    local test_name="$1"
    local test_command="$2"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo ""
    echo "▶ Test $TOTAL_TESTS: $test_name"
    echo "  Command: $test_command"
    echo ""

    if eval "$test_command"; then
        echo -e "${GREEN}✓ PASSED${NC}: $test_name"
        PASSED_TESTS=$((PASSED_TESTS + 1))
    else
        echo -e "${RED}✗ FAILED${NC}: $test_name"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        return 1
    fi
}

# 1. 代码格式检查
echo ""
echo "=== Phase 1: Code Formatting ==="
echo ""

run_test "Check import sorting" "uv run ruff check src/mcpx tests/ --select I"
run_test "Format code" "uv run ruff format src/mcpx tests/ --check"

# 2. 代码质量检查
echo ""
echo "=== Phase 2: Code Quality ==="
echo ""

run_test "Lint check" "uv run ruff check src/mcpx tests/"
run_test "Type check" "uv run mypy src/mcpx"

# 3. 单元测试
echo ""
echo "=== Phase 3: Unit Tests ==="
echo ""

run_test "All tests with coverage" "uv run pytest tests/ -v --cov=src/mcpx --cov-report=term-missing --cov-fail-under=70"

# 4. 特定功能测试
echo ""
echo "=== Phase 4: Feature-Specific Tests ==="
echo ""

run_test "Config loading" "uv run pytest tests/test_mcpx.py::test_load_config_from_file -v"
run_test "Server creation" "uv run pytest tests/test_mcpx.py::test_create_server -v"
run_test "Tool descriptions" "uv run pytest tests/test_mcpx.py::test_update_tool_descriptions -v"
run_test "GUI routing" "uv run pytest tests/test_mcpx.py::test_gui_app_path_routing -v"
run_test "Static files routing" "uv run pytest tests/test_mcpx.py::test_static_files_skip_prefixes -v"

# 5. 集成测试（如果存在）
echo ""
echo "=== Phase 5: Integration Tests ==="
echo ""

if [ -d "tests/integration" ]; then
    run_test "Integration tests" "uv run pytest tests/integration/ -v"
else
    echo -e "${YELLOW}⊘ SKIPPED${NC}: No integration tests found"
fi

# 6. 文档检查
echo ""
echo "=== Phase 6: Documentation ==="
echo ""

run_test "CLAUDE.md exists" "test -f CLAUDE.md"
run_test "README.md exists" "test -f README.md"

# 7. 安全检查
echo ""
echo "=== Phase 7: Security Checks ==="
echo ""

run_test "No hardcoded secrets" "! grep -r 'sk-[a-zA-Z0-9]\\{48\\}' src/ 2>/dev/null"
run_test "No .env files tracked" "! git ls-files | grep -q '\.env$'"

# 8. Git 状态检查
echo ""
echo "=== Phase 8: Git Status ==="
echo ""

echo "Checking for uncommitted changes..."
if git diff-index --quiet HEAD --; then
    echo -e "${GREEN}✓ No uncommitted changes${NC}"
else
    echo -e "${YELLOW}⚠ Uncommitted changes detected${NC}"
    echo ""
    git status --short
fi

# 测试总结
echo ""
echo "======================================"
echo "Test Summary"
echo "======================================"
echo ""
echo -e "Total:  $TOTAL_TESTS"
echo -e "Passed: ${GREEN}$PASSED_TESTS${NC}"
echo -e "Failed: ${RED}$FAILED_TESTS${NC}"
echo ""

if [ $FAILED_TESTS -eq 0 ]; then
    echo -e "${GREEN}✅ All tests passed! Ready to commit.${NC}"
    exit 0
else
    echo -e "${RED}❌ Some tests failed. Please fix before committing.${NC}"
    exit 1
fi
