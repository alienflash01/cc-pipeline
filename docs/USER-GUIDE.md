# cc-pipeline 用户指导文档

> 版本：v0.2 | 225 tests | 91% coverage | 更新日期：2026-07-01

---

## 目录

1. [安装](#1-安装)
2. [快速开始](#2-快速开始)
3. [配置文件详解](#3-配置文件详解)
4. [Pipeline DSL 语法](#4-pipeline-dsl-语法)
5. [三种 Executor 使用场景](#5-三种-executor-使用场景)
6. [CC 间上下文传递](#6-cc-间上下文传递)
7. [Postcondition 门控写法](#7-postcondition-门控写法)
8. [变量注入](#8-变量注入)
9. [Retry 与回滚](#9-retry-与回滚)
10. [CC 错误处理（CO 式分层）](#10-cc-错误处理co-式分层)
11. [定时运行（Cron）](#11-定时运行cron)
12. [崩溃恢复](#12-崩溃恢复)
13. [日志与调试](#13-日志与调试)
14. [异常处理](#14-异常处理)
15. [常见问题](#15-常见问题)

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
   run_id=2026-07-01T23-00-00  concurrency=3  model=glm-4.6
   modules=['auth']

============================================================
  ✓ auth                  passed
============================================================
  1 passed, 0 failed  (run_id: 2026-07-01T23-00-00)
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
    output: scaffold.json
    postcondition:
      shell: "test -d tests/{module}"

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "为 {file} 生成测试用例"
    output: generate.json
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold}"
    retry: 3
    depends_on: scaffold

  - id: verify
    executor: shell
    prompt: "gcov src/{module}/*.c && lcov --summary -o .pipeline/generate.verified.json"
    depends_on: generate

  - id: evaluate
    executor: judge
    prompt: "读取 .pipeline/generate.verified.json，评估测试质量"
    output: evaluate.json
    depends_on: verify

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
| `executor` | string | `claude-code` | 执行器类型（`claude-code` / `shell` / `judge`） |
| `prompt` | string | `""` | 发送给 CC 的指令（支持变量注入） |
| `loop` | string | `null` | `per_file` = 逐文件串行 |
| `retry` | int | 全局 `max_retries` | 该步最大重试次数 |
| `depends_on` | string | `null` | 前置步骤 ID |
| `postcondition` | dict | `null` | 通过条件 |
| `output` | string | `null` | CC 产出状态文件名（写入 `.pipeline/{output}`） |
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
  output: generate.json
```

- CC 可读写 worktree 内的文件
- 框架自动在 prompt 尾部追加指令，要求 CC 写 `.pipeline/{output}.json`
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
- **prompt 就是 shell 命令本身**，不会被注入上下文或 output 指令
- **完全受信任** — 输出直接作为门控依据

### judge（AI 裁判层）

```yaml
- id: evaluate
  executor: judge
  prompt: "读取测试文件，评估断言密度和边界覆盖，打分"
  output: evaluate.json
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

## 6. CC 间上下文传递 ⭐ 新

### 工作原理

cc-pipeline 通过 `.pipeline/` 目录在 CC 之间传递上下文：

```
scaffold CC 执行
    ↓ 框架在 prompt 尾部追加：
    "请将关键信息写入 .pipeline/scaffold.json"
    ↓ CC 写入文件
.pipeline/scaffold.json = {"files_created": ["test_auth.c"], ...}

generate CC 执行
    ↓ 框架自动扫描 .pipeline/*.json，注入到 prompt：
    "--- 前序步骤的上下文 ---
     [scaffold.json]: {"files_created": ["test_auth.c"], ...}
     ---"
    ↓ CC 看到 scaffold 的产出
```

### output 字段

设置 `output` 后，框架自动做两件事：

1. **执行前**：创建 `.pipeline/` 目录（如不存在）
2. **prompt 追加**：在 CC 的 prompt 尾部追加写入指令

```yaml
- id: scaffold
  executor: claude-code
  prompt: "生成测试脚手架"
  output: scaffold.json    # ← CC 会被要求写这个文件
```

### 自动上下文注入

**仅对 `claude-code` 和 `judge` executor 生效**（shell executor 不注入）：

- 每步执行前，扫描 worktree 内 `.pipeline/*.json`
- 所有文件内容以 `[filename]: content` 格式注入到 prompt
- 顺序：按文件名排序

### 什么情况下不注入

| 情况 | 行为 |
|------|------|
| `.pipeline/` 目录为空 | 不注入上下文段，仅追加 output 指令 |
| `.pipeline/` 不存在 | 自动创建空目录 |
| step 没有 output 字段 | 不追加写入指令，但仍注入前序上下文 |
| executor 是 shell | 完全跳过注入和追加 |

---

## 7. Postcondition 门控写法

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

## 8. 变量注入

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

## 9. Retry 与回滚

### Retry 机制

当 CC 执行失败或 postcondition 不通过时：
1. **git rollback** 到上一个成功步骤的最新 checkpoint
2. 清除当前步骤的产出物
3. 重新执行当前步骤
4. 最多重试 `retry` 次

```yaml
- id: generate
  retry: 3           # 最多重试 3 次
  rollback: git-checkpoint   # 回滚方式（默认）
```

### Git Checkpoint 机制

每个成功的步骤会创建 git tag：

```
pipeline/{module}/{step}/{attempt}
例: pipeline/auth/scaffold/3     ← scaffold 第 3 次才过
    pipeline/auth/generate/1     ← generate 一次过
```

**重试时**：使用 `rollback_to_latest()` 回滚到上一个步骤的**最后一次成功 attempt**（不是 attempt=1），确保回滚到的是一个已验证的正确状态。

### 重试日志

```
[step_start] step=generate attempt=1
[retry]      step=generate attempt=1 reason="coverage 65 < 80"
[step_start] step=generate attempt=2
[pass]       step=generate attempt=2 reason="All conditions passed"
```

---

## 10. CC 错误处理（CO 式分层） ⭐ 新

cc-pipeline 实现了 claude-overnight 风格的 4 层 CC 错误处理策略：

### 错误分类

| 层 | 错误类型 | 行为 | 消耗 retry 预算？ |
|----|---------|------|:-:|
| 1 | **Rate limit (429/1302)** | 等待退避后重试 | ❌ 前 5 次免费 |
| 2 | **CC 崩溃 (returncode≠0)** | 跳过 postcondition，直接重试 | ✅ |
| 3 | **零工作检测** | CC 秒退无产出 → 直接重试 | ✅ |
| 4 | **Timeout** | 捕获 TimeoutExpired → 重试 | ✅ |

### Rate Limit 处理细节

```
常量：
  MAX_FREE_RATE_LIMIT_RETRIES = 5   # 免费重试次数
  RATE_LIMIT_BACKOFF_SECS = 60      # 每次退避等待秒数

流程：
  429 → sleep(60s) → 重试（不消耗预算）
  429 → sleep(60s) → 重试（不消耗预算）
  ... 重复最多 5 次 ...
  仍然 429 → 转为 CC_FAILED，开始消耗 retry 预算
```

### 零工作检测

当 CC 满足以下**全部条件**时判定为零工作：
- `returncode == 0`
- `stdout` 全为空白
- `stderr` 全为空白

这通常意味着 CC 秒退了，没有实际执行任何任务。

### 未知异常

CC 执行过程中抛出未预期异常（如 ConnectionError）时：
- 归类为 `UNKNOWN_ERROR`
- 记录异常信息到 ExecResult.reason
- 消耗 retry 预算

---

## 11. 定时运行（Cron）

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

## 12. 崩溃恢复

### 运行中崩溃

cc-pipeline 在 `{run_dir}/orchestrator-state.json` 持久化状态。每个 module 的状态实时更新（线程安全）：

```json
{
  "run_id": "2026-07-01T23-00-00",
  "saved_at": "2026-07-01T23:05:32",
  "modules": {
    "auth": {"status": "passed", "steps_completed": 3, "steps_total": 3},
    "payment": {"status": "error", "error": "disk full"},
    "crypto": {"status": "running", "worktree": "/tmp/.../crypto"}
  }
}
```

### 状态值含义

| status | 含义 |
|--------|------|
| `running` | 正在执行 |
| `passed` | 全部步骤通过 |
| `failed` | 步骤失败，retry 耗尽 |
| `error` | 发生异常（非 pipeline 逻辑失败） |

### 查看状态

```bash
cc-pipeline status
# 列出最近的 run

cc-pipeline status --run-id 2026-07-01T23-00-00
# 显示某个 run 的各 module 状态
```

### 恢复

失败的 module 的 worktree 会保留（不清理），可以手动检查：

```bash
# 查看失败的 worktree
ls {run_dir}/worktrees/

# 进入 worktree 手动分析
cd {run_dir}/worktrees/auth/
git log --oneline  # 查看 checkpoint 历史
git tag -l "pipeline/auth/*"  # 查看所有 checkpoint tag
```

---

## 13. 日志与调试

### Transcript 日志

每个 module 的执行日志在 `{run_dir}/{module}/transcript.jsonl`：

```bash
# 查看某个 module 的执行历史
cat {run_dir}/auth/transcript.jsonl | python3 -m json.tool

# 关键事件类型：
# step_start       — 步骤开始执行
# pass             — 步骤通过
# fail             — 步骤失败
# retry            — 重试
# module_exception — 异常（含完整 traceback）
```

### 异常日志

当 module 执行中发生异常时，完整 traceback 写入 transcript：

```json
{
  "event": "module_exception",
  "error": "FileNotFoundError: git not found",
  "traceback": "Traceback (most recent call last):\n  File ...\n...",
  "timestamp": "2026-07-01T23:05:32"
}
```

### 调试模式

```bash
# 单 module 运行（便于调试）
cc-pipeline run config.yaml --module auth

# 低并发（避免限流）
cc-pipeline run config.yaml --concurrency 1
```

---

## 14. 异常处理 ⭐ 新

### Orchestrator 异常保护

每个 module 的执行被 try/except 包裹，保证：

| 场景 | 行为 |
|------|------|
| Worktree 创建失败 | 记录异常到 transcript，无 worktree 残留 |
| Pipeline 执行中异常 | 记录 traceback，**worktree 保留**供分析 |
| State 更新 | 标记 `status=error`，记录错误信息 |
| PR 创建失败 | 忽略（best-effort），不影响 pipeline 结果 |

### 无静默吞没

所有异常都会：
1. 写入 transcript.jsonl（`module_exception` 事件 + 完整 traceback）
2. 更新 orchestrator-state.json（`status=error` + error 字段）
3. 返回到 Orchestrator 结果（`results[0]["error"]`）

---

## 15. 常见问题

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
- 超时后该步骤标记为 TIMEOUT，进入 retry 流程（消耗预算）

### Q: CC 一直返回 429 怎么办？

**A:** 框架自动处理：
- 前 5 次 429：等待 60 秒后免费重试（不消耗 retry 预算）
- 5 次后仍然 429：转为普通失败，开始消耗 retry 预算
- retry 预算耗尽：该步骤标记为 failed

### Q: CC 间怎么传递数据？

**A:** 通过 `.pipeline/` 目录：
1. 设置 step 的 `output` 字段（如 `output: scaffold.json`）
2. CC 被要求将结果写入 `.pipeline/scaffold.json`
3. 下一步的 CC（claude-code/judge）自动收到前序所有 `.pipeline/*.json` 的内容

注意：shell executor 不注入上下文（它的 prompt 就是命令本身）。

### Q: 回滚后数据安全吗？

**A:** 回滚使用 `git reset --hard` + `git clean -fd --exclude=.pipeline/`，恢复到上一个成功步骤的最新 checkpoint。`.pipeline/` 目录被保留（`--exclude`）。

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
| [DESIGN.md](DESIGN.md) | 完整架构设计 |
| [ROADMAP.md](ROADMAP.md) | 开发计划与里程碑 |
| [TESTING.md](TESTING.md) | 测试方案 |
| [CONSISTENCY-REPORT.md](CONSISTENCY-REPORT.md) | 实现 vs 设计一致性 |

### 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v0.2 | 2026-07-01 | CC 上下文传递、CO 式错误处理、rate limit 保护、orchestrator 异常保护、rollback_to_latest |
| v0.1 | 2026-06-30 | 初始版本：Phase 1-4 开发完成，135 tests |
