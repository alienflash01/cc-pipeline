# cc-pipeline 用户指导文档

> 版本：v0.3.0 | 616 tests | 95% coverage | 更新日期：2026-07-06

---

## 目录

1. [安装](#1-安装)
2. [快速开始（四步上手）](#2-快速开始四步上手)
3. [配置文件详解](#3-配置文件详解)
4. [Pipeline DSL 语法](#4-pipeline-dsl-语法)
5. [三种 Executor 使用场景](#5-三种-executor-使用场景)
6. [CC 间上下文传递](#6-cc-间上下文传递)
7. [Postcondition 门控写法](#7-postcondition-门控写法)
8. [变量注入](#8-变量注入)
9. [Retry、回滚与 on_failure 回跳](#9retry回滚与-on_failure-回跳)
10. [CC 错误处理（CO 式分层）](#10-cc-错误处理co-式分层)
11. [运行时输出与 Preflight 检查](#11-运行时输出与-preflight-检查)
12. [init 命令（交互式生成配置）](#12-init-命令交互式生成配置)
13. [check 命令（环境与配置检查）](#13-check-命令环境与配置检查)
14. [定时运行（Cron）](#14-定时运行cron)
15. [Daemon 模式（后台运行）](#15-daemon-模式后台运行)
16. [崩溃恢复](#16-崩溃恢复)
17. [运行报告](#17-运行报告)
18. [日志与调试](#18-日志与调试)
19. [Transcript 命令（运行调试）](#19-transcript-命令运行调试)
20. [examples 示例目录](#20-examples-示例目录)
21. [异常处理](#21-异常处理)
22. [常见问题](#22-常见问题)

---

## 1. 安装

### 前置条件

- Python ≥ 3.10
- Git（支持 worktree）
- Claude Code CLI（`npm i -g @anthropic-ai/claude-code`）
- LLM API（如智谱 GLM、Anthropic Claude）

### 安装步骤

推荐用项目自带的一键脚本（自动检测 Python/Git/Claude Code/gh，处理 PEP 668）：

```bash
git clone git@github.com:alienflash01/cc-pipeline.git
cd cc-pipeline
./scripts/install.sh        # 一键检测 + 安装（--dev 顺带跑测试）
```

或手动安装：

```bash
git clone git@github.com:alienflash01/cc-pipeline.git
cd cc-pipeline
pip install -e ".[dev]"
```

### 验证

```bash
cc-pipeline --version
# → cc-pipeline 0.3.0

cc-pipeline check            # 环境自检（Python/Git/CC CLI/磁盘空间）
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

### ⚠️ 关于权限：CC 以 `--dangerously-skip-permissions` 运行

cc-pipeline 以 **headless 模式**调用 Claude Code，因此每次 CC 调用都带上
`--dangerously-skip-permissions` 参数——这是非交互运行的必需项。它的含义是：

- CC 在其所处的 **worktree 目录内**拥有**完整的文件读写与命令执行权限**，
  无需逐条确认即可执行 Bash、读写文件。

cc-pipeline 通过两层机制把这个「全权限」约束在隔离边界内，确保你的**主仓库源码安全**：

1. **git worktree 物理隔离**：每个模块在独立 worktree 中运行，CC 的所有改动
   都落在 worktree 里，**主仓库目录不会被直接触碰**。成功后清理，失败后保留供排查。
2. **git checkpoint 链 + 回滚**：每一步成功后 `commit + tag`，重试时回滚到
   「上一步最后一次成功」的已验证状态（`git reset --hard` + `git clean -fd --exclude=.pipeline/`），
   保留源码可恢复，同时保留 `.pipeline/` 运行上下文。

> **如何进一步收紧**：若你的环境对 CC 可执行的工具敏感，可自建 wrapper 限制
> `allowedTools`（例如 judge 步骤默认只允许 `["Read", "Bash"]`），或在 worktree
> 外层加沙箱（容器 / chroot）。但 worktree + checkpoint 已能在常规场景下保护源码。

---

## 2. 快速开始（四步上手）

推荐用四个命令从零跑通，全程不超过 5 分钟：

```bash
# 1) 交互式生成 config.yaml + prompts/（三种任务模板可选）
cc-pipeline init

# 2) 环境与配置自检（不调 CC，0 成本）
cc-pipeline check --config config.yaml

# 3) 配置预览：看清将要跑哪些步骤、哪些文件、估算多少次 CC 调用（不调 CC，0 成本）
cc-pipeline run config.yaml --dry-run

# 4) 正式运行
cc-pipeline run config.yaml
```

### 手写配置也行

如果不想用 `init`，直接创建 `modules.yaml`：

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

### 默认输出（不再静默）

**不加 `--verbose` 也有输出**。从你按下回车到第一个 module 完成，终端不会再死寂——
框架会先打一行启动横幅，再在每个 module 完成时打一行结果：

```
🌙 cc-pipeline 0.3.0
   concurrency=5  modules=['auth']

  ✅ auth     passed  (3 steps, 2 files)

============================================================
  ✓ auth                  passed
============================================================
  1 passed, 0 failed  (run_id: 2026-07-06T23-00-00)
```

- 启动横幅：版本 + 并发数 + 模块列表，**无条件打印**
- 模块进度行：`✅ <module> passed (N steps)` 或 `(N steps, M files)`，**非 verbose 也打**
- 收尾汇总：每个模块的 ✓/✗、失败原因、一键排查命令、`run_id`

想看每一步的实时细节（START/PASS/FAIL、rate-limit、retry、回跳），加 `-v`，详见
[§11 运行时输出与 Preflight 检查](#11-运行时输出与-preflight-检查)。

---

## 3. 配置文件详解

### 全局字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `repo` | string | **必填** | 被测仓库路径 |
| `base_branch` | string | `main` | worktree 基准分支 |
| `concurrency` | int | `5` | module 间并行数 |
| `max_retries` | int | `3` | 全局默认重试次数 |
| `output_branch_prefix` | string | `cc-auto` | worktree 分支前缀 |
| `model` | string | `""` | 全局默认模型（空 = CC 自己决定） |
| `worktree_root` | string | `""` | worktree 根目录（相对路径相对于 `repo`，绝对路径原样使用） |
| `prompt_prefix` | string | `""` | 全局公共上下文（自动拼接到每个 step 的 prompt 开头） |
| `snippets` | dict | `{}` | 命名文本块，通过 `{{snippet:name}}` 在 prompt 任意位置引用 |

> **变更**：`output_branch_prefix` 默认值已从 `ut-auto` 改为 `cc-auto`（cc-pipeline 是通用框架，不再特指 UT）。
> **变更**：`pr_labels` / `pr_title_template` 已删除（PR 功能移除，改为自动 merge worktree 分支到 base_branch）。
> **变更**：不配 `base_branch` 时自动检测 git 默认分支。

### Module 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 模块名（唯一标识，仅允许字母、数字、下划线、连字符） |
| `spec_id` | string | 规格编号（注入 prompt） |
| `source_dir` | string | 源码目录 |
| `source_files` | list | 被测文件列表，元素可以是**字符串**或 **dict**（见下） |
| `variables` | dict | 自定义变量（注入 prompt；覆盖率阈值也写在这里） |
| `file_order` | string | `per_file` 步骤的展开顺序：`batched`（默认）/ `sequential`（见下） |

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
output_branch_prefix: cc-nightly
model: glm-4.6                  # 全局默认模型（留空则 CC 自己决定）
worktree_root: ../worktrees     # worktree 创建在 repo 的上级目录 ../worktrees
prompt_prefix: |                # 全局公共上下文（所有 step 自动拼接）
  编译命令：make test
  断言宏：CHECK
snippets:                        # 命名文本块，prompt 中用 {{snippet:name}} 引用
  build: |
    使用 subagent 编译：cd {source_dir} && make test
  dtest: |
    断言宏：CHECK（dtest 框架）

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
| `output_prompt` | string | `null` | 自定义 output 注入文本（替代框架默认中文指令） |
| `timeout` | int | `null` | 按步骤超时（秒） |
| `on_failure` | string | `null` | 失败后跳转的 step_id（不回滚） |
| `on_failure_max_jumps` | int | `2` | `on_failure` 最大跳转次数 |
| `skill` | string | `null` | ⚠️ **未实现**（声明会被忽略并警告） |
| `rollback` | string | `git-checkpoint` | 回滚方式 |

> **提示**：`prompt_file` 指向不存在的文件时，加载期即报错（不再静默）。用
> `cc-pipeline check --config config.yaml` 可一次性校验所有 `prompt_file` 是否就位。

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

### file_order：per_file 展开顺序

module 级字段 `file_order` 控制当 pipeline 中存在多个 `per_file` 步骤时，文件与步骤的交叉顺序。默认 `batched`，可切换为 `sequential`。

| 取值 | 展开方式 | 适用场景 |
|------|---------|---------|
| `batched`（默认） | 所有文件先过 stepA，再统一过 stepB | 各步骤彼此独立、可批量处理 |
| `sequential` | 每个文件走完**完整 pipeline**（stepA→stepB）后，再处理下一个文件 | 文件间相互独立、想尽早拿到单文件完整结果 |

假设 pipeline 是 `scaffold → generate(per_file) → evaluate`，`source_files: [a.c, b.c, c.c]`：

```
batched（默认）：
  scaffold → generate[a] → generate[b] → generate[c] → evaluate

sequential：
  scaffold → generate[a] → evaluate → generate[b] → evaluate → generate[c] → evaluate
```

> 注：`sequential` 要求下游步骤（如 evaluate）也能在「单文件尚未齐全」的状态下运行；若 evaluate 依赖全部文件产出，请保持默认 `batched`。

```yaml
modules:
  - name: auth
    source_files: [a.c, b.c, c.c]
    file_order: sequential
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

### output_prompt 字段（自定义注入文本）

默认情况下，框架在 prompt 尾部注入一段固定的中文指令，要求 CC 把关键信息以 JSON 格式写入 `.pipeline/{output}`：

```
请将本次执行的关键信息...以 JSON 格式写入 .pipeline/{output}
```

如果这段默认指令不符合需求（例如想换语言、换格式、换措辞），用 `output_prompt` 自定义：

```yaml
- id: analyze
  executor: claude-code
  prompt: '分析 {module}，结果写到 {output}'
  output: analyze.json
  output_prompt: '将分析结果以 JSON 格式写入 .pipeline/{output}'
```

- `output_prompt` 中的 `{output}` 同样会被替换成 `output` 字段的实际值
- 留空（默认 `null`）时使用框架内置的默认中文指令

### {output} 变量

`output` 字段的值可以作为 `{output}` 变量在 `prompt` 中直接引用：

```yaml
- id: collect
  executor: claude-code
  prompt: '将结果写到 {output}'
  output: result.json
  # CC 收到的 prompt: '将结果写到 result.json'
```

这样无需在 prompt 里硬编码文件名，只改 `output` 一处即可。

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

### expect 值的形式（true / false / contains / JSON 表达式）

`expect` 不写 JSONPath 时，按值的字面形式匹配 shell 退出码或 stdout。四种形式：

| `expect` 值 | 通过条件 | 典型用途 |
|-------------|---------|---------|
| `true` | shell **退出码为 0** 即通过 | 「命令成功执行」类断言 |
| `false` | shell **退出码非 0** 才通过（即期望命令失败） | 「确认某坏路径确实会报错」类反向断言 |
| `contains('text')` | **stdout 包含** `text` 即通过 | 解析命令输出找关键词 |
| `$.field …`（JSON 表达式） | **stdout 必须是合法 JSON**，再按表达式求值 | 解析 JSON 结果做数值/逻辑判断 |

```yaml
# 期望命令成功（最常见）
postcondition:
  shell: "make build"
  expect: "true"

# 期望命令失败（反向断言：坏输入应被拒绝）
postcondition:
  shell: "./run_with bad_input"
  expect: "false"

# stdout 关键词
postcondition:
  shell: "make test 2>&1 | tail -1"
  expect: "contains('ALL PASSED')"

# JSON 表达式：stdout 须为合法 JSON
postcondition:
  shell: "gcovr --json-coverage -"
  expect: "$.line_rate >= 0.8"
```

> **修复说明（v0.3.0）**：此前 `expect: "true"` / `expect: "false"` 会被误当作 JSON 解析而报错。
> 现已改为**字面匹配**——`true`/`false` 直接对应「期望退出码 0 / 非 0」，不再尝试 JSON 解析。
> 含 `$.` 的表达式才走 JSON 解析路径，此时 stdout 必须是合法 JSON。

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

> **注意**：postcondition 的 `shell` 命令在加载期**不校验工具是否存在**。如果你写了
> `check_coverage.sh {module}` 但本地没装这个脚本，要跑到运行期才会 `command not found`。
> 建议先用 `cc-pipeline run config.yaml --dry-run` 把配置编译一遍，确认步骤链能跑通。

### postcondition 完整配置示例

#### 场景 1: 文件存在检查

```yaml
postcondition:
  shell: "test -f .pipeline/analyze.json"
  expect: "true"
```

#### 场景 2: 测试通过检查

```yaml
postcondition:
  shell: "make test 2>&1 | tail -1"
  expect: "contains('passed')"
```

#### 场景 3: 覆盖率门槛（>= 80%）

```yaml
postcondition:
  shell: "gcovr --json-coverage - | python3 -c \"import sys,json; d=json.load(sys.stdin); exit(0 if d['line_rate'] >= 0.8 else 1)\""
  expect: "true"
```

#### 场景 4: CC JSON 评分 >= 90

```yaml
- id: evaluate
  executor: claude-code
  prompt_file: prompts/evaluate.md
  output: evaluate.json
  postcondition:
    shell: "python3 -c \"import json; d=json.load(open('.pipeline/evaluate.json')); exit(0 if d.get('score',0) >= 90 else 1)\""
    expect: "true"
  on_failure: generate
```

CC 把评分写入 `.pipeline/evaluate.json`，postcondition 读 JSON 判断 score >= 90。

> **FAQ: 如何让评估器打分超过 90 分才算通过？**
>
> 在 evaluate 步骤配 postcondition（场景 4）。CC 把评分写入 JSON 文件，
> postcondition 的 shell 命令读 JSON 提取 score，判断是否 >= 90。
> 不达标则触发 `on_failure: generate` 回跳重新生成。

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
| `{output}` | step.output 字段 | `result.json` |
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

实时带时间戳的版本见 [§11 运行时输出](#11-运行时输出与-preflight-检查)。

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
常量（runner.py）：
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

## 11. 运行时输出与 Preflight 检查

这一章解释 `cc-pipeline run` 在运行时会打印什么、怎么排查失败。

### 11.1 启动前：Preflight 检查（只 warn 不停）

正式跑之前，框架会自动做一次环境预检，覆盖：

- Claude Code CLI 是否安装（`which claude`）
- `repo` 目录是否存在
- `repo` 是否是 git 仓库（有 `.git`）
- `base_branch` 在 repo 中是否存在
- 若配置了 `worktree_root`，其父目录是否存在

**检测到问题只打印警告到 stderr，绝不停止运行**：

```
⚠️  Preflight warning:
  • Branch develop not found in repo
  • worktree_root parent directory not found: /nonexistent
```

> 这是 advisory 提醒。preflight 不阻断执行，方便你「先看到再决定」。

### 11.2 默认输出（不加 `--verbose`）

如 [§2](#2-快速开始四步上手) 所述，默认模式已不再全程沉默。运行期间你会看到：

```
🌙 cc-pipeline 0.3.0
   concurrency=5  modules=['auth', 'payment']

  ✅ auth     passed  (3 steps, 2 files)
  ✗ payment   failed — evaluate: score=45 < 60 (3 retries)
```

- **启动横幅**：`🌙 cc-pipeline <version>` + 并发数 + 模块列表，无条件打印
- **模块进度行**：每个 module 完成时打印一行 `✅/✗ <module> <status> (N steps[, M files])`

### 11.3 详细输出（`-v` / `--verbose`）

加 `-v` 后，在模块进度行之外，**额外实时打印每一步的带时间戳事件**，便于观察耗时与卡点：

```bash
cc-pipeline run config.yaml -v
```

输出示例（关键事件）：

```
  verbose mode ON — printing step progress
  [20:15:03] [auth] scaffold      START
  [20:15:48] [auth] scaffold      PASS
  [20:15:10] [auth] generate      ⏳ RATE LIMIT (retry 1/3)
  [20:15:42] [auth] generate      ⚠️  RETRY (attempt 2) — Postcondition failed
  [20:16:11] [auth] ↩️  JUMP: evaluate → generate (jump 1)
  [20:16:20] [auth] evaluate      ❌ FAIL — score=45 < 60
```

| 事件 | 含义 |
|------|------|
| `START [file]` | 步骤开始（`per_file` 步骤带当前文件） |
| `PASS [file]` | 步骤通过 |
| `⏳ RATE LIMIT (retry N/3)` | 触发限流，免费退避重试（不耗预算） |
| `⚠️  RETRY (attempt N) — <原因>` | 消耗预算的重试 |
| `↩️  JUMP: <from> → <to> (jump N)` | `on_failure` 回跳 |
| `❌ FAIL — <原因>` | 步骤彻底失败 |

> 时间戳格式为 `[HH:MM:SS]`，前缀为 `[<module>]`。Cron / daemon 长跑时，长输出也会落盘到 `{run_dir}/daemon.log`，可用 `tail -f` 跟踪。

### 11.4 收尾汇总：失败原因 + 排查命令

运行结束后打印汇总。**每个失败模块会附上失败原因和一键排查命令**：

```
============================================================
  ✓ auth                  passed
  ✗ payment               failed — evaluate: score=45 < 60 (3 retries)
     💡 cc-pipeline transcript --run-dir /root/tmp-1 --module payment
============================================================
  1 passed, 1 failed  (run_id: 2026-07-06T23-00-00)
```

- 失败原因直接显示在汇总行（哪一步、什么分数、重试了几次）
- `💡` 行复制即可查看该模块的完整执行记录（CC 收到的 prompt、返回了什么、为什么 FAIL）

### 11.5 配置预览（`--dry-run`）

正式运行前用 `--dry-run` 预览，**不调 CC、不创建 worktree、0 成本**：

```bash
cc-pipeline run config.yaml --dry-run
```

输出包含：

1. **步骤列表**（`per_file` 步骤会标注）
2. **每个模块的文件表格**（纯字符串列表 → 单列；dict → 每个 key 一列）
3. **估算 CC 调用次数**（非循环步 = 1 次/模块；`per_file` 步 = 文件数/模块）
4. **全局变量**

```
📊 Pipeline Preview (dry-run)
══════════════════════════════════════════════════

  Steps: scaffold → generate(per_file) → evaluate

  Module: auth (2 files)
  ┌───────────────┬──────────────┐
  │ File          │ assert_macro │
  ├───────────────┼──────────────┤
  │ auth_login.c  │ CHECK        │
  │ auth_token.c  │ REQUIRE      │
  └───────────────┴──────────────┘

  Estimated: 5 CC calls
  (scaffold=1 + generate=2 + evaluate=2)

  Variables:
    repo=/home/user/my-project
    base_branch=main
    concurrency=5
    ...

  ✅ Config valid. Run without --dry-run to execute.
```

> dry-run 还兼任「配置编译器」：任何一个模块编译失败（变量缺失、prompt 解析错等）都会在这里报错，跑之前就把问题挡住。

---

## 12. init 命令（交互式生成配置）

`init` 是降门槛利器——用一段交互问答，生成可直接运行的 `config.yaml` + `prompts/` 目录，免去手写 YAML。

### 基本用法

```bash
cc-pipeline init
# → 🧩 cc-pipeline 配置生成器
#   项目路径 repo（默认 '.'）:
#   任务类型 1=UT生成 2=代码审查 3=自定义: 1
#   source_dir（默认 "src/"）:
#   模块列表逗号分隔（默认 "auth"）:
#   assert_macro（默认 "CHECK"）:
#   concurrency（默认 "5"）:
```

### 三种任务模板

| 任务类型 | 生成的 pipeline | 生成的 prompts/ |
|---------|----------------|----------------|
| `1` = UT 生成 | `scaffold → generate(per_file) → evaluate`，含 `on_failure` 回跳 | `scaffold.md` / `generate.md` / `evaluate.md` |
| `2` = 代码审查 | 单个 `review` 步骤 | `review.md` |
| `3` = 自定义 | 单个 `step1` 步骤 | `step1.md` |

生成完成后会列出所有文件，并提示下一步：

```
✅ 生成完成
生成的文件：
  /path/config.yaml
  /path/prompts/scaffold.md
  /path/prompts/generate.md
  /path/prompts/evaluate.md
运行: cc-pipeline run config.yaml --dry-run
```

> **技巧**：`init` 用 `str.replace` 而非 `str.format` 替换占位符，因此 prompt 里的字面
> `{module}` / `{file}` 变量会被原样保留，不会在生成期被吃掉。

### 命令参数

| 参数 | 说明 |
|------|------|
| `--output-dir <dir>` | 生成文件的目录（默认 `.`，当前目录） |
| `--template <name>` | ⚠️ **未实现**（声明会打印提示并忽略，仍走默认交互流程） |

---

## 13. check 命令（环境与配置检查）

`check` 是排查前置关：一次性把环境依赖和配置正确性过一遍，**永远返回 0（advisory）**，不会因为某项失败就阻断你。

### 基本用法

```bash
cc-pipeline check                      # 只查环境
cc-pipeline check --config config.yaml # 环境 + 配置双重检查
```

### 检查项

**环境探测（总是运行）：**

| 检查项 | 说明 |
|--------|------|
| Python 版本 | 当前解释器版本 |
| Git | `git` 是否可用 |
| Claude Code CLI | `claude` 是否可用 |
| Git user.name | 是否已设置（创建 commit 需要） |
| Disk space | 当前盘剩余空间（>1GB 为 ✅） |

**配置探测（带 `--config` 时额外运行）：**

| 检查项 | 说明 |
|--------|------|
| Config load | YAML 能否加载（必填字段、executor 拼写等） |
| Repo exists | `repo` 目录是否存在 |
| base_branch exists | `base_branch` 在 repo 中是否存在 |
| prompt_files present | 所有 `prompt_file` 是否就位 |
| Dry-run preview | pipeline 能否编译（变量解析、步骤链） |

### 输出示例

```
🔍 cc-pipeline Environment Check

  Python 3.11.0: ✅
  Git: ✅ /usr/bin/git
  Claude Code CLI: ✅ /usr/local/bin/claude
  Git user.name: ✅ John Doe
  Disk space: ✅ 50.2 GB free
  Config load: ✅ valid
  Repo exists: ✅ /home/user/my-project
  base_branch exists: ✅ main
  prompt_files present: ✅ all found
  Dry-run preview: ✅ compiles

  Summary: 10/10 checks passed
```

> 跑之前先 `check --config`，能把绝大多数「跑到一半才报错」的问题（repo 路径错、分支名错、prompt 文件缺、配置编译失败）挡在运行前。

---

## 14. 定时运行（Cron）

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

## 15. Daemon 模式（后台运行）

`--daemon` 让 pipeline fork 到后台运行，父进程立即退出，便于在服务器/Cron 中长跑。

> **平台**：daemon 模式基于 `os.fork()`，**仅限 Unix-like 环境**（Linux / macOS / WSL）。原生 Windows 不可用。

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

- `stop` 读取 PID 文件，向进程发信号，然后**轮询最多 30 秒确认进程是否已退出**
- **SIGTERM（默认）**：发信号后等待。框架会在合适的时机停止——注意正在进行的 CC 调用不会被立即打断，通常是**当前 step 完成后**再退出（不是立即在 module 边界）。state 实时落盘，便于后续 `resume`
- **`--force`**：直接 SIGKILL，不等待；用于进程卡死时
- **停止语义可信**：若 30 秒后进程仍存活（例如卡在一个长 CC 调用里），`stop` 会老实告诉你并保留 PID 文件：
  ```
  Sending SIGTERM to PID 12345...
  Process 12345 still running after 30s.
    Try: cc-pipeline stop --run-dir <dir> --force
  ```
  不会误报「已停止」，也不会提前删 PID 文件。

### 信号处理（Ctrl+C / SIGTERM）

无论前台 `run` 还是后台 daemon，收到 **Ctrl+C（SIGINT）或 SIGTERM** 时都会走**优雅退出**流程，不会留下孤儿 CC 进程：

1. **设置优雅退出标志**：通知 orchestrator 停止派发新任务
2. **kill 所有正在运行的 CC 子进程**：通过 `pkill -f claude.*-p` 终止当前在跑的 Claude Code 调用（否则 CC 子进程会脱离父进程继续占用 token）
3. **完成当前模块后停止**：已在执行的 module 跑到自然边界后收尾，state 实时落盘

```
^C
🛑 收到中断信号，正在优雅退出...
   - 已 kill N 个 CC 子进程
   - 当前模块完成后停止（state 已落盘，可用 resume 续跑）
```

- **state 不丢**：退出时 `orchestrator-state.json` 已是最新，直接 `cc-pipeline resume config.yaml --run-dir <dir>` 即可幂等续跑
- **CC 不会变孤儿**：第 2 步的 `pkill` 确保即使父进程先退，CC 子进程也被一并清掉，避免后台继续烧 token
- 强制立即退出用 `kill -9`（SIGKILL），但此时可能留下正在运行的 CC 子进程，不推荐

---

## 16. 崩溃恢复

### 运行中崩溃

cc-pipeline 在 `{run_dir}/orchestrator-state.json` 持久化状态。每个 module 的状态实时更新（线程安全）：

```json
{
  "run_id": "2026-07-06T23-00-00",
  "saved_at": "2026-07-06T23-05:32",
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

cc-pipeline status --run-id 2026-07-06T23-00-00
# 显示某个 run 的各 module 状态
```

### Resume（幂等恢复）

中断后用 `resume` 续跑，**已成功的 module / step 不会重复执行**：

```bash
cc-pipeline resume config.yaml --run-dir <dir>
cc-pipeline resume config.yaml --run-dir <dir> -v   # 详细输出（每步带时间戳）
```

`resume` 与 `run` 一样支持 `--verbose` / `-v`：续跑时实时打印每个 step 的带时间戳事件（START / PASS / FAIL / RETRY / JUMP），方便确认「到底从哪一步接着跑、哪些被跳过了」。

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

## 17. 运行报告

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

## 18. 日志与调试

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
  "ts": "2026-07-06T23-05:32"
}
```

### 调试模式

```bash
# 配置预览（不调 CC，0 成本，挡住编译期问题）
cc-pipeline run config.yaml --dry-run

# 单 module 运行（便于调试）
cc-pipeline run config.yaml --module auth

# 详细输出（每步带时间戳）
cc-pipeline run config.yaml -v

# 低并发（避免限流）
cc-pipeline run config.yaml --concurrency 1
```

---

## 19. Transcript 命令（运行调试）

`transcript` 命令把 `{run_dir}/{module}/transcript.jsonl` 渲染成人类可读的执行记录，便于排查 CC 实际收到了什么 prompt、返回了什么、为什么 PASS / FAIL / RETRY。

### 基本用法

```bash
# 查看所有模块的完整执行记录
cc-pipeline transcript --run-dir /data/runs/job1

# 只看某个模块
cc-pipeline transcript --run-dir /data/runs/job1 --module auth
```

### 输出格式说明

| 段落 | 含义 |
|------|------|
| 步骤头部 | 时间戳 + step + attempt + loop_file |
| `[PROMPT]` | 完整 CC prompt（逐行显示） |
| CC RESULT | returncode + stdout（前 15 行）+ stderr（前 15 行） |
| PASS / FAIL / RETRY | 状态 + 原因 |
| JUMP BACK | `on_failure` 回跳记录（含 from / to） |

> 与 `report` 的区别：`report` 生成汇总报告（成功率、模块详情）；`transcript` 则逐事件还原 CC 的真实输入输出，是定位「CC 这次到底干了什么」的首选工具。
> 失败模块的收尾汇总里会直接给出对应的 `transcript` 命令，复制即可运行。

---

## 20. examples 示例目录

仓库自带两套开箱即用的示例，分别在「无 CC」和「真跑 CC」两端：

```
examples/
├── simple.yaml              # 最小示例：单 module 单步 pipeline（repo: .，clone 即跑）
├── quickstart-shell/        # 0 门槛：纯 shell executor，不需要 CC / API key
│   ├── config.yaml
│   └── run.sh
└── quickstart-cc/           # 完整 CC 编排示例（自包含）
    ├── config.yaml
    ├── run.sh
    ├── prompts/             # scaffold.md / generate.md / evaluate.md
    └── src/                 # math_utils.py / string_utils.py（被测代码）
```

### quickstart-shell（5 分钟体验入口）

- **0 依赖、0 API key、0 成本**：全程用 `shell` executor，不调用 Claude Code
- 演示完整的三步流水线 + postcondition + depends_on 机制
- 任何人 clone 后 `cd examples/quickstart-shell && ./run.sh` 就能跑通
- 最理想的「先看效果」入口，也是推广给同事的首选演示

### quickstart-cc（真跑一次 CC）

- 自包含：含 `prompts/*.md`（prompt 卫生良好，带反踩坑指令）+ `src/*.py`（被测代码）
- 演示 `scaffold → generate(per_file) → evaluate` 的完整 CC 编排
- 需要配置好 Claude Code（`~/.claude/settings.json` 的 token / base_url）
- 是「真跑一次 CC、看 trust 分层怎么工作」的最短路径

### simple.yaml

最小示例，`repo: .` 指向当前目录，clone 后立即可用，适合快速验证安装是否正常。

---

## 21. 异常处理

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

## 22. 常见问题

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

**A:** cc-pipeline 是通用 pipeline 框架。`cc-pipeline init` 提供「代码审查」「自定义」模板；或直接把 pipeline 步骤改为你的场景（文档生成、重构等），modules 改为你的目标单元即可。

### Q: 超时怎么处理？

**A:** 每个 executor 都支持 timeout 参数，也可用 step 的 `timeout` 按步覆盖：
- CCExecutor: 默认 600 秒
- ShellExecutor: 默认 300 秒
- 超时后该步骤标记为 TIMEOUT，进入 retry 流程（消耗预算）

### Q: shell 命令失败时能看到详细原因吗？

**A:** 可以。从 v0.3.0 起，shell executor 失败不再是黑盒——失败原因（`reason`）会包含 **exit code + stderr/stdout 最后 5 行**，直接显示在 verbose 进度行、收尾汇总和 transcript 里。

例如 `postcondition` 的 shell 返回非 0 时，你会看到类似：

```
[20:15:42] [auth] generate      ⚠️  RETRY (attempt 2) — shell exit=2 (stderr: make: *** No rule ... )
```

- **exit code**：命令的退出码
- **stderr / stdout 最后 5 行**：截取末尾 5 行，定位「卡在哪一句」
- **verbose 模式**（`-v`）下还会在终端**实时打印** shell 的 stdout/stderr，长跑时方便 `tail -f` 跟踪

> 想看完整输出而非摘要，用 `cc-pipeline transcript --run-dir <dir> --module <m>`，里面记录了 shell 的完整 stdout/stderr。

### Q: CC 一直返回 429 怎么办？

**A:** 框架自动处理（前 **3** 次免费、每次退避 **30 秒**）：
- 前 3 次 429：等待 30 秒后免费重试（不消耗 retry 预算）
- 3 次后仍然 429：转为普通失败，开始消耗 retry 预算
- retry 预算耗尽（或触发 `on_failure`）：该步骤标记为 failed / 回跳

### Q: CC 间怎么传递数据？

**A:** 通过 `.pipeline/` 目录：
1. 设置 step 的 `output` 字段（如 `output: scaffold.json`）
2. CC 被要求将结果写入 `.pipeline/scaffold.json`
3. 下一步的 CC（claude-code/judge）自动收到前序所有 `.pipeline/*.json` 的内容

注意：shell executor 不注入上下文（它的 `command` 就是命令本身）。

### Q: prompt 里有 C 代码的花括号 `{ return 0; }`，会被误解析吗？

**A:** 不会。框架只对「看起来像变量名」的花括号内容（如 `{config}`）发出警告并尝试替换。C 代码中的 `{ return 0; }`、`{ error_path; }` 等含空格、分号、特殊字符的花括号会**原样保留**，不替换也不警告。

如果确实需要字面花括号（既不替换也不警告），用 `{{ }}` 转义。

### Q: 回滚后数据安全吗？

**A:** 回滚使用 `git reset --hard` + `git clean -fd --exclude=.pipeline/`，恢复到上一个成功步骤的最新 checkpoint。`.pipeline/` 目录被保留（`--exclude`）。注意 worktree 内**其他未跟踪文件**会被 `git clean` 清掉。

### Q: 模型怎么指定？

**A:** 按「`step.model` > `--model` > `config.model` > None」优先级。全部留空时由 Claude Code 用其默认模型；想全局固定就设 `config.model`，单步固定就设 `step.model`。

### Q: 后台跑挂了怎么办？

**A:** 用 `resume` 续跑（幂等，跳过已成功的 module/step，worktree 从 checkpoint 恢复）。用 `report` 生成报告定位失败原因。

### Q: 为什么跑起来终端一开始没反应？

**A:** 不会了。当前版本（v0.3.0）起，启动横幅（`🌙 cc-pipeline <version>` + 并发数 + 模块列表）会**无条件打印**，每个 module 完成时也会打一行 `✅/✗`。如果想要每一步的实时细节，加 `-v`。

---

## 附录

### CLI 命令速查

```bash
# —— 运行 ——
cc-pipeline run config.yaml                        # 运行 pipeline
cc-pipeline run config.yaml --module auth          # 只跑一个 module
cc-pipeline run config.yaml --concurrency 3        # 指定并行度
cc-pipeline run config.yaml --model glm-4.6        # 指定模型（默认 CC 自己决定）
cc-pipeline run config.yaml -v / --verbose         # 详细输出（每步带时间戳）
cc-pipeline run config.yaml --dry-run              # 配置预览，不调 CC、0 成本
cc-pipeline run config.yaml --daemon               # 后台运行（仅 Unix）

# —— 恢复 ——
cc-pipeline resume config.yaml --run-dir <dir>     # 幂等续跑中断的 run
cc-pipeline resume config.yaml --run-dir <dir> -v  # 续跑时详细输出（每步带时间戳）

# —— 后台进程 ——
cc-pipeline stop --run-dir <dir>                   # 优雅停止（SIGTERM）
cc-pipeline stop --run-dir <dir> --force           # 强制停止（SIGKILL）

# —— 观测 ——
cc-pipeline status                                 # 查看运行历史
cc-pipeline status --run-id <id>                   # 查看特定 run
cc-pipeline status --run-dir <dir>                 # 查看特定 run 目录
cc-pipeline report --run-dir <dir>                 # Markdown 报告
cc-pipeline report --run-dir <dir> --format html --config <cfg>   # HTML 报告（含 DAG）
cc-pipeline transcript --run-dir <dir>             # 查看完整执行记录（所有模块）
cc-pipeline transcript --run-dir <dir> --module X  # 只看某个模块

# —— 上手 ——
cc-pipeline init                                   # 交互式生成 config.yaml + prompts/
cc-pipeline init --output-dir <dir>                # 指定生成目录
cc-pipeline check                                  # 环境自检
cc-pipeline check --config config.yaml             # 环境 + 配置双重检查

# —— 维护 ——
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
| [UX-AUDIT.md](UX-AUDIT.md) | 用户体验审计报告 |
| [CONSISTENCY-REPORT.md](CONSISTENCY-REPORT.md) | 实现 vs 设计一致性 |

### 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v0.3.0 | 2026-07-06 | UX 审计修复：默认输出不再静默（启动横幅 + 模块进度行无条件打印）、`run --dry-run` 配置预览、preflight 运行前检查（只 warn 不停）、`stop` 停止语义可信（30s 后复查存活、不再误报 / 误删 PID）、`output_branch_prefix` 默认值改为 `cc-auto`、init/check 命令补入文档与速查表、examples/ 双示例（quickstart-shell 无 CC 入口 + quickstart-cc 完整编排）；`file_order: batched\|sequential` 控制 per_file 展开顺序、postcondition `expect: true/false` 字面匹配（不再误解析为 JSON）、shell executor 失败详情（exit code + stderr/stdout 末 5 行 + verbose 实时打印）、Ctrl+C/SIGTERM 优雅退出（kill CC 子进程 + state 落盘 + 可 resume）、`resume --verbose/-v` 续跑详细输出 |
| v0.3 | 2026-07-04 | source_files dict 格式、coverage→variables 迁移、daemon 模式、resume 幂等恢复、HTML 报告（Mermaid DAG）、on_failure 回跳、uninstall、per-step model/timeout/command/prompt_file、GLM rate-limit 调优（3 次/30 秒）、expect OR 表达式、`{output}` 变量与 `output_prompt` 自定义注入文本、`transcript` 命令、verbose 带时间戳、C 代码花括号免误报、prompt 完整记录 |
| v0.2 | 2026-07-01 | CC 上下文传递、CO 式错误处理、rate limit 保护、orchestrator 异常保护、rollback_to_latest |
| v0.1 | 2026-06-30 | 初始版本：Phase 1-4 开发完成，135 tests |
