# cc-pipeline 用户指导文档

> 版本：v0.1 | 适用场景：C 嵌入式工程 UT 自动生成（通用 pipeline 编排）

---

## 目录

1. [安装](#1-安装)
2. [快速开始](#2-快速开始)
3. [配置文件详解](#3-配置文件详解)
4. [Pipeline DSL 语法](#4-pipeline-dsl-语法)
5. [三种 Executor 使用场景](#5-三种-executor-使用场景)
6. [Postcondition 门控写法](#6-postcondition-门控写法)
7. [变量注入](#7-变量注入)
8. [Retry 与回滚](#8-retry-与回滚)
9. [定时运行（Cron）](#9-定时运行cron)
10. [崩溃恢复](#10-崩溃恢复)
11. [日志与调试](#11-日志与调试)
12. [常见问题](#12-常见问题)

---

## 1. 安装

### 前置条件

- Python ≥ 3.10
- Git（支持 worktree）
- Claude Code CLI（`npm i -g @anthropic-ai/claude-code`）
- LLM API（如智谱 GLM、Anthropic Claude）

### 安装步骤

```bash
git clone git@github.com:alienflash01/cc-pipeline.git
cd cc-pipeline
pip install -e ".[dev]"
```

### 验证

```bash
cc-pipeline --version
# → cc-pipeline 0.1.0
```

### 配置 Claude Code

确保 `~/.claude/settings.json` 指向你的 API：

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "your-token",
    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"
  }
}
```

---

## 2. 快速开始

### 第一个 Pipeline

创建 `modules.yaml`：

```yaml
repo: /path/to/your/project
base_branch: main
concurrency: 3
max_retries: 2

pipeline:
  - id: generate
    executor: claude-code
    prompt: |
      你在为 {module} 模块生成单元测试。
      源码目录：{source_dir}
      读取所有源文件，为每个函数生成测试。
    postcondition:
      shell: "test -d tests/{module}"

modules:
  - name: auth
    spec_id: SPEC-001
    source_dir: src/auth/
    source_files:
      - auth_login.c
      - auth_token.c
    coverage:
      line_threshold: 80
      branch_threshold: 70
```

运行：

```bash
cc-pipeline run modules.yaml
```

输出：

```
🌙 cc-pipeline 0.1.0
   run_id=2026-06-30T23-00-00  concurrency=3  model=glm-4.6
   modules=['auth']

============================================================
  ✓ auth                  passed
============================================================
  1 passed, 0 failed  (run_id: 2026-06-30T23-00-00)
```

---

## 3. 配置文件详解

### 全局字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `repo` | string | **必填** | 被测仓库路径 |
| `base_branch` | string | `main` | worktree 基准分支 |
| `concurrency` | int | `5` | module 间并行数 |
| `max_retries` | int | `3` | 全局默认重试次数 |
| `output_branch_prefix` | string | `ut-auto` | PR 分支前缀 |

### Module 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 模块名（唯一标识） |
| `spec_id` | string | 规格编号（注入 prompt） |
| `source_dir` | string | 源码目录 |
| `source_files` | list | 被测文件列表（loop: per_file 时使用） |
| `coverage` | dict | 覆盖率阈值（注入 prompt 作为变量） |
| `variables` | dict | 自定义变量（注入 prompt） |

### 完整示例

```yaml
repo: /home/user/my-project
base_branch: develop
concurrency: 5
max_retries: 3
output_branch_prefix: ut-nightly

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "为 {module} 生成测试脚手架，源码在 {source_dir}"
    postcondition:
      shell: "test -d tests/{module}"

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "为 {file} 生成测试用例"
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold}"
    retry: 3
    depends_on: scaffold

  - id: evaluate
    executor: judge
    prompt: "评估测试质量"
    depends_on: generate

modules:
  - name: auth
    spec_id: SPEC-2026-001
    source_dir: src/auth/
    source_files: [auth_login.c, auth_token.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
    variables:
      mock_strategy: link-time

  - name: payment
    spec_id: SPEC-2026-002
    source_dir: src/payment/
    source_files: [payment_process.c]
    coverage:
      line_threshold: 85
      branch_threshold: 75
```

---

## 4. Pipeline DSL 语法

### Step 字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | string | **必填** | 步骤唯一标识 |
| `executor` | string | `claude-code` | 执行器类型 |
| `prompt` | string | `""` | 发送给 CC 的指令（支持变量注入） |
| `loop` | string | `null` | `per_file` = 逐文件串行 |
| `retry` | int | 全局 `max_retries` | 该步最大重试次数 |
| `depends_on` | string | `null` | 前置步骤 ID |
| `postcondition` | dict | `null` | 通过条件 |
| `output` | string | `null` | CC 产出状态文件名 |
| `skill` | string | `null` | CC 加载的 skill 名称 |
| `rollback` | string | `git-checkpoint` | 回滚方式 |

### loop: per_file

当一个 step 设置 `loop: per_file` 时，该步骤会对 module 的 `source_files` 列表中的每个文件执行一次，**串行执行**：

```yaml
- id: generate
  executor: claude-code
  loop: per_file
  prompt: "为 {file} 生成测试"
  # 如果 source_files: [a.c, b.c, c.c]
  # 则执行顺序：generate[a.c] → generate[b.c] → generate[c.c]
```

### depends_on

步骤间依赖关系，确保执行顺序：

```yaml
- id: generate
  depends_on: scaffold    # generate 在 scaffold 之后执行
- id: evaluate
  depends_on: generate    # evaluate 在 generate 之后执行
```

---

## 5. 三种 Executor 使用场景

### claude-code（不可信干活层）

```yaml
- id: generate
  executor: claude-code
  prompt: "读取 {source_dir}/{file}，生成 dtest 测试用例"
```

- CC 可读写 worktree 内的文件
- CC 自己写 `.pipeline/{step}.json`（自述状态）
- **不受信任** — 需要后续 verify 步骤确认

### shell（可信验证层）

```yaml
- id: verify
  executor: shell
  prompt: "gcov src/{module}/*.c && lcov --summary"
  postcondition:
    shell: "echo '{\"line\": 85, \"branch\": 72}'"
    expect: "$.line >= 80 && $.branch >= 70"
```

- 运行确定性命令（覆盖率、编译、lint）
- **完全受信任** — 输出直接作为门控依据

### judge（AI 裁判层）

```yaml
- id: evaluate
  executor: judge
  prompt: "读取测试文件，评估断言密度和边界覆盖，打分"
  postcondition:
    shell: "test $(cat .pipeline/score) -ge 60"
```

- 独立 CC 调用，**只读权限**（Read + Bash）
- 读取 verified 数据做主观判断
- 半可信 — 用于无法确定性量化的质量评估

### 信任传递规则

```
下一步 CC 的 prompt 注入数据优先级：
  verified.json（shell 确认） > judge.json（AI 裁判） > step.json（CC 自述）
```

---

## 6. Postcondition 门控写法

### 基本结构

```yaml
postcondition:
  shell: "your_command"
  expect: "expression"
```

### expect 表达式语法

| 表达式 | 说明 | 示例 |
|--------|------|------|
| `$.field >= value` | 数值比较 | `$.line >= 80` |
| `$.field == value` | 等于 | `$.errors == 0` |
| `$.field != value` | 不等于 | `$.status != "fail"` |
| `$.a >= 80 && $.b >= 70` | AND | 行覆盖 AND 分支覆盖 |
| `contains('text')` | stdout 包含文本 | `contains('passed')` |
| （省略） | 只要 shell 退出码 0 就通过 | — |

### 示例

```yaml
# 覆盖率门控
postcondition:
  shell: "gcov --json-output -"
  expect: "$.line >= {line_threshold} && $.branch >= {branch_threshold}"

# 文件存在检查
postcondition:
  shell: "test -f tests/test_{module}.c"

# 测试通过检查
postcondition:
  shell: "dtest_runner tests/ | tail -1"
  expect: "contains('ALL PASSED')"

# 断言密度检查
postcondition:
  shell: "count_asserts.sh tests/{module}/"
  expect: "$.density >= 2.0"
```

---

## 7. 变量注入

### 可用变量

| 变量 | 来源 | 示例值 |
|------|------|--------|
| `{module}` | modules.yaml → name | `auth` |
| `{file}` | loop 当前文件 | `auth_login.c` |
| `{source_dir}` | modules.yaml → source_dir | `src/auth/` |
| `{spec_id}` | modules.yaml → spec_id | `SPEC-001` |
| `{line_threshold}` | coverage.line_threshold | `80` |
| `{branch_threshold}` | coverage.branch_threshold | `70` |
| `{custom_var}` | modules.yaml → variables | `link-time` |
| `{.pipeline/xxx.json}` | 读取 JSON 文件内容注入 | `{"line": 85}` |

### 使用示例

```yaml
prompt: |
  你在为 {module} 模块生成测试。
  规格编号：{spec_id}
  源码目录：{source_dir}
  当前文件：{file}
  覆盖率要求：行 ≥ {line_threshold}%，分支 ≥ {branch_threshold}%
  Mock 策略：{mock_strategy}
  
  脚手架信息：{.pipeline/scaffold.json}
```

---

## 8. Retry 与回滚

### Retry 机制

当 postcondition 失败时：
1. **git rollback** 到上一个成功步骤的 checkpoint
2. 重新执行当前步骤
3. 最多重试 `retry` 次

```yaml
- id: generate
  retry: 3           # 最多重试 3 次
  rollback: git-checkpoint   # 回滚方式（默认）
```

### Git Checkpoint 机制

每个成功的步骤会创建 git tag：

```
pipeline/{module}/{step}/{attempt}
例: pipeline/auth/scaffold/1
    pipeline/auth/generate/1
```

**重试时**：回滚到上一个 checkpoint，清除当前步骤的产出物，然后重新执行。

### 重试日志

```
[step_start] step=generate attempt=1
[retry]      step=generate attempt=1 reason="coverage 65 < 80"
[step_start] step=generate attempt=2
[pass]       step=generate attempt=2 reason="All conditions passed"
```

---

## 9. 定时运行（Cron）

### 使用 cron-template.sh

```bash
# 编辑 scripts/cron-template.sh
CONFIG_FILE="/path/to/modules.yaml"
CONCURRENCY=5
MODEL="glm-4.6"

# 添加到 crontab
crontab -e
# 每晚 23:00 运行
0 23 * * * /path/to/cc-pipeline/scripts/cron-template.sh
```

### 手动 crontab

```bash
0 23 * * * cd /path/to/cc-pipeline && cc-pipeline run /path/to/modules.yaml --concurrency 5 --model glm-4.6
```

### GLM API 并发限制

| 并发数 | 状态 |
|--------|------|
| ≤ 5 | ✅ 稳定 |
| 6-7 | ⚠️ 边界 |
| ≥ 8 | ❌ 429 限流 |

**建议 `--concurrency=5`。**

---

## 10. 崩溃恢复

### 运行中崩溃

cc-pipeline 在 `~/.cc-pipeline/runs/{run_id}/orchestrator-state.json` 持久化状态。

### 查看状态

```bash
cc-pipeline status
# 列出最近的 run

cc-pipeline status --run-id 2026-06-30T23-00-00
# 显示某个 run 的各 module 状态
```

### 恢复

失败的 module 的 worktree 会保留（不清理），可以手动检查：

```bash
# 查看失败的 worktree
ls ~/.cc-pipeline/runs/{run_id}/worktrees/

# 进入 worktree 手动分析
cd ~/.cc-pipeline/runs/{run_id}/worktrees/auth/
git log --oneline  # 查看 checkpoint 历史
```

---

## 11. 日志与调试

### Transcript 日志

每个 module 的执行日志在 `~/.cc-pipeline/runs/{run_id}/{module}/transcript.jsonl`：

```bash
# 查看某个 module 的执行历史
cat ~/.cc-pipeline/runs/{run_id}/auth/transcript.jsonl | python3 -m json.tool

# 关键事件类型：
# step_start / pass / fail / retry
```

### 调试模式

```bash
# 单 module 运行（便于调试）
cc-pipeline run config.yaml --module auth

# 低并发（避免限流）
cc-pipeline run config.yaml --concurrency 1
```

---

## 12. 常见问题

### Q: CC 生成的测试质量差怎么办？

**A:** 使用三层信任模型：
1. `shell` executor 做确定性覆盖率检查（可信）
2. `judge` executor 做 AI 质量评测（断言密度等）
3. postcondition 不通过 → 自动 retry

### Q: 多个 module 会不会互相影响？

**A:** 不会。每个 module 在独立的 git worktree 中执行，完全隔离。成功后自动清理，失败后保留 worktree 供分析。

### Q: 如何限制 CC 不能修改源码？

**A:** 在 claude-code executor 的 prompt 中明确约束："只修改 tests/ 目录，不要修改 src/ 目录"。框架层的 GitCheckpoint 确保即使 CC 改了源码，rollback 时也会恢复。

### Q: 如何支持非 UT 场景？

**A:** cc-pipeline 是通用 pipeline 框架。只要把 pipeline 步骤改为你的场景（代码审查、文档生成、重构等），modules 改为你的目标单元即可。

### Q: 超时怎么处理？

**A:** 每个 executor 都支持 timeout 参数：
- CCExecutor: 默认 600 秒
- ShellExecutor: 默认 300 秒
- 超时后该步骤标记为失败，进入 retry 流程

---

## 附录

### CLI 命令速查

```bash
cc-pipeline run config.yaml                    # 运行 pipeline
cc-pipeline run config.yaml --module auth      # 只跑一个 module
cc-pipeline run config.yaml --concurrency 3    # 指定并行度
cc-pipeline run config.yaml --model glm-4.6    # 指定模型
cc-pipeline status                             # 查看运行历史
cc-pipeline status --run-id <id>               # 查看特定 run
cc-pipeline resume --run-id <id>               # 恢复中断的 run（TODO）
cc-pipeline --version                          # 版本号
cc-pipeline --help                             # 帮助
```

### 相关文档

| 文档 | 内容 |
|------|------|
| [DESIGN.md](docs/DESIGN.md) | 完整架构设计 |
| [ROADMAP.md](docs/ROADMAP.md) | 开发计划与里程碑 |
| [TESTING.md](docs/TESTING.md) | 测试方案 |
| [CONSISTENCY-REPORT.md](docs/CONSISTENCY-REPORT.md) | 实现 vs 设计一致性 |
