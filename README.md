# cc-pipeline

> **Multi-stage serial pipeline orchestrator for Claude Code** — module 间并行 + module 内串行 + CC 间上下文传递 + git 原生状态管理

## What It Does

给定一个 YAML 配置文件，cc-pipeline：

1. 为每个 **module** 创建隔离的 git **worktree**
2. 在每个 worktree 内**串行**执行多步 pipeline（scaffold → generate → evaluate → lint）
3. **module 间并行**执行（ThreadPoolExecutor）
4. 每步有 **postcondition 门控**，不通过自动 **retry + rollback**
5. 全程 **git checkpoint** 管理状态，支持崩溃恢复
6. 完成后自动提交 **PR**

```
┌─── Module A (worktree-A) ──────────────────────────┐
│ scaffold → gen(file1) → gen(file2) → evaluate      │ ─┐
│ → lint → merge → PR                                │  │
└────────────────────────────────────────────────────┘  │
┌─── Module B (worktree-B) ──────────────────────────┐  ├── 并行
│ scaffold → gen(file1) → evaluate → lint → PR       │  │
└────────────────────────────────────────────────────┘ ─┘
```

## Install

```bash
git clone git@github.com:alienflash01/cc-pipeline.git
cd cc-pipeline
pip install -e ".[dev]"
```

## Quick Start

```bash
# Run a pipeline
cc-pipeline run modules.yaml

# With options
cc-pipeline run modules.yaml --concurrency 5 --model glm-4.6

# Single module
cc-pipeline run modules.yaml --module auth

# Check status
cc-pipeline status
```

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
    postcondition:
      shell: "test -d tests/{module}"

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "基于脚手架为 {file} 生成测试"
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold}"
    retry: 3
    depends_on: scaffold

  - id: evaluate
    executor: judge
    prompt: "评估测试质量"
    postcondition:
      shell: "test $(cat .pipeline/score) -ge 60"
    depends_on: generate

modules:
  - name: auth
    spec_id: SPEC-001
    source_dir: src/auth/
    source_files: [auth_login.c, auth_token.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
```

## Three Executor Types

| Executor | 信任度 | 用途 |
|---------|--------|------|
| `claude-code` | ❌ 不可信 | CC 干活（生成代码/测试） |
| `shell` | ✅ 可信 | 确定性验证（覆盖率/编译/lint） |
| `judge` | 🔶 半可信 | AI 裁判（只读 + 评测脚本） |

## Key Features

- **Pipeline DSL** — 声明式步骤定义，loop/retry/depends_on
- **三层信任模型** — CC 自述 vs 确定性验证 vs AI 裁判
- **Git 原生状态** — 每步 commit + tag，retry 时精确回滚
- **两层调度** — module 间并行 + module 内串行
- **CC 间上下文传递** — `.pipeline/*.json` 文件注入
- **崩溃恢复** — `orchestrator-state.json` + git checkpoint
- **通用性** — 不限于 UT，任何多步 CC pipeline 场景

## Architecture

```
src/cc_pipeline/
├── cli.py              # CLI 入口 (run/resume/status)
├── config.py           # YAML config parser
├── render.py           # Variable renderer ({module} {.pipeline/*.json})
├── compiler.py         # Pipeline compiler (YAML → CompiledStep)
├── executor.py         # CCExecutor + ShellExecutor
├── postcondition.py    # Shell + expect expression evaluator
├── git_checkpoint.py   # Git commit/tag/rollback
├── runner.py           # ModuleRunner (serial step execution)
├── worktree.py         # WorktreeManager (git worktree lifecycle)
├── orchestrator.py     # Orchestrator (parallel module dispatch)
├── state.py            # StateManager (crash recovery)
├── pr.py               # PRCreator (gh pr create)
└── logger.py           # JSONL transcript logger
```

## Cron Integration

```bash
# Every night at 23:00
0 23 * * * /path/to/scripts/cron-template.sh
```

See `scripts/cron-template.sh` for a ready-to-use template.

## Testing

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=cc_pipeline --cov-report=term-missing

# Unit only
pytest tests/unit/ -v

# Integration only
pytest tests/integration/ -v
```

## Design Docs

- [DESIGN.md](docs/DESIGN.md) — 完整架构设计
- [ROADMAP.md](docs/ROADMAP.md) — 开发计划
- [TESTING.md](docs/TESTING.md) — 测试方案

## License

MIT
