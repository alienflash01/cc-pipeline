# cc-pipeline 详细设计方案

> **⚠️ 本文档为 v0.1 历史设计，部分字段已删除（skill/rollback/on_complete/command）。以 USER-GUIDE.md 为准。**
>
> 版本：v0.1 | 日期：2026-06-29

---

## 一、问题定义

### 场景

C 嵌入式工程，每晚定时为多个模块自动生成单元测试。每个模块需要经过"脚手架生成 → 逐文件测试用例生成 → 质量评测 → 规范检查 → 合并提 PR"的多阶段流程。

### 核心需求

1. **module 间并行**：N 个模块同时处理
2. **module 内串行**：每个模块内部的多步骤按顺序执行，步骤间可传递上下文
3. **CC 间交互**：前一个 Claude Code 的产出物能传递给下一个 CC
4. **质量门控**：每步可设定通过条件，不通过可重试
5. **git 原生状态管理**：用 git checkpoint 管理每步状态，支持精确回滚
6. **通用性**：不限于 UT，pipeline 定义可迁移到其他场景

---

## 二、架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                     cc-pipeline 编排器                         │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ Config Loader│  │ Pipeline    │  │  Scheduler   │          │
│  │ (modules.yaml)│→ │ Compiler    │→ │ (两层调度)    │          │
│  └─────────────┘  │ (DSL→Steps)  │  │              │          │
│                    └─────────────┘  └──────┬──────┘          │
│                                           │                   │
│              ┌────────────────────────────┼───────────┐      │
│              │                            │           │      │
│              ▼                            ▼           ▼      │
│  ┌─── Module A (worktree-A) ─┐ ┌── Module B ─┐ ┌── Module C ─┤│
│  │ scaffold → gen(f1)        │ │ scaffold    │ │ scaffold   ││
│  │ → gen(f2) → evaluate      │ │ → gen(f1)   │ │ → ...      ││
│  │ → lint → done             │ │ → evaluate  │ │            ││
│  └───────────────────────────┘ └─────────────┘ └────────────┘│
│                                                              │
│  每个 Module Pipeline:                                        │
│    Step 1 → Step 2 → Step 3 → ... (串行，git checkpoint)      │
│    失败 → 回滚到 checkpoint → retry                           │
│    全部通过 → merge → PR                                     │
└──────────────────────────────────────────────────────────────┘
```

### 组件职责

| 组件 | 职责 |
|------|------|
| **Config Loader** | 读取 `modules.yaml`，解析 module 列表 + pipeline 定义 |
| **Pipeline Compiler** | 把声明式 YAML 编译为可执行的 Step 序列 |
| **Scheduler** | 两层调度：module 间并行（ThreadPool）+ module 内串行 |
| **Executor** | 三种执行器：`claude-code` / `shell` / `judge` |
| **State Manager** | 两层状态：git checkpoint（worktree）+ JSON state（pipeline） |
| **Gate Evaluator** | 评估 postcondition，决定通过/重试/失败 |

---

## 三、Pipeline DSL

### 完整语法

```yaml
# modules.yaml
repo: /path/to/test-repo          # 被测仓库路径
base_branch: main                  # 基准分支
concurrency: 5                     # module 间并行数
max_retries: 3                     # 全局默认重试次数
output_branch_prefix: ut-auto      # PR 分支前缀

# Pipeline 定义（通用，可迁移到其他场景）
pipeline:
  - id: scaffold
    executor: claude-code
    skill: ut-scaffold              # CC 加载的 skill
    prompt: |
      你在为 {module} 模块生成测试脚手架。
      源码目录：{source_dir}
      规格编号：{spec_id}
      读取所有 .c/.h 文件，生成测试目录结构。
    postcondition:
      shell: "test -d tests/{module}"
    output: scaffold.json           # CC 产出的状态文件名

  - id: generate
    executor: claude-code
    skill: ut-generate
    loop: per_file                  # 逐文件循环（串行）
    source: "{source_files}"        # 循环变量来源
    prompt: |
      你在为 {module}/{file} 生成单元测试。
      脚手架信息：{.pipeline/scaffold.json}
      上一个文件的验证结果：{.pipeline/generate.verified.json}
      读取源文件，生成 dtest 格式测试用例。
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold} && $.branch >= {branch_threshold}"
    retry: 3
    rollback: git-checkpoint        # 重试前回滚
    output: generate.json

  - id: verify
    executor: shell                 # 确定性验证，不信任 CC
    command: |
      gcov + lcov → .pipeline/generate.verified.json
    depends_on: generate
    output: generate.verified.json

  - id: evaluate
    executor: judge                 # AI 裁判（独立 CC 调用）
    skill: ut-evaluate
    prompt: |
      读取 .pipeline/generate.verified.json（真实覆盖率数据）。
      评估测试用例质量：断言密度、边界覆盖、test smell。
      输出评分到 .pipeline/evaluate.judge.json。
    postcondition:
      shell: "test $(jq '.score' .pipeline/evaluate.judge.json) -ge 60"
    depends_on: verify
    retry: 2
    output: evaluate.judge.json

  - id: finalize
    executor: claude-code
    skill: ut-lint
    prompt: "检查代码规范，修复格式问题"
    depends_on: evaluate
    on_complete:
      - merge: personal_branch       # 合并到个人分支
      - pr:
          title: "UT for {module}"
          body: "Auto-generated UT for {module} (spec: {spec_id})\n\nCoverage: {generate.verified.line}%"
          labels: [auto-generated, ut]

# Module 列表
modules:
  - name: auth
    spec_id: SPEC-2026-001
    source_dir: src/auth/
    source_files:
      - auth_login.c
      - auth_token.c
    coverage:
      line_threshold: 80
      branch_threshold: 70
    variables:                      # 模块级变量，注入 prompt
      mock_strategy: link-time

  - name: payment
    spec_id: SPEC-2026-002
    source_dir: src/payment/
    source_files:
      - payment_process.c
    coverage:
      line_threshold: 85
      branch_threshold: 75
```

### DSL 核心概念

| 概念 | 说明 |
|------|------|
| **Step** | pipeline 中的一个步骤，有唯一 id |
| **Executor** | 步骤的执行方式：`claude-code` / `shell` / `judge` |
| **Loop** | `per_file` 对文件列表逐个执行 |
| **Postcondition** | 通过条件，含 shell 命令 + 期望值表达式 |
| **Retry** | postcondition 不通过时的重试次数 |
| **Rollback** | 重试前的回滚方式（默认 `git-checkpoint`） |
| **Depends_on** | 步骤间依赖关系 |
| **Output** | CC 产出的状态文件名 |
| **On_complete** | pipeline 完成后的动作（merge / PR） |
| **Variables** | 模块级变量，用 `{var}` 语法注入 prompt |

### 变量注入规则

prompt 模板中的 `{xxx}` 在执行前替换：

| 变量 | 来源 | 示例 |
|------|------|------|
| `{module}` | modules.yaml 的 name | `auth` |
| `{file}` | loop 的当前文件 | `auth_login.c` |
| `{source_dir}` | modules.yaml | `src/auth/` |
| `{spec_id}` | modules.yaml | `SPEC-2026-001` |
| `{line_threshold}` | coverage 配置 | `80` |
| `{.pipeline/xxx.json}` | 读取 JSON 文件内容注入 | `{"line": 85, ...}` |

---

## 四、三种 Executor 模型

### 4.1 claude-code Executor

```python
# 执行方式
subprocess.run([
    "claude", "-p", rendered_prompt,
    "--cwd", worktree_dir,
    "--allowedTools", "Read,Write,Edit,Bash",
    "--model", "glm-4.6",
])
```

**行为：**
- 启动 Claude Code（headless `-p` 模式）
- CC 完成后退出
- CC 可在 worktree 内自由读写文件
- CC 自己写 `.pipeline/{step_id}.json`（自述状态）
- **不受信任** — CC 写的数据需要后续 verify 步骤确认

**Skill 加载：** CC 通过 `--skill` 或 CLAUDE.md 加载指定 skill。

### 4.2 shell Executor

```python
# 执行方式
result = subprocess.run(
    rendered_command,
    shell=True,
    cwd=worktree_dir,
    capture_output=True,
    timeout=step_timeout,
)
```

**行为：**
- 在 worktree 内运行确定性 shell 命令
- 用于编译、覆盖率检测、变异测试、lint 等可重复验证的操作
- **完全受信任** — 输出直接写入 `.pipeline/{step_id}.verified.json`

### 4.3 judge Executor

```python
# 执行方式 — 独立 CC 调用，只读模式
subprocess.run([
    "claude", "-p", judge_prompt,
    "--cwd", worktree_dir,
    "--allowedTools", "Read,Bash",   # 只读 + 运行评测脚本
])
```

**行为：**
- 启动一个独立 CC，专门做主观评判
- 只读 + 运行评测脚本权限（不能 Write/Edit 源码）
- 读取 verified.json（真实数据），不读 CC 自述
- 输出评分到 `.pipeline/{step_id}.judge.json`

### 4.4 信任层级

| Executor | 能 Write/Edit | 能 Bash | 产出文件 | 信任度 |
|---------|---------------|---------|---------|--------|
| `claude-code` | ✅ | ✅ | `step.json`（自述） | ❌ |
| `shell` | ❌ | 本身就是命令 | `step.verified.json` | ✅ |
| `judge` | ❌（只读） | ✅（评测脚本） | `step.judge.json` | 🔶 |

**传递规则：** 下一步 CC 的 prompt 注入数据时，优先级：`verified > judge > self-reported`

---

## 五、状态管理

### 5.1 两层状态

```
Worktree 内（git 管理）:
  src/*.c
  tests/*.c
  .pipeline/
    ├── scaffold.json              # CC 自述
    ├── generate.verified.json     # shell 验证
    └── evaluate.judge.json        # AI 裁判

Pipeline 编排器（独立于 git）:
  ~/.cc-pipeline/runs/{run_id}/
    ├── orchestrator-state.json    # 全局状态
    └── modules/
        ├── auth/
        │   ├── module-state.json  # module 级状态
        │   └── transcript.jsonl   # 执行日志
        └── payment/
            └── ...
```

### 5.2 Git Checkpoint 机制

每个 step 完成后自动 commit + tag：

```bash
# 在 worktree 内
git add -A
git commit -m "[pipeline:{module}:{step_id}:{attempt}] {verdict}"

# tag 格式
git tag pipeline/{module}/{step_id}/{attempt}
# 例: pipeline/auth/generate/1
```

### 5.3 Retry + Rollback

```
Step: generate (attempt 1)
  → CC 执行
  → postcondition 失败
  → rollback: git checkout pipeline/auth/scaffold/1
  → 清理 CC 产出物（但保留 scaffold 产出）
  → 重试

Step: generate (attempt 2)
  → CC 重新执行（干净的 worktree）
  → postcondition 通过
  → git commit + tag pipeline/auth/generate/2
```

### 5.4 Orchestrator State Schema

```json
{
  "run_id": "2026-06-29-23-00-00",
  "started_at": "2026-06-29T23:00:00Z",
  "status": "running",
  "concurrency": 5,
  "modules": {
    "auth": {
      "status": "running",
      "current_step": "generate",
      "current_file": "auth_token.c",
      "attempts": {
        "scaffold": { "attempts": 1, "verdict": "pass" },
        "generate": { "attempts": 2, "verdict": "retrying" }
      },
      "worktree": "/tmp/cc-pipeline/auth",
      "branch": "ut-auto/auth"
    },
    "payment": {
      "status": "done",
      "current_step": null,
      "attempts": {
        "scaffold": { "attempts": 1, "verdict": "pass" },
        "generate": { "attempts": 1, "verdict": "pass" },
        "evaluate": { "attempts": 1, "verdict": "pass" },
        "finalize": { "attempts": 1, "verdict": "pass" }
      },
      "pr_url": "https://github.com/.../pull/42"
    }
  }
}
```

---

## 六、调度模型

### 6.1 两层调度

```python
# 伪代码
with ThreadPoolExecutor(max_workers=concurrency) as pool:
    futures = []
    for module in modules:
        future = pool.submit(run_module_pipeline, module, pipeline)
        futures.append(future)
    
    for future in as_completed(futures):
        result = future.result()
        report(module, result)

def run_module_pipeline(module, pipeline):
    worktree = create_worktree(module)
    
    for step in pipeline.steps:
        if step.loop == "per_file":
            for file in module.source_files:
                run_step_with_retry(step, module, file, worktree)
        else:
            run_step_with_retry(step, module, None, worktree)
    
    if step.on_complete:
        merge_and_pr(module, worktree)
    
    cleanup_worktree(worktree)
```

### 6.2 CC 并发控制

- module 间并行：`concurrency` 参数（默认 5，GLM API 限制）
- module 内串行：严格按 step 顺序，一个 CC 退出后下一个才启动
- 同一时刻最多 N 个 CC 实例运行（N = concurrency）

---

## 七、Worktree 生命周期

```
创建：git worktree add /tmp/cc-pipeline/{module} -b ut-auto/{module} {base_branch}
     ↓
Pipeline 执行中（worktree 存活）
     ├── scaffold CC 在此工作
     ├── generate CC 在此工作
     ├── verify shell 在此运行
     └── 每步 git commit + tag
     ↓
Pipeline 完成
     ├── merge: ut-auto/{module} → personal_branch
     ├── PR: gh pr create
     └── 清理: git worktree remove
     ↓
Pipeline 失败（超过重试）
     ├── 保留 worktree（供分析）
     └── 标记 failed
```

---

## 八、Postcondition 评估

### 语法

```yaml
postcondition:
  shell: "check_coverage.sh {module} {file}"
  expect: "$.line >= {line_threshold} && $.branch >= {branch_threshold}"
```

### 执行流程

```
1. 运行 shell 命令 → 输出 JSON 到 stdout
2. jq 表达式评估 expect
3. 通过 → commit + tag → 下一步
4. 不通过 → rollback → retry（如果还有重试次数）
5. 重试耗尽 → 标记 module 失败
```

### Expect 表达式

使用简化 JSONPath + 比较运算：

```
$.line >= 80              # 简单比较
$.line >= 80 && $.branch >= 70  # AND
$.errors == 0             # 等于
$.score >= 60             # 数值比较
exists("$.report")        # 文件存在检查
```

---

## 九、失败处理与恢复

### 失败级别

| 级别 | 触发条件 | 行为 |
|------|---------|------|
| **Step Retry** | postcondition 失败 | 回滚 + 重试（≤ max_retries） |
| **Module Fail** | 重试耗尽 | 标记 module 失败，保留 worktree |
| **Module Skip** | 前置步骤失败 | 跳过该 module 的后续步骤 |
| **Run Abort** | 所有 module 失败 | 整个 run 标记失败 |

### 崩溃恢复

```bash
# 恢复中断的 run
cc-pipeline resume --run-id 2026-06-29-23-00-00

# 编排器读取 orchestrator-state.json
# 找到每个 module 的最后 checkpoint
# 从断点继续
```

---

## 十、安全性

### 文件保护

- worktree 内的源码目录（`src/`）标记为只读（CC 不能修改被测源码）
- CC 只能在 `tests/` 目录和 `.pipeline/` 目录写文件
- `.pipeline/state.json`（编排器状态）不在 worktree 内，CC 无法篡改

### 权限控制

| Executor | 文件写权限 | Bash 权限 |
|---------|-----------|----------|
| claude-code | tests/ + .pipeline/ | 受限（无 rm -rf / git push） |
| shell | 无（命令自身决定） | 命令本身 |
| judge | 无（只读） | 只允许评测脚本 |

---

## 十一、CLI 接口

```bash
# 基本用法
cc-pipeline run modules.yaml

# 指定并行度
cc-pipeline run modules.yaml --concurrency=3

# 只跑特定 module
cc-pipeline run modules.yaml --module=auth

# 恢复中断的 run
cc-pipeline resume --run-id 2026-06-29-23-00-00

# Dry run（只生成 tasks，不执行）
cc-pipeline plan modules.yaml

# 查看状态
cc-pipeline status --run-id 2026-06-29-23-00-00

# 清理失败的 worktree
cc-pipeline cleanup --run-id 2026-06-29-23-00-00
```

---

## 十二、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.10+ | 生态丰富，subprocess 管理方便，团队熟悉 |
| CC 调用 | `claude -p`（headless） | 不依赖 claude-overnight SDK |
| 并发 | concurrent.futures.ThreadPoolExecutor | 简单可靠，GIL 对 IO 密集型足够 |
| 配置 | YAML | 人类可读，支持复杂结构 |
| 状态 | JSON + Git | 无需数据库 |
| CI/定时 | cron / systemd-timer | 外部触发 |
| 表达式评估 | 自写轻量 JSONPath | 避免 jq 依赖 |

---

## 十三、与 claude-overnight 的关系

| 维度 | cc-pipeline | claude-overnight |
|------|-------------|-----------------|
| 定位 | 多阶段串行 pipeline 编排 | 并行任务蜂群执行 |
| 调度模型 | DAG pipeline | flat task queue |
| CC 调用 | `claude -p` headless | Claude Agent SDK |
| 状态管理 | git checkpoint + JSON | run.json + tasks.json |
| 信任模型 | 三层（CC/shell/judge） | 单层（CC 自述） |
| 通用性 | 任意 pipeline 场景 | 偏编码任务 |
| 技能自进化 | 无（Phase 2 可选） | Librarian + A/B |

**不依赖 claude-overnight，但可复用其设计思想。**
