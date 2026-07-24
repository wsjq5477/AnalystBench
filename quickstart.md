# AnalystBench 快速开始

选择适合你的路径：

| 路径 | 适合谁 | 需要数据库？ |
|------|--------|-------------|
| **A. 单次打分与测评** | 只想评分几份报告就走 | ❌ |
| **B. 数据库部署与前端支持** | 需要版本管理、批量评测、前端 UI | ✅ |

---

## 路径 A：单次打分与测评

### 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### Claude Skill 评分（推荐）

```text
/analystbench-evaluate 使用 case/case-1.json 直接评分并对比：
case/test-1-agent-1.md
case/test-1-skill-1.md
```

### CLI 评分

```bash
.venv/bin/analystbench evaluate ./case/case-1.json ./case/test-1-agent-1.md ./case/test-1-skill-1.md
```

结果输出到 `data/results/`，不写入数据库。

---

## 路径 B：数据库部署与前端支持

### 安装 + 初始化

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/analystbench db-upgrade
```

### 导入并发布 Case

```bash
.venv/bin/analystbench case-import ./HM_PANIC_SYSMGR-case1.json \
  --case-key HM_PANIC_SYSMGR-case1 \
  --test-set kernel-log-analysis \
  --category panic
```

### 评分（数据库模式）

```bash
.venv/bin/analystbench evaluate HM_PANIC_SYSMGR-case1 \
  ./HM_PANIC_SYSMGR-test1-agent-1.md \
  ./HM_PANIC_SYSMGR-test1-skill-1.txt
```

### 启动 API 与 Worker

```bash
# 终端 1
.venv/bin/analystbench api

# 终端 2
.venv/bin/analystbench worker
```

---

## 详细文档

| 文档 | 说明 |
|------|------|
| [docs/quickstart.md](docs/quickstart.md) | 完整指南：两条路径的分步说明、评分规则、Judge 类型 |
| [docs/scoring-input.md](docs/scoring-input.md) | Case JSON 字段说明、评分策略、AI 报告格式 |
| [docs/skills.md](docs/skills.md) | 4 个 Claude Skill 说明 |
| [docs/cli-workflow.md](docs/cli-workflow.md) | 数据库模式完整 CLI 流程 |
| [docs/operations.md](docs/operations.md) | 部署、备份恢复、安全隐私 |
