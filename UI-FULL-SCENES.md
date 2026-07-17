# cc-pipeline 全场景 UI 逐行解剖

> 2026-07-17 | -v / -vv / 默认 / 失败 / resume / report / transcript / help

---

## 场景A：-v 多模块 per_file

```
 LINE | 内容
 ─────┼─────────────────────────────────────────
   1  | verbose mode ON (level 1) — step progress    ←  冗余横幅
   2  | 🌙 cc-pipeline 0.3.2
   3  |    concurrency=2  modules=['auth','payment']
   4  |
   5  | [07:54:55] [auth]       scaffold START        ←  8 空格 padding，浪费宽度
   6  | [07:54:55] [auth]       postcondition: echo ok ← postcondition 原命令
   7  | [07:54:55] [auth]       scaffold PASS
   8  | [07:54:55] [auth] [a.c] generate START         ←  per_file 未加 [1/2] 进度
   9  | [07:54:55] [auth]       postcondition: echo ok ←  重复噪音 × 8 次
  10  | [07:54:55] [auth] [a.c] generate PASS
  ... | (auth 的 b.c + evaluate 重复同样模式)
  19  | 📁 Worktree preserved at .../worktrees/auth    ←  3 行 worktree 噪音
  20  |    Branch: cc-auto/auth
  21  |    Manual merge: ...
  22  | ✅ auth     passed  (4 steps, 2 files)
  23  | [07:54:55] [payment]   scaffold START          ←  payment 部分与 auth 完全相同
  ... | (payment 重复 auth 的模式)
  38  | =============================================
  39  |   ✓ auth                  passed               ←  与 L22 完全重复
  40  |   ✓ payment               passed
  41  | =============================================
  42  |   2 passed, 0 failed

问题：
  ① 同秒内 22 个事件打 22 个相同时间戳 → 零信息
  ② 8 行 `postcondition: echo ok` → 纯噪音
  ③ per_file 无 `[a.c 1/2]` 进度 → 不知道还剩几个文件
  ④ [module] 与 [file] 用 8 空格 padding → 浪费 1/3 行宽
  ⑤ worktree 3 行插入模块间 → 打断进度流
  ⑥ 同一结果打印两遍（L22 + L39）

改进：
  app · scaffold ✓, generate[a.c ✓, b.c ✓], evaluate ✓  (2 files)
  payment · scaffold ✓, generate[a.c ✓], evaluate ✓    (1 file)
  ─────────────────────────────────────────
  2/2 passed · worktrees at runs/worktrees/
```

---

## 场景B：-vv 超详细

```
 相比 -v 新增:
   [07:54:55]   SHELL: echo S           ← SHELL 前缀与 step 的 [module] 不对齐
   [07:54:55]   SHELL: echo G-a.c
   [07:54:55]   SHELL: echo G-b.c

问题：
  ① SHELL 行缩进不同 —— step 行是 `[time] [module] [file] STEP STATUS`，
     SHELL 行是 `[time] SHELL: cmd`，列对不齐
  ② 对 shell 用户来说 `SHELL: echo S` 信息量低——用户自己写的就是 `echo S`
  ③ -vv 应该区分 CC executor（显示 prompt）和 shell executor（不需要显示命令）

改进：
  去掉 SHELL 行（用户自己写了什么自己知道）。
  -vv 的价值在于显示 CC prompt 和 CC 输出，对 shell 不需要。
```

---

## 场景C：on_failure jump -v

```
   [07:55:02] [app] rescue START
   [07:55:02] [app] rescue PASS
   [07:55:02] [app] worker START
   ❌ Shell failed (exit 1): exit 1
   [07:55:02] [app] worker ❌ FAIL — shell failed: exit 1
   [07:55:02] [app] ↩️  JUMP: worker → rescue (jump 1)       ← 跳转
   [07:55:02] [app] rescue START                                ← rescue 再次 START
   [07:55:02] [app] rescue PASS
   [07:55:02] [app] worker START                                ← worker 再次 START
   ❌ Shell failed (exit 1): exit 1
   [07:55:02] [app] worker ❌ FAIL — shell failed: exit 1
   ✗ app      failed — Step 'worker' failed after 1 attempts

问题：
  ① rescue PASS → worker FAIL → JUMP → rescue START → rescue PASS → worker START
     rescue 第一次的 PASS 和第二次的 START 之间只隔了一个 worker FAIL 行，
     肉眼很难看出"这是第二轮了"
  ② "attempts" 计数归零：worker 首次 fail + jump 后第二次 fail，
     显示 "failed after 1 attempts" 而不是 "2 attempts over 2 rounds"
  ③ jump 事件行没有换行/分隔，淹没在日志流中

改进：
   rescue       ✓
   worker       ✗ (exit 1)  ──JUMP→ rescue
   ── round 2 ──
   rescue       ✓
   worker       ✗ (exit 1)  max jumps reached
```

---

## 场景D：默认模式失败

```
🌙 cc-pipeline 0.3.2
   concurrency=1  modules=['app']

  🧹 Worktree 'app': 目录已存在，删除重建
  ❌ Shell failed (exit 1): exit 1
  [07:55:02] [app] worker ❌ FAIL — shell failed: exit 1
  ❌ Shell failed (exit 1): exit 1                                ← 同一错误打两遍！
  [07:55:02] [app] worker ❌ FAIL — shell failed: exit 1          ← 同一错误打两遍！
  ✗ app      failed — Step 'worker' failed after 1 attempts

问题：
  ① 同样失败信息输出两次（一次裸的 ❌ Shell failed，一次带 [time] [app] 的）
  ② 默认模式不应显示 worktree 清理信息（`🧹 Worktree ... 删除重建`）
  ③ `❌ Shell failed (exit 1): exit 1` — 命令就是 `exit 1`，出现两遍 `exit 1`

改进：
  🌙 app · 0/1 · run 17-07-12-06
  ─────────────────────────────
  rescue  ✓
  worker  ✗ (exit 1)  ──JUMP→ rescue
  worker  ✗ (exit 1)  max jumps · giving up
```

---

## 场景E：resume 输出

```
All modules already passed. Nothing to resume.
```

✅ 简洁。但 `resume --dry-run` 输出可以更丰富：

```
📊 Resume Preview (dry-run)
═══════════════════════════════════════════════

  Module: m1 — skip 12 completed step(s): ['checker/a.c', ...]

  Modules to run: ['m1']
  ✅ Run without --dry-run to execute.
```

问题：
① `['checker/a.c', 'common', 'final', 'fix', 'fixer/a.c', ...]` — 12 个 step 名挤一行
② `Modules to run: ['m1']` — 既然全都已完成，应该说 "Nothing to resume. All modules passed."

改进：
  Module m1: 12 steps already completed — skipping
  Module m2: nothing completed — will run all
  ─────────────────────────────────────────
  Nothing to resume. All done.

---

## 场景F：report 输出

```
# Pipeline Run Report

**Run ID:** 2026-07-17T07-55-02
**Generated:** 2026-07-17 07:55:02 UTC

## Summary
| Metric | Value |
|--------|-------|
| Modules | 3 |
| Passed | 2 |
| Failed | 1 |
| Success Rate | 66.7% |

## Module Details

### app
| Step | Status | Attempt | Reason |
|------|--------|---------|--------|
| setup | PASS | 1 |  |
| flaky | PASS | 3 |  |
| verify | PASS | 1 | All conditions passed |
| merge | ? | 1 |  |
| rescue | PASS | 1 | No postcondition |
| worker | FAIL | 1 | shell failed: exit 1 |

**Duration:** 159.4s
```

问题：
① `Status: ?` — merge 步骤状态是问号，无意义
② `Reason` 列留空或写 `No postcondition` — 无信息量
③ 多 module 混在一个表格中 — auth 和 payment 被合并
④ 没有失败模块的高亮/优先级排序
⑤ Duration 159.4s — 精确到 0.1s 但 run_id 精确到秒，不一致

改进：
  成功模块压缩为 1 行，失败模块展开详情。
  Status 用 ✅/❌ 代替 PASS/FAIL/?。

```
### ✅ auth (4 steps, 2 files · 2.3s)
### ✅ payment (3 steps, 1 file · 1.1s)
### ❌ app (failed · 159.4s)
  | worker | ✗ (exit 1) | after 1 attempt |
  💡 cc-pipeline transcript runs --module app
```

---

## 场景G：transcript 输出

```
── 07:52:23 ── setup ── attempt 1 ──
   [command_audit] {'ts': '...', 'command': 'echo setting_up', 'returncode': 0}
   ✅ PASS —

── 07:52:23 ── flaky ── attempt 2 ──
   [command_audit] {'ts': '...', 'command': 'C=/tmp/ux-v/count.txt...', 'returncode': 1}
   ⚠️  RETRY (attempt 2) — shell failed: exit 1
```

问题：
① `[command_audit]` 行是 Python dict 的 repr — 对人类不可读
② 命令是多行 shell 用 `\n` 编码 — 完全不可读
③ 每个事件重复整个 command 字段 — 3 次 retry = 3 遍同样的命令
④ `✅ PASS —` 后面是空 — 为什么有一个破折号？

改进：
  按 events 分组。失败高亮。命令只显示一次（首次），后续引用。

```
── setup (attempt 1) ✓  07:52:23
── flaky (attempt 1) ✗  exit 1
── flaky (attempt 2) ✗  exit 1
── flaky (attempt 3) ✓  07:52:23
── verify (attempt 1) ✓  07:52:23
```

---

## 场景H：--help 输出

```
usage: cc-pipeline run [-h] [--concurrency CONCURRENCY] [--module MODULE]
                       [--model MODEL] [--run-dir RUN_DIR] [--daemon]
                       [--verbose] [--dry-run]
                       config
```

问题：无示例。无子命令列表。

改进：在 UI-READABILITY.md 场景7中已给出完整建议。

---

## 汇总：改进优先级

| 优先级 | 场景 | 改进 |
|--------|------|------|
| P0 | -v | postcondition 成功时隐藏，失败时显示原因 |
| P0 | -v | 去掉无效时间戳（同秒内只打一次） |
| P0 | 默认 | 失败信息打两遍的 bug |
| P1 | -v | per_file 加 `[1/3]` 进度标记 |
| P1 | -v | 重试用 `(attempt 2/3)` 格式，PASS 绿色区分 |
| P1 | -v | worktree 信息移到末尾汇总 |
| P1 | resume | 全部完成时说 "Nothing to resume" |
| P2 | report | 压缩成功模块，展开失败模块 |
| P2 | transcript | command_audit 用人类可读格式 |
| P2 | -vv | shell executor 不显示 SHELL 行 |
