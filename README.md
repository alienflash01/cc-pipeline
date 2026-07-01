# cc-pipeline

> **Multi-stage serial pipeline orchestrator for Claude Code** — module 间并行 + module 内串行 + CC 间上下文传递 + git 原生状态管理 + CO 式错误处理

[![tests](https://img.shields.io/badge/tests-225%20passed-brightgreen)]()
[![coverage](https://img.shields.io/badge/coverage-91%25-green)]()
[![license](https://img.shields.io/badge/license-MIT-blue)]()

## What It Does

给定一个 YAML 配置文件，cc-pipeline 自动完成：

1. 为每个 **module** 创建隔离的 git **worktree**
2. 在每个 worktree 内**串行**执行多步 pipeline（scaffold → generate → evaluate → lint）
3. **module 间并行**执行（ThreadPoolExecutor）
4. 每步有 **postcondition 门控**，不通过自动 **retry + git rollback**
5. **CC 间上下文传递** — `.pipeline/*.json` 自动注入下一步 prompt
6. **CO 式 4 层错误处理** — rate limit 退避 / 零工作检测 / 超时 / 崩溃保护
7. 全程 **git checkpoint** 管理状态，支持崩溃恢复
8. 完成后自动提交 **PR**

```
┌─── Module A (worktree-A) ──────────────────────────┐
│ scaffold → gen(file1) → gen(file2) → evaluate      │ ─┐
│ → lint → merge → PR                                │  │
└────────────────────────────────────────────────────┘  │
┌─── Module B (worktree-B) ──────────────────────────┐  ├── 并行
│ scaffold → gen(file1) → evaluate → lint → PR       │  │
└────────────────────────────────────────────────────┘ ─┘
```

## Quick Install

```bash
git clone git@github.com:alienflash01/cc-pipeline.git
cd cc-pipeline

# 一键安装（自动检测依赖）
bash scripts/install.sh

# 或手动安装
pip install -e .
# 开发模式（含 pytest）
pip install -e ".[dev]"
```

**前置条件：** Python ≥ 3.10 | Git | Claude Code CLI（可选，shell 模式不需要）

## Quick Start

```bash
# 创建配置文件
cat > modules.yaml << 'EOF'
repo: /path/to/repo
base_branch: main
concurrency: 5

pipeline:
  - id: generate
    executor: claude-code
    prompt: "为 {module} 生成测试，源码在 {source_dir}"
    output: generate.json
    postcondition:
      shell: "test -d tests/{module}"

modules:
  - name: auth
    source_dir: src/auth/
    source_files: [auth_login.c]
EOF

# 运行
cc-pipeline run modules.yaml --concurrency 5 --model glm-4.6

# 压力测试（5 模块 × 3 步骤，无需 API）
bash scripts/stress-test.sh
```

## Key Features

| 特性 | 说明 |
|------|------|
| **Pipeline DSL** | 声明式步骤定义，`loop: per_file` / `retry: N` / `depends_on` |
| **三层信任模型** | `claude-code`（干活）/ `shell`（可信验证）/ `judge`（AI 裁判） |
| **CC 间上下文传递** | `.pipeline/*.json` 自动注入下一步 prompt |
| **Git 原生状态** | 每步 commit + tag，retry 时 `rollback_to_latest` |
| **两层调度** | module 间并行 + module 内串行 |
| **CO 式错误处理** | 4 层：rate limit 退避 / CC 崩溃检测 / 零工作检测 / 超时 |
| **Rate limit 保护** | 前 5 次免费重试 + 60s backoff，不消耗 retry 预算 |
| **异常保护** | 无静默吞没，完整 traceback 写入 transcript |
| **崩溃恢复** | `orchestrator-state.json` + git checkpoint |
| **通用性** | 不限于 UT，任何多步 CC pipeline 场景 |

## Config Format

```yaml
repo: /path/to/repo
base_branch: main
concurrency: 5
max_retries: 3

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "为 {module} 生成测试脚手架，源码目录：{source_dir}"
    output: scaffold.json
    postcondition:
      shell: "test -d tests/{module}"

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "基于脚手架为 {file} 生成测试"
    output: generate.json
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold}"
    retry: 3
    depends_on: scaffold

  - id: verify
    executor: shell
    prompt: "gcov src/{module}/*.c && lcov --summary"
    depends_on: generate

  - id: evaluate
    executor: judge
    prompt: "读取 .pipeline/generate.json，评估测试质量"
    depends_on: verify

modules:
  - name: auth
    spec_id: SPEC-001
    source_dir: src/auth/
    source_files: [auth_login.c, auth_token.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
```

## Three Executor Types

| Executor | 信任度 | prompt 注入 | 用途 |
|---------|--------|:-:|------|
| `claude-code` | ❌ 不可信 | ✅ 上下文 + output 指令 | CC 干活（生成代码/测试） |
| `shell` | ✅ 可信 | ❌ 原始命令 | 确定性验证（覆盖率/编译/lint） |
| `judge` | 🔶 半可信 | ✅ 上下文 | AI 裁判（只读 + 评测） |

## CC Error Handling (CO-style)

```
Layer 1: Rate limit (429/1302) → sleep(60s) + free retry (max 5)
Layer 2: CC crash (returncode≠0) → skip postcondition, consume retry
Layer 3: Zero-work (empty stdout+stderr) → immediate retry
Layer 4: Timeout → catch TimeoutExpired, consume retry
```

## Architecture

```
src/cc_pipeline/
├── cli.py              # CLI 入口 (run/status/resume)
├── config.py           # YAML config parser
├── render.py           # Variable renderer ({module} {.pipeline/*.json})
├── compiler.py         # Pipeline compiler (YAML → CompiledStep)
├── executor.py         # CCExecutor + ShellExecutor
├── postcondition.py    # Shell + expect expression evaluator
├── git_checkpoint.py   # Git commit/tag/rollback/rollback_to_latest
├── runner.py           # ModuleRunner (serial steps + CO error handling)
├── worktree.py         # WorktreeManager (git worktree lifecycle)
├── orchestrator.py     # Orchestrator (parallel dispatch + exception guard)
├── state.py            # StateManager (crash recovery, thread-safe)
├── pr.py               # PRCreator (gh pr create)
└── logger.py           # JSONL transcript logger
```

## Testing

```bash
# All tests (225 tests, ~12s)
pytest tests/ -v --cov=cc_pipeline --cov-report=term-missing

# By layer
pytest tests/unit/         # 138 unit tests
pytest tests/integration/  # 40 integration tests
pytest tests/e2e/          # 47 e2e/blackbox tests

# Stress test (5 modules, no API needed)
bash scripts/stress-test.sh
```

## Scripts

| 脚本 | 用途 |
|------|------|
| `scripts/install.sh` | 一键安装（检测依赖 + pip install） |
| `scripts/stress-test.sh` | 压力测试（5 模块 × 3 步 × 3 并行） |
| `scripts/cron-template.sh` | Cron 定时运行模板 |

## Cron Integration

```bash
# Every night at 23:00
0 23 * * * /path/to/scripts/cron-template.sh
```

## Design Docs

| 文档 | 内容 |
|------|------|
| [USER-GUIDE.md](docs/USER-GUIDE.md) | 用户指导手册（15 章） |
| [DESIGN.md](docs/DESIGN.md) | 完整架构设计 |
| [ROADMAP.md](docs/ROADMAP.md) | 开发计划 + 魔改 CO 待办 |
| [TESTING.md](docs/TESTING.md) | 测试方案 |
| [CONSISTENCY-REPORT.md](docs/CONSISTENCY-REPORT.md) | 实现 vs 设计一致性 |

## License

MIT
