# cc-pipeline 用户指导文档

> 版本：v0.3 | 449 tests | 95% coverage | 更新日期：2026-07-04

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
9. [Retry、回滚与 on_failure 回跳](#9-retry回滚与-on_failure-回跳)
10. [CC 错误处理（CO 式分层）](#10-cc-错误处理co-式分层)
11. [定时运行（Cron）](#11-定时运行cron)
12. [Daemon 模式（后台运行）](#12-daemon-模式后台运行)
13. [崩溃恢复](#13-崩溃恢复)
14. [运行报告](#14-运行报告)
15. [日志与调试](#15-日志与调试)
16. [异常处理](#16-异常处理)
17. [常见问题](#17-常见问题)

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
# → cc-pipeline 0.3.0
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

### 卸载

```bash
cc-pipeline uninstall            # 交互确认后卸载
cc-pipeline uninstall --yes      # 跳过确认直接卸载
```

卸载会：移除 pip 安装的 `cc-pipeline` 包、清理 `/tmp/cc-pipeline-worktrees` 与 `~/.cc-pipeline/runs`。
**不会触碰**你的项目仓库和已存在的 worktree。

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
    variables:
      line_threshold: 80
      branch_threshold: 70
```

运行：

```bash
cc-pipeline run modules.yaml
```

输出：

```
🌙 cc-pipeline 0.3.0
   run_id=2026-07-01T23-00-00  concurrency=3  model=auto (CC default)
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
| `model` | string | `""` | 全局默认模型（空 = CC 自己决定） |
| `worktree_root` | string | `""` | worktree 根目录（相对路径相对于 `repo`，绝对路径原样使用） |
| `pr_labels` | list | `[]` | 创建 PR 时附加的标签 |
| `pr_title_template` | string | `""` | PR 标题模板 |

### Module 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 模块名（唯一标识，仅允许字母、数字、下划线、连字符） |
| `spec_id` | string | 规格编号（注入 prompt） |
| `source_dir` | string | 源码目录 |
| `source_files` | list | 被测文件列表，元素可以是**字符串**或 **dict**（见下） |
| `variables` | dict | 自定义变量（注入 prompt；覆盖率阈值也写在这里） |

> **注意**：旧版的 `coverage:` 字段已删除。覆盖率阈值等内容现在统一写在 `variables:` 里。
> YAML 中仍然可以写 `coverage:` —— 迁移层会自动将其内容并入 `variables:`，并发出一条 deprecated 警告。

### source_files 的两种写法

**纯字符串**（向后兼容）：

```yaml
source_files: [auth_login.c, auth_token.c]
```

**dict 格式**（新）——为每个文件附加任意变量：

```yaml
source_files:
  - path: auth_login.c          # path 必填，映射到 {file} 变量
    assert_macro: CHECK          # 其余 key 任意命名，全部展开为变量
    spec_id: SPEC-001            # 例如可在 prompt 中用 {assert_macro}、{spec_id}
  - path: auth_token.c
    assert_macro: REQUIRE
```

dict 中除 `path` 外的**所有 key 都展开为变量**，名字随意起。`path` 的值会被注入为 `{file}`。
dict 内的变量会与 module 级变量合并（dict 优先级更高，可覆盖 module 级同名变量）。

### 完整示例

```yaml
repo: /home/user/my-project
base_branch: develop
concurrency: 5
max_retries: 3
output_branch_prefix: ut-nightly
model: glm-4.6                  # 全局默认模型（留空则 CC 自己决定）
worktree_root: ../worktrees     # worktree 创建在 repo 的上级目录 ../worktrees
pr_labels: [auto-generated, unit-test]
pr_title_template: "[UT] {module}"

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
    prompt_file: prompts/generate.md   # 从外部 .md 文件加载 prompt
    output: generate.json
    timeout: 900                        # 按步骤覆盖超时（秒）
    model: glm-4.6                      # 按步骤覆盖模型
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold}"
    retry: 3
    on_failure: scaffold                # 失败后回跳到 scaffold（不回滚）
    on_failure_max_jumps: 3
    depends_on: scaffold

  - id: verify
    executor: shell
    command: "gcov src/{module}/*.c && lcov --summary -o .pipeline/generate.verified.json"
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
    variables:
      line_threshold: 80
      branch_threshold: 70
      mock_strategy: link-time

  - name: payment
    spec_id: SPEC-2026-002
    source_dir: src/payment/
    source_files: [payment_process.c]
    variables:
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
| `command` | string | `""` | shell executor 的命令（替代 `prompt`） |
| `prompt_file` | string | `null` | 从外部 `.md` 文件加载 prompt |
| `model` | string | `""` | 按步骤指定模型（空 = 用全局/CC 默认，覆盖全局 `model`） |
| `loop` | string | `null` | `per_file` = 逐文件串行 |
| `retry` | int | 全局 `max_retries` | 该步最大重试次数 |
| `depends_on` | string | `null` | 前置步骤 ID |
| `postcondition` | dict | `null` | 通过条件 |
| `output` | string | `null` | CC 产出状态文件名（写入 `.pipeline/{output}`） |
| `timeout` | int | `null` | 按步骤超时（秒） |
| `on_failure` | string | `null` | 失败后跳转的 step_id（不回滚） |
| `on_failure_max_jumps` | int | `2` | `on_failure` 最大跳转次数 |
| `skill` | string | `null` | ⚠️ **未实现**（声明会被忽略并警告） |
| `rollback` | string | `git-checkpoint` | 回滚方式 |

### prompt 解析优先级

- **shell executor**：`command` > `prompt` > `prompt_file`
- **claude-code / judge executor**：`prompt` > `prompt_file`
- 当 `prompt`/`command` 均为空时，才会从 `prompt_file` 加载

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
  command: "gcov src/{module}/*.c && lcov --summary"
  postcondition:
    shell: "echo '{\"line\": 85, \"branch\": 72}'"
    expect: "$.line >= 80 && $.branch >= 70"
```

- 运行确定性命令（覆盖率、编译、lint）
- **`command` 就是 shell 命令本身**（也可用 `prompt`），不会被注入上下文或 output 指令
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

## 6. CC 间上下文传递

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
| `$.a >= 70 \|\| $.b >= 80` | OR（任一成立即通过） | 行覆盖 OR 分支覆盖达标 |
| `contains('text')` | stdout 包含文本 | `contains('passed')` |
| （省略） | 只要 shell 退出码 0 就通过 | — |

> `||` 与 `&&` 可组合：先按 `||` 拆成若干 OR 组，每组内部再用 `&&` 求与，**任一 OR 组成立**即整体通过。

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
| `{file}` | loop 当前文件（字符串或 dict 的 `path`） | `auth_login.c` |
| `{source_dir}` | modules.yaml → source_dir | `src/auth/` |
| `{spec_id}` | modules.yaml → spec_id（可被 dict 覆盖） | `SPEC-001` |
| `{line_threshold}` | variables → line_threshold | `80` |
| `{branch_threshold}` | variables → branch_threshold | `70` |
| `{custom_var}` | modules.yaml → variables / source_files dict 任意 key | `link-time` |
| `{.pipeline/xxx.json}` | 读取 JSON 文件内容注入 | `{"line": 85}` |

> 凡是写在 module 的 `variables:` 里、或 `source_files` dict 里的任意 key（除 `path`），都会成为可用变量。

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

## 9. Retry、回滚与 on_failure 回跳

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

### on_failure 回跳（不回滚）

`retry` 耗尽后，若设置了 `on_failure`，框架会**跳到目标 step 重新执行**，而不是回滚当前步重跑：

```yaml
- id: generate
  on_failure: scaffold          # generate 彻底失败 → 跳回 scaffold
  on_failure_max_jumps: 3       # 最多回跳 3 次（默认 2）
```

- **不回滚**：跳转时保留当前 worktree 状态，不执行 `git rollback`
- 跳转计数：每跳一次 `jump_count + 1`，达到 `on_failure_max_jumps` 后不再跳转，标记失败
- 跳转事件记入 transcript：`event=on_failure_jump`，含 `from / to / jump`

#### retry 与 on_failure 的区别

| 机制 | 是否回滚 | 目标 | 适用场景 |
|------|:------:|------|---------|
| `retry` | ✅ 回滚 | **同一步**重跑 | CC 偶发抽风、覆盖率差一点 |
| `on_failure` | ❌ 不回滚 | **跳到另一步** | 当前步是上一步的下游产物有问题，需要重做上游 |

---

## 10. CC 错误处理（CO 式分层）

cc-pipeline 实现了 claude-overnight 风格的 4 层 CC 错误处理策略：

### 错误分类

| 层 | 错误类型 | 行为 | 消耗 retry 预算？ |
|----|---------|------|:-:|
| 1 | **Rate limit (429/1302)** | 等待退避后重试 | ❌ 前 3 次免费 |
| 2 | **CC 崩溃 (returncode≠0)** | 跳过 postcondition，直接重试 | ✅ |
| 3 | **零工作检测** | CC 秒退无产出 → 直接重试 | ✅ |
| 4 | **Timeout** | 捕获 TimeoutExpired → 重试 | ✅ |

### Rate Limit 处理细节

```
常量：
  MAX_FREE_RATE_LIMIT_RETRIES = 3   # 免费重试次数
  RATE_LIMIT_BACKOFF_SECS = 30      # 每次退避等待秒数

流程：
  429 → sleep(30s) → 重试（不消耗预算）
  429 → sleep(30s) → 重试（不消耗预算）
  ... 重复最多 3 次 ...
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
MODEL=""                       # 留空 = CC 自己决定（可填 glm-4.6 强制指定）

# 添加到 crontab
crontab -e
# 每晚 23:00 运行
0 23 * * * /path/to/cc-pipeline/scripts/cron-template.sh
```

### 手动 crontab

```bash
# --model 留空则 CC 自己决定；想固定模型就显式传入
0 23 * * * cd /path/to/cc-pipeline && cc-pipeline run /path/to/modules.yaml --concurrency 5
```

### 模型优先级链

```
step.model  >  --model 参数  >  config.model  >  None（CC 自己决定）
```

都不指定时，框架传 `None` 给 CC，由 Claude Code 使用其默认模型。

### GLM API 并发限制

| 并发数 | 状态 |
|--------|------|
| ≤ 5 | ✅ 稳定 |
| 6-7 | ⚠️ 边界 |
| ≥ 8 | ❌ 429 限流 |

**建议 `--concurrency=5`。**

---

## 12. Daemon 模式（后台运行）

`--daemon` 让 pipeline fork 到后台运行，父进程立即退出，便于在服务器/Cron 中长跑。

### 启动

```bash
cc-pipeline run config.yaml --daemon
# → Daemon started. PID: 12345
#   PID file: /home/user/.cc-pipeline/runs/cc-pipeline.pid
#   Monitor:  cc-pipeline status --run-dir <dir>
#   Stop:     cc-pipeline stop --run-dir <dir>
```

- 父进程写 PID 文件（`{run_dir}/cc-pipeline.pid`）后退出
- 子进程 `setsid()` 脱离终端，stdout/stderr 重定向到 `{run_dir}/daemon.log`
- 正常结束时框架会清理 PID 文件

### 停止

```bash
cc-pipeline stop --run-dir <dir>            # SIGTERM，优雅退出（默认）
cc-pipeline stop --run-dir <dir> --force    # SIGKILL，强制结束
```

- `stop` 读取 PID 文件，向进程发信号
- **SIGTERM（默认）**：优雅退出。框架捕获信号后，会在当前 module 边界停止，state 实时落盘，便于后续 `resume`
- **`--force`**：直接 SIGKILL，不等待；用于进程卡死时
- 无论哪种方式，停止后都会清理 PID 文件

---

## 13. 崩溃恢复

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

### Resume（幂等恢复）

中断后用 `resume` 续跑，**已成功的 module / step 不会重复执行**：

```bash
cc-pipeline resume config.yaml --run-dir <dir>
```

恢复行为（**幂等**）：

- **module 级跳过**：读 `orchestrator-state.json`，状态为 `passed` 的 module 整体跳过
- **step 级跳过**：对未完成的 module，读取 git tag（`pipeline/{module}/{step}/*`）找出已完成的 step，只重跑剩余步骤
- **worktree 从 checkpoint 恢复**：worktree 不是从 `base_branch` 重建，而是从最近一次成功 checkpoint 的 ref 创建，保留已完成步的产物
- 失败 / error 的 module 会重新执行

```
  Skipping passed: ['auth', 'crypto']
  Resuming: ['payment']
```

### 手动检查失败的 worktree

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

## 14. 运行报告

`report` 命令基于 `orchestrator-state.json` + 各 module 的 `transcript.jsonl` 生成报告。

### Markdown 报告（默认）

```bash
cc-pipeline report --run-dir <dir>
# → 打印到 stdout，并写入 {run_dir}/report.md
```

内容包含：
- **汇总表**：modules 总数、passed/failed、成功率
- **模块详情**：每个 module 各 step 的状态、attempt 次数、reason、耗时
- **失败模块**：最后事件、失败原因、CC stdout 摘要

### HTML 报告

```bash
cc-pipeline report --run-dir <dir> --format html --config config.yaml
# → 写入 {run_dir}/report.html
```

- `--config` 用于绘制 DAG（不传则无 DAG）
- 在 Markdown 内容基础上额外提供：
  - **Mermaid DAG**：可视化 pipeline 步骤依赖（`per_file` 步骤会标注）
  - **折叠的 CC prompt**：可展开查看每步发给 CC 的实际 prompt

---

## 15. 日志与调试

### Transcript 日志

每个 module 的执行日志在 `{run_dir}/{module}/transcript.jsonl`：

```bash
# 查看某个 module 的执行历史
cat {run_dir}/auth/transcript.jsonl | python3 -m json.tool

# 关键事件类型：
# step_start         — 步骤开始执行
# pass               — 步骤通过
# fail               — 步骤失败
# retry              — 重试
# on_failure_jump    — on_failure 回跳（含 from/to/jump）
# resume_skip        — resume 时跳过的 step
# module_exception   — 异常（含完整 traceback）
```

### 异常日志

当 module 执行中发生异常时，完整 traceback 写入 transcript：

```json
{
  "event": "module_exception",
  "error": "FileNotFoundError: git not found",
  "traceback": "Traceback (most recent call last):\n  File ...\n...",
  "ts": "2026-07-01T23:05:32"
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

## 16. 异常处理

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

## 17. 常见问题

### Q: CC 生成的测试质量差怎么办？

**A:** 使用三层信任模型：
1. `shell` executor 做确定性覆盖率检查（可信）
2. `judge` executor 做 AI 质量评测（断言密度等）
3. postcondition 不通过 → 自动 retry；或用 `on_failure` 回跳到上游步重做

### Q: 多个 module 会不会互相影响？

**A:** 不会。每个 module 在独立的 git worktree 中执行，完全隔离。成功后自动清理，失败后保留 worktree 供分析。

### Q: 如何限制 CC 不能修改源码？

**A:** 在 claude-code executor 的 prompt 中明确约束："只修改 tests/ 目录，不要修改 src/ 目录"。框架层的 GitCheckpoint 确保即使 CC 改了源码，rollback 时也会恢复。

### Q: 如何支持非 UT 场景？

**A:** cc-pipeline 是通用 pipeline 框架。只要把 pipeline 步骤改为你的场景（代码审查、文档生成、重构等），modules 改为你的目标单元即可。

### Q: 超时怎么处理？

**A:** 每个 executor 都支持 timeout 参数，也可用 step 的 `timeout` 按步覆盖：
- CCExecutor: 默认 600 秒
- ShellExecutor: 默认 300 秒
- 超时后该步骤标记为 TIMEOUT，进入 retry 流程（消耗预算）

### Q: CC 一直返回 429 怎么办？

**A:** 框架自动处理：
- 前 3 次 429：等待 30 秒后免费重试（不消耗 retry 预算）
- 3 次后仍然 429：转为普通失败，开始消耗 retry 预算
- retry 预算耗尽（或触发 `on_failure`）：该步骤标记为 failed / 回跳

### Q: CC 间怎么传递数据？

**A:** 通过 `.pipeline/` 目录：
1. 设置 step 的 `output` 字段（如 `output: scaffold.json`）
2. CC 被要求将结果写入 `.pipeline/scaffold.json`
3. 下一步的 CC（claude-code/judge）自动收到前序所有 `.pipeline/*.json` 的内容

注意：shell executor 不注入上下文（它的 `command` 就是命令本身）。

### Q: 回滚后数据安全吗？

**A:** 回滚使用 `git reset --hard` + `git clean -fd --exclude=.pipeline/`，恢复到上一个成功步骤的最新 checkpoint。`.pipeline/` 目录被保留（`--exclude`）。

### Q: 模型怎么指定？

**A:** 按「`step.model` > `--model` > `config.model` > None」优先级。全部留空时由 Claude Code 用其默认模型；想全局固定就设 `config.model`，单步固定就设 `step.model`。

### Q: 后台跑挂了怎么办？

**A:** 用 `resume` 续跑（幂等，跳过已成功的 module/step，worktree 从 checkpoint 恢复）。用 `report` 生成报告定位失败原因。

---

## 附录

### CLI 命令速查

```bash
# 运行
cc-pipeline run config.yaml                        # 运行 pipeline
cc-pipeline run config.yaml --module auth          # 只跑一个 module
cc-pipeline run config.yaml --concurrency 3        # 指定并行度
cc-pipeline run config.yaml --daemon               # 后台运行
cc-pipeline run config.yaml --model glm-4.6        # 指定模型（默认 CC 自己决定）

# 恢复
cc-pipeline resume config.yaml --run-dir <dir>     # 幂等续跑中断的 run

# 后台进程
cc-pipeline stop --run-dir <dir>                   # 优雅停止（SIGTERM）
cc-pipeline stop --run-dir <dir> --force           # 强制停止（SIGKILL）

# 观测
cc-pipeline status                                 # 查看运行历史
cc-pipeline status --run-id <id>                   # 查看特定 run
cc-pipeline report --run-dir <dir>                 # Markdown 报告
cc-pipeline report --run-dir <dir> --format html --config <cfg>   # HTML 报告（含 DAG）

# 维护
cc-pipeline uninstall [--yes]                      # 卸载
cc-pipeline --version                              # 版本号
cc-pipeline --help                                 # 帮助
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
| v0.3 | 2026-07-04 | source_files dict 格式、coverage→variables 迁移、daemon 模式、resume 幂等恢复、HTML 报告（Mermaid DAG）、on_failure 回跳、uninstall、per-step model/timeout/command/prompt_file、GLM rate-limit 调优（3 次/30 秒）、expect OR 表达式 |
| v0.2 | 2026-07-01 | CC 上下文传递、CO 式错误处理、rate limit 保护、orchestrator 异常保护、rollback_to_latest |
| v0.1 | 2026-06-30 | 初始版本：Phase 1-4 开发完成，135 tests |
