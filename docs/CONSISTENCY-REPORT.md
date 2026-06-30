# 实现与设计一致性检查报告

> 检查日期：2026-06-30

## 逐项对比

### ✅ 完全一致（10 项）

| 设计要求 | 实现位置 | 状态 |
|---------|---------|------|
| Config Loader 解析 modules.yaml | config.py:49 `load_config()` | ✅ |
| Pipeline Compiler 编译 YAML→Steps | compiler.py:29 `PipelineCompiler` | ✅ |
| 三种 Executor (claude-code/shell/judge) | executor.py + runner.py:123 | ✅ |
| Postcondition 门控 (shell+expect) | postcondition.py:19 `evaluate()` | ✅ |
| Git Checkpoint (commit+tag+rollback) | git_checkpoint.py:8 | ✅ |
| Retry + Rollback 循环 | runner.py:66-105 | ✅ |
| Loop per_file 展开 | compiler.py:67-76 | ✅ |
| 两层调度 (并行module+串行step) | orchestrator.py:54 + runner.py:62 | ✅ |
| Worktree 隔离 + 生命周期 | worktree.py:9 | ✅ |
| 变量注入 {module}/{file}/{.pipeline/*.json} | render.py:9 | ✅ |

### ⚠️ 部分实现（3 项）

| 设计要求 | 实现状态 | 差异 |
|---------|---------|------|
| **on_complete (merge+PR)** | PRCreator 已实现但 **Orchestrator 未调用** | Orchestrator._run_module 完成 pipeline 后没有调用 PR |
| **StateManager 集成** | StateManager 已实现但 **Orchestrator 未调用** | Orchestrator 运行中不保存 state，无法崩溃恢复 |
| **Judge executor 只读权限** | runner.py:131 设置了 allowed_tools=["Read","Bash"] | ✅ 已实现，但未集成到 config 校验 |

### ❌ 未实现（2 项）

| 设计要求 | 说明 |
|---------|------|
| **Resume 命令** | cli.py `_cmd_resume()` 还是 TODO 占位符 |
| **output 字段写入** | CompiledStep.output 字段存在但 runner 未让 CC 写 `.pipeline/{output}.json` |
