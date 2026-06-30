# cc-pipeline 开发计划

> 版本：v0.1 | 日期：2026-06-29

---

## 总览

分 4 个 Phase，每个 Phase 产出可独立验证的成果。

```
Phase 1 (MVP)    → 单 module、单步、跑通 CC 调用
Phase 2 (Pipeline) → 多步骤串行 + postcondition + retry
Phase 3 (并行)    → 多 module 并行 + worktree 隔离
Phase 4 (生产化)  → 质量评测 + PR + 崩溃恢复 + 定时触发
```

---

## Phase 1：MVP（1 周）

**目标：** 单 module、单步 CC 调用，验证 `claude -p` 链路。

### 任务清单

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 1.1 | 项目骨架 | 目录结构 + pyproject.toml + 入口 `cc-pipeline` | ⬜ |
| 1.2 | Config Loader | 解析 modules.yaml，输出 module 列表 | ⬜ |
| 1.3 | CC Executor | `claude -p` headless 调用封装 | ⬜ |
| 1.4 | 变量注入 | `{module}` / `{source_dir}` / `{file}` 模板替换 | ⬜ |
| 1.5 | 基础日志 | 控制台输出 + JSONL transcript | ⬜ |
| 1.6 | 冒烟测试 | 用 co-demo 的 Python 文件验证链路 | ⬜ |

### 验收标准

```bash
# 这条命令能跑通
cc-pipeline run examples/simple.yaml
# → 启动 1 个 CC，为 1 个模块生成测试，输出结果
```

### 目录结构

```
cc-pipeline/
├── README.md
├── pyproject.toml
├── docs/
│   ├── DESIGN.md
│   └── ROADMAP.md
├── src/
│   └── cc_pipeline/
│       ├── __init__.py
│       ├── cli.py              # CLI 入口
│       ├── config.py           # Config Loader
│       ├── executor.py         # CC Executor
│       └── render.py           # 变量注入
├── examples/
│   └── simple.yaml             # 最小示例
└── tests/
    └── test_config.py
```

---

## Phase 2：Pipeline 引擎（1.5 周）

**目标：** 多步骤串行 + postcondition 门控 + retry + git checkpoint。

### 任务清单

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 2.1 | Pipeline Compiler | YAML pipeline → Step 序列 | ⬜ |
| 2.2 | Shell Executor | subprocess 执行确定性命令 | ⬜ |
| 2.3 | Judge Executor | 只读模式 CC 调用 | ⬜ |
| 2.4 | Postcondition Evaluator | shell + expect 表达式评估 | ⬜ |
| 2.5 | Git Checkpoint | step 完成后 commit + tag | ⬜ |
| 2.6 | Retry + Rollback | 失败回滚 + 重试循环 | ⬜ |
| 2.7 | Loop (per_file) | 逐文件串行循环 | ⬜ |
| 2.8 | 上下文传递 | 读 `.pipeline/*.json` 注入下一步 prompt | ⬜ |
| 2.9 | 端到端测试 | scaffold → generate → evaluate 三步跑通 | ⬜ |

### 验收标准

```bash
# 3 步 pipeline 跑通
cc-pipeline run examples/ut-3step.yaml
# → scaffold CC → generate CC → evaluate CC（串行）
# → postcondition 门控生效
# → 故意失败触发 retry + rollback
```

---

## Phase 3：并行调度 + Worktree（1 周）

**目标：** 多 module 并行 + worktree 隔离 + 并发控制。

### 任务清单

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 3.1 | Worktree Manager | git worktree 创建/删除/清理 | ⬜ |
| 3.2 | 并行调度器 | ThreadPoolExecutor + module 间并行 | ⬜ |
| 3.3 | Module Pipeline Runner | 单 module 内串行 pipeline 执行 | ⬜ |
| 3.4 | 并发控制 | CC 并发数限制（GLM ≤5） | ⬜ |
| 3.5 | Module 状态追踪 | orchestrator-state.json | ⬜ |
| 3.6 | 端到端测试 | 3 module 并行跑通 | ⬜ |

### 验收标准

```bash
# 3 个 module 并行跑通
cc-pipeline run examples/multi-module.yaml --concurrency=3
# → 3 个 worktree 同时工作
# → 每个 module 内部串行执行步骤
# → 全部完成后汇总报告
```

---

## Phase 4：生产化（1.5 周）

**目标：** 质量评测集成 + PR 自动提交 + 崩溃恢复 + 定时触发。

### 任务清单

| # | 任务 | 产出 | 状态 |
|---|------|------|------|
| 4.1 | 覆盖率检查脚本 | gcov/lcov 解析 → JSON | ⬜ |
| 4.2 | 断言密度检查脚本 | 解析 dtest 宏密度 | ⬜ |
| 4.3 | Merge + PR | gh pr create 自动化 | ⬜ |
| 4.4 | 崩溃恢复 | resume 从 checkpoint 继续 | ⬜ |
| 4.5 | 失败分析报告 | 收集失败 module 的日志 + CC transcript | ⬜ |
| 4.6 | Cron 集成 | systemd-timer / crontab 模板 | ⬜ |
| 4.7 | 无人值守测试 | 完整夜间运行验证 | ⬜ |

### 验收标准

```bash
# 无人值守完整流程
0 23 * * * cc-pipeline run /path/to/modules.yaml --concurrency=5
# → 次日早上检查：所有 PR 已创建，失败 module 有分析报告
```

---

## 里程碑时间线

```
Week 1     Phase 1 (MVP)
            ├── 链路验证 ✅
            └── 单 module 单步跑通 ✅

Week 2-3   Phase 2 (Pipeline)
            ├── 多步骤串行 ✅
            ├── postcondition 门控 ✅
            └── retry + rollback ✅

Week 4     Phase 3 (并行)
            ├── 多 module 并行 ✅
            └── worktree 隔离 ✅

Week 5-6   Phase 4 (生产化)
            ├── 质量评测集成 ✅
            ├── PR 自动化 ✅
            ├── 崩溃恢复 ✅
            └── 定时触发 ✅
```

---

## 技术债 / 未来方向

| 项目 | 描述 | 优先级 |
|------|------|--------|
| 技能自进化 | 接入 Librarian + A/B 测试（借鉴 claude-overnight） | 中 |
| 多模型混用 | 按步骤指定不同模型（Opus 规划 + GLM 执行） | 中 |
| Web UI | 实时监控 pipeline 状态 | 低 |
| 变异测试集成 | mull / 源码变异脚本 | 中 |
| Pipeline 可视化 | 生成 DAG 图 | 低 |
| Plugin 系统 | 第三方 executor / postcondition 扩展 | 低 |

---

## 风险评估

| 风险 | 影响 | 缓解 |
|------|------|------|
| GLM API 限流（≤7 并发） | module 并行受限 | 并发设 5，留余量 |
| CC 生成的测试质量差 | PR 合并后引入问题 | 三层信任模型 + postcondition 门控 |
| worktree 磁盘占用 | 大量 module 时磁盘满 | pipeline 完成后自动清理 |
| CC 超时 | 单步卡住 | 超时杀死 + 标记失败 |
| git 操作冲突 | 多 worktree 同时操作 | 每个 worktree 独立分支，无冲突 |
