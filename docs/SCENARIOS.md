# cc-pipeline 场景行为手册

> 用具体的 3 文件 × 4 步示例描述所有场景的行为。
> 人类可审查——每一步发生了什么，为什么。

---

## 术语约定

| 术语 | 含义 |
|------|------|
| **P1-P4** | pipeline 的 4 个 step（如 scaffold → generate → evaluate → report） |
| **A.c B.c C.c** | module 的 3 个源文件 |
| **PASS / FAIL** | postcondition 通过 / 不通过 |
| **START** | step 开始执行 |

---

## 1. 基本执行流程

**配置：**
```yaml
pipeline:
  - id: P1
  - id: P2
  - id: P3
  - id: P4
modules:
  - name: auth
    source_files: [A.c, B.c, C.c]
```

**行为（无 loop，无 retry，无 on_failure）：**

```
P1 START → PASS
P2 START → PASS
P3 START → PASS
P4 START → PASS
✅ auth passed (4 steps)
```

每个 step 顺序执行，全部通过后 module 标记 passed。

---

## 2. per_file 循环展开

### 2a. file_order: batched（默认）

**配置：**
```yaml
pipeline:
  - id: P1
  - id: P2
    loop: per_file
  - id: P3
    loop: per_file
  - id: P4
```

**行为：**
```
所有文件先过 P1：
  P1 START → PASS

所有文件过 P2（逐个）：
  P2 START [A.c] → PASS
  P2 START [B.c] → PASS
  P2 START [C.c] → PASS

所有文件过 P3（逐个）：
  P3 START [A.c] → PASS
  P3 START [B.c] → PASS
  P3 START [C.c] → PASS

所有文件过 P4：
  P4 START → PASS
```

**关键：** P1 和 P4 没有 `loop: per_file`，所以只执行一次。P2 和 P3 展开成 3 次。

### 2b. file_order: sequential

**行为：**
```
P1 START → PASS

A.c 走完整流程：
  P2 START [A.c] → PASS
  P3 START [A.c] → PASS

B.c 走完整流程：
  P2 START [B.c] → PASS
  P3 START [B.c] → PASS

C.c 走完整流程：
  P2 START [C.c] → PASS
  P3 START [C.c] → PASS

P4 START → PASS
```

**关键：** 每个文件走完所有 loop 步骤，再下一个文件。

---

## 3. retry 重试

**配置：**
```yaml
pipeline:
  - id: P2
    loop: per_file
    retry: 2
```

**场景：P2[B.c] 第一次失败，第二次通过**

```
P2 START [A.c] → PASS

P2 START [B.c] → CC 执行成功
  postcondition: test -f tests/test_B.c
  → 文件不存在 → FAIL
  → retry 预算 2-1=1

P2 RETRY [B.c] (attempt 2) → CC 执行成功
  postcondition: test -f tests/test_B.c
  → 文件存在 → PASS

P2 START [C.c] → PASS
```

**关键：**
- retry 不回滚——CC 在上一次的代码基础上继续改进
- retry 预算是 per-step 的：P2[B.c] 用掉的预算不影响 P2[C.c]
- 执行失败（exit ≠ 0）和 postcondition 失败共享同一个 retry 预算

### 3a. retry 预算耗尽

```
P2 START [B.c] → FAIL（retry 预算 2→1）
P2 RETRY [B.c] (attempt 2) → FAIL（retry 预算 1→0）
P2 RETRY [B.c] (attempt 3) → FAIL（retry 预算 0）
P2 ❌ FAIL — Step 'P2' failed after 3 attempts
✗ auth failed
```

---

## 4. on_failure 回跳

**配置：**
```yaml
pipeline:
  - id: P3
    loop: per_file
    on_failure: P2
    on_failure_max_jumps: 2
  - id: P4
    loop: per_file
```

**场景：P3[C.c] 失败 → 跳回 P2[C.c] 重做**

### 4a. 非 per_file 场景

```
P1 → PASS
P2 → PASS
P3 → FAIL（retry 耗尽）
  ↩️ JUMP: P3 → P2 (jump 1)
P2（重跑）→ PASS
P3（重跑）→ PASS
P4 → PASS
✅ auth passed
```

### 4b. per_file 场景（关键区别）

```
P2 [A.c] → PASS
P2 [B.c] → PASS
P2 [C.c] → PASS

P3 [A.c] → PASS
P3 [B.c] → PASS
P3 [C.c] → FAIL（retry 耗尽）
  ↩️ JUMP: P3[C.c] → P2[C.c] (jump 1)    ← 只跳 C.c 的 P2！

P2 [C.c]（重跑）→ PASS                    ← A.c 和 B.c 的 P2 不重跑
P3 [C.c]（重跑）→ FAIL（retry 耗尽）
  ↩️ JUMP: P3[C.c] → P2[C.c] (jump 2)

P2 [C.c]（重跑）→ PASS
P3 [C.c]（重跑）→ PASS

P4 [A.c] → PASS
P4 [B.c] → PASS
P4 [C.c] → PASS
✅ auth passed
```

**关键：**
- on_failure 跳转精确到文件级——C.c 失败只跳 C.c 的 P2，不影响 A.c 和 B.c
- jump 计数 per-target（P2/C.c 独立计数）
- retry 预算在每次 jump 后重置（新 step 获得新的 retry 预算）

### 4c. on_failure max_jumps 耗尽

```
P3 → FAIL → ↩️ JUMP P3→P2 (jump 1)
P2 → PASS → P3 → FAIL → ↩️ JUMP P3→P2 (jump 2)
P2 → PASS → P3 → FAIL
  🚫 max_jumps (2) 已用完，不再跳转
✗ auth failed
```

### 4d. max_jumps=0 禁用跳转

```
P3 → FAIL（retry 耗尽）
  on_failure 已配置但 max_jumps=0 → 不跳转
✗ auth failed
```

---

## 5. retry × on_failure 交互

**配置：** `retry: 1, on_failure: P2, on_failure_max_jumps: 1`

```
P3 → FAIL（retry 1→0）
P3 RETRY → FAIL（retry 0）
  → retry 耗尽 → on_failure 触发
  ↩️ JUMP P3→P2 (jump 1)

P2 → PASS
P3 → FAIL（retry 1→0，新预算）
P3 RETRY → FAIL（retry 0）
  → retry 再次耗尽 → on_failure 检查
  → jump 2 > max_jumps 1 → 不再跳
✗ auth failed
```

**总执行次数：** (retry+1) × (max_jumps+1) = 2 × 2 = 4 次 P3

---

## 6. postcondition 门控

### 6a. postcondition 通过

```
P2 START [A.c] → CC 执行成功
  postcondition: cat .pipeline/result.json
  expect: $.score >= 60
  → CC 输出 {"score": 85} → 85 >= 60 → PASS
```

### 6b. postcondition 失败

```
P2 START [A.c] → CC 执行成功
  postcondition: cat .pipeline/result.json
  expect: $.score >= 60
  → CC 输出 {"score": 45} → 45 >= 60 → FAIL
  → retry 或 on_failure
```

**终端输出（默认模式，不需要 -v）：**
```
[09:43:50] [auth] postcondition: cat .pipeline/result.json
[09:43:50] [auth] postcondition FAIL: Condition failed: $.score >= 60
[09:43:50] [auth]   stdout: {"score": 45}
```

### 6c. postcondition 超时

```
P2 START [A.c] → CC 执行成功
  postcondition: make test
  → make test 卡住超过 timeout（默认 300s）
  → 不崩溃，返回 FAIL（reason: Shell timed out）
  → retry 或 on_failure
```

---

## 7. 4 层错误处理

| 层 | 触发条件 | 行为 | 消耗 retry？ |
|---|---------|------|:---:|
| 1 | Rate limit (429) | 等待 30s → 免费重试（最多 3 次） | ❌ |
| 2 | CC 崩溃 (exit≠0) / 超时 / 零工作 | 跳过 postcondition → retry | ✅ |
| 3 | CC 成功但 postcondition 失败 | retry 或 on_failure | ✅ |
| 4 | retry + on_failure 全部用完 | module 标记 failed | — |

```
P2 [A.c] → 429 Rate Limit → ⏳ 等待 30s（免费重试，不消耗预算）
P2 [A.c] → 429 Rate Limit → ⏳ 等待 30s（免费重试）
P2 [A.c] → 429 Rate Limit → ⏳ 等待 30s（免费重试）
P2 [A.c] → 429 持续 → 转为 CC_FAILED，开始消耗 retry 预算
P2 [A.c] RETRY → CC 崩溃 (exit 1) → ⚠️ RETRY (消耗预算)
P2 [A.c] RETRY → CC 成功 → postcondition PASS → ✅
```

---

## 8. resume 断点续传

### 8a. module 级跳过

```
第一次运行：
  auth → PASS
  crypto → FAIL

resume：
  Skipping passed: ['auth']
  Resuming: ['crypto']
```

### 8b. step 级跳过（含 per_file 粒度）

```
第一次运行：
  P1 → PASS                    → state.json 记录 "P1"
  P2 [A.c] → PASS              → state.json 记录 "P2/A.c"
  P2 [B.c] → PASS              → state.json 记录 "P2/B.c"
  P2 [C.c] → FAIL              → 不记录

resume：
  ⏭️ Resume: skipping 3 completed step(s): ['P1', 'P2/A.c', 'P2/B.c']
  ♻️ Resume: reusing existing worktree 'auth'    ← 复用 worktree，产物保留

  P2 [C.c] → 重跑（A.c 和 B.c 的产物还在 worktree 里）
  P3 [A.c] [B.c] [C.c] → 全部执行
  P4 → 执行
```

**关键：**
- worktree 被复用——之前 step 的产出文件（如 test_A.c、test_B.c）保留
- 如果 worktree 目录不存在但 branch 存在 → 从 branch 重建 worktree（代码保留）
- 如果都不存在 → 从 base_branch 新建（干净开始）

### 8c. resume --dry-run

```
$ cc-pipeline resume config.yaml --run-dir X --dry-run

📊 Resume Preview (dry-run)

  Module: auth — skip 3 completed step(s): ['P1', 'P2/A.c', 'P2/B.c']
  Module: crypto — no completed steps, will run all

  Modules to run: ['auth', 'crypto']
  ✅ Run without --dry-run to execute resume.
```

---

## 9. merge 行为

### 9a. auto_merge: false（默认）

```
P4 → PASS
📁 Worktree preserved at /path/worktrees/auth
   Branch: cc-auto/auth
   Manual merge: git checkout main && git merge --squash cc-auto/auth
```

用户自己 merge。

### 9b. auto_merge: true

```
P4 → PASS
🔀 Merged cc-auto/auth → main
```

框架自动 squash merge + commit message 模板。

### 9c. auto_merge: true + 冲突

```
P4 → PASS
  git merge --squash cc-auto/auth → 冲突
```

**如果 auto_resolve_conflicts: false（默认）：**
```
⚠️ Merge conflict — worktree preserved
   Manual merge: git checkout main && git merge cc-auto/auth
```

**如果 auto_resolve_conflicts: true：**
```
🤖 Attempting AI conflict resolution for: ['Makefile', 'tests/common.h']
✅ AI resolved all conflicts
🔀 Merged cc-auto/auth → main
```

AI 解冲突后用 postcondition 验证。解不了回退人工。

### 9d. commit message 模板

```yaml
commit_message: "feat({module}): add unit tests"
```

squash merge 后的 commit：
```
feat(auth): add unit tests
```

支持 `{module}` 变量。默认：`feat({module}): auto-generated by cc-pipeline`。

---

## 10. 并发模块

**配置：** `concurrency: 3, modules: [auth, crypto, net]`

```
🌙 cc-pipeline 0.3.2
   concurrency=3  modules=['auth', 'crypto', 'net']

  [09:43:50] [auth]   P1   START
  [09:43:50] [crypto] P1   START       ← 3 个模块并行
  [09:43:50] [net]    P1   START

  [09:44:20] [auth]   P1   PASS
  [09:44:30] [crypto] P1   PASS
  [09:44:45] [net]    P1   FAIL

  ...
  ✅ auth     passed
  ✅ crypto   passed
  ✗ net       failed
```

**关键：**
- 每个模块在独立 worktree 中运行，互不影响
- merge 操作有 `_merge_lock` 串行化——不会并发操作同一个 repo
- 一个模块失败不影响其他模块继续运行

---

## 11. timeout 超时

**配置：**
```yaml
- id: P2
  timeout: 30
```

| executor | timeout 生效？ | 超时行为 |
|----------|:---:|---------|
| shell | ✅ | 30s 后杀进程 → ExecOutcome.TIMEOUT → retry |
| claude-code | ✅ | 30s 后杀进程 → ExecOutcome.TIMEOUT → retry |

```
P2 [B.c] START → CC 执行 30s 超时
  ⚠️ RETRY (attempt 1) — timeout: CC timeout
P2 [B.c] RETRY → CC 执行成功 → PASS
```

**per_file 场景：一个文件超时不阻塞其他文件**
```
P2 [A.c] → PASS
P2 [B.c] → 超时 → retry → 超时 → FAIL
  ✗ module failed（C.c 不再执行）
```

---

## 12. output 文件隔离

**配置：**
```yaml
- id: P3
  loop: per_file
  output: "eval-{file}.json"
```

**行为：**
```
P3 [A.c] → CC 写入 .pipeline/eval-A.c.json
P3 [B.c] → CC 写入 .pipeline/eval-B.c.json
P3 [C.c] → CC 写入 .pipeline/eval-C.c.json
```

每个文件独立的 output 文件。postcondition 检查各自的文件，不会因为 A.c 的残留文件误判 B.c 通过。

---

## 13. 变量注入

| 变量 | 来源 | per_file 可用？ |
|------|------|:---:|
| `{module}` | module.name | ✅ 总是 |
| `{source_dir}` | module.source_dir | ✅ 总是 |
| `{source_files}` | 文件列表（逗号分隔） | ✅ 总是 |
| `{output}` | step.output | ✅ 总是 |
| `{file}` | loop 当前文件 | ⚠️ 仅 loop:per_file |
| `{assert_macro}` | source_files dict | ⚠️ 仅 loop:per_file |
| 自定义变量 | source_files dict 的 key | ⚠️ 仅 loop:per_file |

**{file} 无 loop 警告：**
```
P1 prompt 使用了 {file}，但 step 没有配 loop: per_file
→ Warning: Step 'P1': prompt uses {file} but step has no loop
→ {file} 保留原样不展开
```

---

## 14. snippets 公共片段

**配置：**
```yaml
snippets:
  build: |
    使用 subagent 执行编译。
    cd {source_dir} && make test
  dtest: |
    断言宏：CHECK（dtest 框架）

pipeline:
  - id: P2
    prompt: |
      为 {file} 生成测试。
      {{snippet:dtest}}
      {{snippet:build}}
```

**行为：** `{{snippet:name}}` 在 prompt 任意位置展开为 snippet 内容。未定义的 snippet → warn。

**prompt_prefix vs snippets：**
- `prompt_prefix`：全局，所有 step 自动拼接到开头
- `snippets`：按需，`{{snippet:name}}` 在 prompt 任意位置引用

---

## 15. 三级 verbose

| 级别 | 参数 | 输出内容 |
|------|------|---------|
| 0（默认） | 无 | 启动横幅 + postcondition 命令/结果 + 失败详情 + 模块汇总 |
| 1 | `-v` | + 每步 START/PASS 时间戳 + retry/jump 事件 |
| 2 | `-vv` | + 完整 prompt 逐行 + shell 命令 + CC 输出摘要 |

**默认模式（无 -v）始终可见：**
```
🌙 cc-pipeline 0.3.2
   concurrency=1  modules=['auth']

  [09:43:50] [auth] postcondition: test -f tests/test_A.c
  [09:44:20] [auth] postcondition: test -f tests/test_B.c
  [09:44:50] [auth] postcondition FAIL: Condition failed: $.score >= 60
  [09:44:50] [auth]   stdout: {"score": 45}

  ❌ Shell failed (exit 1): make test
     │ undefined reference to 'foo'

  ✅ auth     passed  (4 steps, 3 files)
```

---

## 16. 配置校验

### 16a. 拼写检测（三级）

```yaml
# 全局拼错
concurency: 3
→ warn: Unknown global field 'concurency' — did you mean 'concurrency'?

# 模块拼错
modules:
  - name: auth
    sorce_dir: src/
→ warn: Unknown field 'sorce_dir' — did you mean 'source_dir'?

# postcondition 拼错
postcondition:
  except: "$.score > 60"
→ error: postcondition has 'except' — did you mean 'expect'?
```

### 16b. init 生成可运行配置

```
$ cc-pipeline init
  模块列表: src/auth/          ← 输入路径
  ❌ 模块名不合法: ['src/auth/']（只能用字母、数字、下划线、连字符）
  模块列表: auth               ← 重新输入

  concurrency: pytest tests/   ← 输入非数字
  ❌ concurrency 必须是正整数，收到: pytest tests/
  concurrency: 5               ← 重新输入
```

---

## 17. 错误信息可见性

| 场景 | 终端输出 |
|------|---------|
| shell 失败 | `❌ Shell failed (exit 1): make test` + stderr 末 5 行 |
| CC 超时 | `⚠️ RETRY — timeout: CC timeout` |
| postcondition 失败 | `postcondition FAIL: <reason>` + stdout 预览 |
| worktree 创建失败 | `git worktree add failed: stderr: <git error>` |
| merge 冲突 | `⚠️ Merge conflict — worktree preserved` + 手动 merge 命令 |
| rate limit | `⏳ RATE LIMIT (retry 1/3)` |
| retry | `⚠️ RETRY (attempt N) — <reason>` |
| on_failure jump | `↩️ JUMP: P3[C.c] → P2[C.c] (jump 1)` |
| module 失败 | `✗ auth failed — Step 'P2' failed after 3 attempts` |

**所有失败路径都打印到终端——不需要 -v。**

---

## 附录：快速行为对照表

| 我想... | 配置 | 行为 |
|---------|------|------|
| 重试失败的 step | `retry: 3` | CC 在当前代码上重跑，不回滚 |
| 失败后跳到另一步 | `on_failure: P2` | retry 用完后跳到 P2 |
| 不自动 merge | `auto_merge: false` | 留在 worktree，用户自己 merge |
| 自动 merge | `auto_merge: true` | squash merge + commit message |
| 每文件独立 output | `output: "eval-{file}.json"` | 每文件生成独立 JSON |
| resume 续跑 | `cc-pipeline resume config.yaml --run-dir X` | 跳过已完成步骤 |
| 只跑部分模块 | `--module auth,crypto` | 只执行指定模块 |
| 预览不执行 | `--dry-run` | 展示步骤 + 文件 + 估算调用数 |
| 看详细日志 | `-v` | START/PASS/RETRY/JUMP 带时间戳 |
| 看完整 prompt | `-vv` | + prompt 逐行 + CC 输出 |
| 共享编译说明 | `snippets: { build: ... }` | prompt 中 `{{snippet:build}}` 引用 |
| 全局上下文 | `prompt_prefix: ...` | 所有 step 自动拼接到开头 |
