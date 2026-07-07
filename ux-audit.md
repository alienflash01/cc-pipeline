# cc-pipeline 用户体验审计报告

> 角色视角：挑剔的产品体验官，从"新用户第一次接触"到"日常使用"全链路体验
> 评估方式：端到端真实操作，记录每一个卡点、困惑和摩擦
> 日期：2026-07-07

---

## 总评：6/10

功能完善，架构扎实，但**新用户第一次跑通的成本太高**。一个功能强大的工具如果让人在第一步就摔跤，大部分用户不会走到第二步。

---

## 评分明细

| 维度 | 得分 | 评价 |
|---|---|---|
| 首次安装 | 8/10 | `pip install -e .` 顺滑，check 命令很赞 |
| 新手上手 | **3/10** | init 命令生成的配置直接报错，致命 |
| 错误信息 | **4/10** | 报错不指向解决方向，用户不知道怎么改 |
| CLI 完整度 | 7/10 | 10 个子命令覆盖全面，但缺引导 |
| 文档质量 | 8/10 | USER-GUIDE 22 章非常详尽 |
| 进度可感知 | **5/10** | 运行中用户不知道"跑到哪了、还要多久" |

---

## 🔴 P0：致命体验问题（用户会在这里放弃）

### P0-1：`cc-pipeline init` 生成的配置无法直接运行

**这是最严重的问题。** init 交互式生成 config.yaml，用户按提示一步步填完，结果 dry-run 直接报错。

**复现路径（新用户的真实体验）：**
```
$ cc-pipeline init
  项目路径 repo（默认 '.'）: .        ← 用户回车
  任务类型 1=UT生成 2=代码审查 3=自定义: 1
  source_dir: auth
  模块列表: src/auth/                 ← init 接受了这个输入
  assert_macro: tests/
  concurrency: pytest tests/ -q       ← init 也接受了这个

$ cc-pipeline run config.yaml --dry-run
  Error: concurrency must be a positive integer, got: pytest tests/ -q  💀
```

修了 concurrency 后再跑：
```
  Error: Invalid module name 'src/auth/': only alphanumeric, underscore, 
  and hyphen allowed  💀💀
```

**新用户的心理：** "我按你的提示填的，你说不行？那你为什么要接受这个输入？"

**根因：** init 的交互逻辑有两个 bug：
1. concurrency 问题是第 5 个问的，但用户填了"pytest tests/ -q"（测试命令），init 没校验就写入
2. 模块名"src/auth/"包含路径分隔符，init 没校验

**修复建议：** init 在接受每个输入时就做校验。不合法就重新问。**生成的配置必须能直接 dry-run 通过。** 这是铁律。

---

### P0-2：YAML 语法错误的提示不够友好

```
$ cc-pipeline run bad_syntax.yaml --dry-run
  Error: Config validation failed: YAML syntax error: while scanning a simple key
  in "bad_syntax.yaml", line 2, column 1
  could not find expected ':'
  in "bad_syntax.yaml", line 3, column 7
```

这个报错对懂 YAML 的人可以接受，但对新手来说——"第 2 行第 1 列是什么意思？我该怎么改？"

**修复建议：** 在 YAML 错误后追加一行：`提示：检查缩进是否一致（用空格而非 Tab），key 后面必须有冒号+空格。常见错误是在 pipeline 后面少了冒号。`

---

## 🟡 P1：严重体验问题（用户会困惑但不会立刻放弃）

### P1-1：错误信息不指向修复方向

四种不同的配置错误，全部报同一个消息：

```
$ cc-pipeline run missing_field.yaml --dry-run
  Error: Config validation failed: Missing required field: modules (or empty list)

$ cc-pipeline run bad_executor.yaml --dry-run  
  Error: Config validation failed: Missing required field: modules (or empty list)

$ cc-pipeline run circular_dep.yaml --dry-run
  Error: Config validation failed: Missing required field: modules (or empty list)
```

**三个完全不同的问题**（缺 prompt、executor 写错、循环依赖），都因为缺 modules 而先挂掉。用户只看到"缺 modules"，修完 modules 后才暴露下一个问题。一层一层剥洋葱。

**修复建议：** config validation 应该一次性收集所有错误，不是遇到第一个就停：
```
Config validation failed (3 errors):
  1. Missing required field: modules
  2. Step 's1': executor 'nonexistent' is not valid (choose: claude-code, shell)
  3. Circular dependency detected: s1 → s2 → s1
```

### P1-2：`status` 命令缺乏引导

```
$ cc-pipeline status
  No runs found.
```

然后呢？新用户不知道怎么开始。

**修复建议：**
```
$ cc-pipeline status
  No runs found.
  
  💡 Getting started:
     1. cc-pipeline init          — Generate a config interactively
     2. cc-pipeline run config.yaml --dry-run   — Preview your pipeline
     3. cc-pipeline run config.yaml             — Execute
```

### P1-3：`report` 和 `transcript` 不提供 run-dir 就报 usage error

```
$ cc-pipeline report
  error: the following arguments are required: --run-dir
```

用户不知道 run-dir 在哪。

**修复建议：** 先列出可用的 run 目录，再让用户选：
```
$ cc-pipeline report
  Available runs:
    ~/.cc-pipeline/runs/run-20260707-001 (2 modules, completed)
    ~/.cc-pipeline/runs/run-20260706-003 (3 modules, failed)
  
  Usage: cc-pipeline report --run-dir <path>
```

### P1-4：运行中缺少进度感知

dry-run 的输出很好：
```
  Steps: scaffold → generate(per_file) → evaluate
  Module: auth (1 files)
  Estimated: 3 CC calls
```

但真实运行时，用户坐在终端前等 CC 回复，只能盯着光标闪。不知道：
- 当前跑到哪个步骤？
- 还剩几步？
- 每步花了多少时间？
- 当前模块 vs 并行模块的进度？

**修复建议：** 运行时加一个简洁的进度条或状态行：
```
[Module: auth] [Step: 2/3 generate] [Elapsed: 45s] [ETA: ~60s]
```

### P1-5：Quick Start 示例中 source_files 格式与 init 生成的格式不一致

README Quick Start 里：
```yaml
source_files: [auth_login.c]
```

init 生成的：
```yaml
source_files:
  - path: example.c
    assert_macro: tests/
```

两种格式，用户不知道哪个对。

**修复建议：** 统一为一种格式，或文档中明确说明两种都支持。

---

## 🟢 P2：改进建议（锦上添花）

### P2-1：README badge 数据过时

```markdown
[![tests](https://img.shields.io/badge/tests-225%20passed-brightgreen)]()
[![coverage](https://img.shields.io/badge/coverage-91%25-green)]()
```

实际是 651 passed，不是 225。badge 是硬编码的，不会自动更新。

**建议：** 要么用 CI 自动更新，要么删掉数字只留标签。

### P2-2：`python -m cc_pipeline` 不可用

```bash
$ python3.12 -m cc_pipeline --help
  No module named cc_pipeline.__main__
```

很多 Python 工具支持 `python -m` 方式运行。缺少 `__main__.py`。

### P2-3：USER-GUIDE 非常详尽但太长（22 章）

对新手来说 22 章是恐怖的。建议分成：
- **Quick Start**（1 页，5 分钟跑通）
- **Core Guide**（安装 + 配置 + 运行 + 报告，10 分钟）
- **Advanced**（daemon / cron / 崩溃恢复 / transcript，按需阅读）

### P2-4：init 的任务类型只支持中文

```
任务类型 1=UT生成 2=代码审查 3=自定义:
```

非中文用户看不懂。

### P2-5：check 命令非常好——但 init 里没用它

`cc-pipeline check` 是体验最好的命令——清晰的 ✅ 标记，检查 Python/Git/CC/磁盘空间。但 init 命令不调用 check，导致用户可能在不满足前置条件的情况下生成配置。

**建议：** init 第一步先跑 check，不通过就提示。

### P2-6：缺少 `cc-pipeline examples` 命令

新用户想看示例配置，但不知道 examples/ 目录在哪。应该有命令直接列出可用示例。

### P2-7：卸载有专门命令但安装没有

```
cc-pipeline uninstall   ← 有
cc-pipeline install     ← 没有
```

虽然 README 有 `pip install -e .`，但从 UX 一致性角度，应该有 `cc-pipeline install` 或者至少 `cc-pipeline doctor`。

---

## 体验路径对比

### 理想路径（应该是）
```
pip install → cc-pipeline check → cc-pipeline init → dry-run ✅ → run → 报告
     1步          1步               1步           1步        1步     1步
```

### 当前实际路径（用户经历的）
```
pip install → cc-pipeline init → dry-run 💀 → 修 config → dry-run 💀 → 查文档 → 
修 config → dry-run ✅ → run → 等待(不知道进度) → 成功/失败(不知道为什么)
```

**用户需要 4-5 次试错才能跑通。每次试错都是流失风险。**

---

## 优先修复建议

| 优先级 | 问题 | 工作量 | 影响 |
|---|---|---|---|
| **P0** | init 生成可运行的配置 | 半天 | 新用户留存率 |
| **P0** | config 一次性报全部错误 | 1天 | 调试效率 |
| P1 | status/report 引导提示 | 2小时 | 降低困惑 |
| P1 | 运行时进度条 | 1天 | 使用体验 |
| P1 | 统一 source_files 格式 | 2小时 | 文档一致性 |
| P2 | badge 自动更新 | 1小时 | 专业感 |
| P2 | __main__.py | 10分钟 | Python 习惯 |
| P2 | USER-GUIDE 分层 | 半天 | 降低阅读门槛 |

---

## 做得好的地方（不要丢掉）

1. ✅ **`cc-pipeline check`** — 最好的命令，清晰、有用、友好
2. ✅ **dry-run 预览** — pipeline DAG + 模块 + 变量一览，非常赞
3. ✅ **postcondition 门控** — 这是核心价值，design 很强
4. ✅ **USER-GUIDE 22 章** — 内容质量很高，覆盖全面
5. ✅ **init 交互式生成** — 方向正确，只是实现有 bug
6. ✅ **preflight warning** — "Repo is not a git repository" 这种提醒很贴心
7. ✅ **错误消息中文+英文混合** — 适合中国开发者
8. ✅ **on_failure 回跳** — transcript 中清晰显示 JUMP BACK + 跳跃次数
9. ✅ **verbose 并行输出** — `[23:09:31] [alpha] step1 START/PASS` 格式清晰，时间戳+模块名+步骤名

---

## 深度使用发现（程序员视角）

> 以下问题来自端到端真实运行：多模块并行、postcondition expect、on_failure 回跳、transcript、status/report、clean、resume

### 🔴 P0-3：`status` 命令输出无意义

```
$ cc-pipeline status
  Recent runs:
    mod1
    orchestrator-state.json
    worktrees
```

这**不是 run 列表**——这是 `ls ~/.cc-pipeline/runs/` 的结果。把目录内容当 run 列表展示了。程序员看到 `orchestrator-state.json` 和 `worktrees` 出现在 runs 列表里会非常困惑。

**根因：** status 命令把 run 根目录下的所有条目当 run 列出了，而非扫描子目录或读取 state 文件中的 run_id。

**修复：** status 应该读取每个 run 的 state 文件，展示：
```
$ cc-pipeline status
  Recent runs:
    2026-07-07 23:09 — 3 modules (3 passed, 0 failed) — config_multi.yaml
    2026-07-07 23:08 — 1 module  (0 passed, 1 failed) — config_fail.yaml
```

### 🔴 P0-4：`report` 和 `resume` 找不到运行数据

运行成功后，`~/.cc-pipeline/` 几乎是空的（只有 runs/ 空目录）。实际 state 和 transcript 存在**项目目录的 `.pipeline/` 下**。但 `report --run-dir` 指向 `~/.cc-pipeline/runs/` 时报 "State file not found"。

**程序员的困惑：** 刚跑完的 pipeline，status 找不到，report 找不到，resume 不知道用哪个路径。运行数据到底在哪？

**根因：** run 数据的存储位置和 CLI 命令期望的默认路径不一致。

**修复：** 统一存储路径，或让 status/report/resume 自动发现最近的 run。

### 🟡 P1-6：`variables` 字段定义了但 dry-run 不显示、prompt 中不展开

```yaml
variables:
  test_framework: pytest
```

dry-run 警告 `Unknown variable {test_framework}`，且 Variables 区域不显示自定义变量。

**程序员的困惑：** 我明明定义了 variables，为什么说 unknown？是字段名写错了？还是 variables 的用法变了？

**需要验证：** variables 的文档说明 vs 实际行为是否一致。如果不支持 prompt 中用 `{var}` 引用 variables，文档需要明确说明。

### 🟡 P1-7：`command` 字段在步骤级是 "Unknown field"

```yaml
- id: verify
  executor: shell
  command: "pytest tests/"
```

dry-run 输出：`UserWarning: Unknown field 'command' in step 'verify' — ignored`

**程序员的困惑：** shell executor 不应该用 `command` 来指定要跑的命令吗？USER-GUIDE 里 shell executor 的示例用的是 `prompt` 字段而非 `command`。但 `command` 是更直觉的字段名。

**修复：** 要么接受 `command` 作为 `prompt` 的别名（shell executor 语境下更自然），要么文档明确说明 shell executor 也用 `prompt` 字段。

### 🟡 P1-8：`--module` 过滤不存在的模块不报错

```
$ cc-pipeline run config.yaml --dry-run --module nonexistent
  ✅ Config valid. Run without --dry-run to execute.
```

dry-run 正常输出所有模块，`--module nonexistent` 被静默忽略。

**程序员的困惑：** 我指定了一个不存在的模块，你没告诉我。实际运行时会发生什么？跳过所有模块？报错？

**修复：** `--module foo` 时检查 foo 是否在 config.modules 中，不存在则报错退出。

### 🟡 P1-9：transcript 跨多次运行累积，不按 run 分隔

```
$ cc-pipeline transcript --run-dir ~/.cc-pipeline/runs --module mod1
```

输出包含了**所有历史运行**的记录（多次不同配置的运行混在一起），没有按 run_id 分隔。程序员想看"最近一次运行的详细日志"，但看到的是全部历史的堆叠。

**修复：** transcript 默认只显示最近一次运行，加 `--all` 查看历史，或 `--run-id` 精确过滤。

### 🟡 P1-10：运行中没有模块级进度总览

3 个模块并行运行时，控制台逐条输出 `[alpha] step1 PASS`。但程序员想看的是总览：

```
Module    Status     Step       Elapsed
────────  ─────────  ─────────  ───────
alpha     ✅ done    —          0.3s
beta      🔄 running step2      2.1s
gamma     ⏳ queued  —          —
```

并行 5+ 模块时，逐条日志会刷屏。

### 🟡 P1-11：`clean` 只清 worktree 不清分支

```
$ cc-pipeline clean --repo /tmp/ux-deep
  🗑️  worktree: ~/.cc-pipeline/runs/worktrees/mod1
  ✅ Cleaned: 1 worktrees, 0 branches, 4 tags.

$ git branch
  cc-auto/mod1    ← 还在！
```

清了 worktree 但 `cc-auto/*` 分支残留。多次运行后分支会堆积。

**修复：** `clean --all` 应该同时清理 worktree + 分支 + tag + .pipeline 目录。

### 🟡 P1-12：失败后没有告诉用户"下一步该怎么做"

```
  ✗ mod1     failed — Step 'fail_step' failed after 2 attempts
  💡 cc-pipeline transcript --run-dir ... --module mod1
```

提示了 transcript 命令。但程序员真正想知道的是：**为什么失败？怎么修？**

**修复：** 失败时追加一段诊断信息：
```
  ✗ mod1     failed — Step 'fail_step' failed after 2 attempts
  
  📋 Last error: exit code 1, stderr: (空)
  💡 Next steps:
     1. cc-pipeline transcript --run-dir ... --module mod1  — 查看完整日志
     2. cc-pipeline resume config.yaml --run-dir ...        — 修复后继续
     3. cc-pipeline run config.yaml --module mod1           — 只重跑这个模块
```

### 🟢 P2-8：postcondition expect 失败时 transcript 不显示实际值

```
  ❌ FAIL — Condition failed: $.coverage >= 80
```

程序员想知道：**实际值是多少？** 50？75？还是字段缺失？

**修复：**
```
  ❌ FAIL — Condition failed: $.coverage >= 80 (actual: 50)
```

### 🟢 P2-9：运行输出中 `concurrency` 总是显示 5，不管配置文件写的多少

```
🌙 cc-pipeline 0.3.0
   concurrency=5  modules=['mod1', 'mod2', 'mod3']
```

config 里写了 `concurrency: 3`，但输出显示 5（默认值）。实际是否按 3 跑了？

### 🟢 P2-10：on_failure 回跳的 transcript 追踪号（jump N）很好

```
  ↩️  JUMP BACK: step2 → step1 (jump 1)
```

这个体验很好。但如果有 max_jumps 限制，应该在跳到上限时明确提示：
```
  🚫 Max jumps reached (3) — stopping retries
```
