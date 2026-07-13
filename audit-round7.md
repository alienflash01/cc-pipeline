# cc-pipeline 深度工程审查（第 7 轮）

> 审查者视角：挑剔的软件工程专家，关注架构合理性、设计缺陷、隐藏的工程债
> 审查方法：通读全部 14 个源码模块（4257 行），逐函数审查设计决策
> 前置：第 1-6 轮审计已发现并修复 58+ 个 bug，本轮聚焦架构和设计层面

---

## 执行摘要

- 审查范围：14 个模块，4257 行源码
- 发现问题：13 个（P1: 4，P2: 6，P3: 3）
- 总体判断：**代码工程质量不错，但存在几个架构级的结构性问题**

---

## P1：架构级问题

### P1-1：`_merge_branch` 在 repo 主目录做 `git checkout`——破坏用户工作树

**位置：** `orchestrator.py:326`

```python
co_result = _sp.run(["git", "checkout", self.config.base_branch],
        cwd=repo, capture_output=True, text=True)
```

merge 操作直接在 `repo_path`（用户的真实工作目录）上执行 `git checkout`。这意味着：

1. 如果用户在 repo 里有未提交的修改，`git checkout` 会失败或者（在没有 `--` 的情况下）覆盖文件
2. 如果用户当前不在 `base_branch` 上，这一行会**切换用户的分支**
3. 多个 module 并行 merge 时，虽然 `_merge_lock` 序列化了，但每次 merge 都在用户的主工作树上 checkout 来回切——这非常危险

**正确做法：** merge 应该在一个临时的 worktree 或 bare clone 中操作，永远不要碰用户的主工作树。或者用 `git merge` 到 base_branch 而不 checkout（通过 `git checkout base_branch -- .` 或 `git update-ref`）。

**影响：** 用户在跑 pipeline 的时候不能同时在 repo 里做其他工作——他们的分支会被来回切换，未提交修改可能丢失。

---

### P1-2：`_detect_file_changes` 检测的是整个 worktree 的变更，不区分当前步骤

**位置：** `runner.py:503-514`

```python
def _detect_file_changes(self) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True,
        cwd=self.worktree_path,
    )
```

这在 postcondition 失败时被调用（`_print_postcondition_diag`），目的是告诉用户"CC 改了哪些文件"。但 `git status --porcelain` 返回的是 **worktree 从 base branch 以来的所有累积变更**，不是"当前这一步 CC 产生的变更"。

第 3 步失败时，你看到的是 step1+step2+step3 的所有文件变更混在一起。用户无法定位"到底是哪一步引入了问题"。

**正确做法：** 如果要做 per-step diff，需要在每步开始前 snapshot `git rev-parse HEAD`，结束后 `git diff <snapshot>..HEAD`。但当前代码没有 per-step commit（P1-2 of round 6 已经发现了），所以无法做 per-step diff。

**影响：** postcondition 失败诊断信息不准确，误导调试。

---

### P1-3：`CCExecutor` 硬编码 `--dangerously-skip-permissions`，用户无法关闭

**位置：** `executor.py:50`

```python
cmd = [
    self.claude_path,
    "-p", prompt,
    "--dangerously-skip-permissions",  # ← 硬编码
]
```

每个 CC 调用都跳过权限确认。这在开发环境下可以接受，但在以下场景是安全隐患：

1. 生产环境：CC 可能修改 `/etc` 或执行危险命令
2. 共享服务器：多个用户共享同一台机器
3. CI/CD：pipeline 可能拿到敏感文件

而且没有任何配置项可以关闭它。

**影响：** 无法限制 CC 的权限范围，不适合生产部署。

---

### P1-4：`StateManager` 每次写入都是 load-modify-save——并发下丢失更新

**位置：** `state.py:50-72`

每个写方法（`update_module`、`mark_step_completed`、`set_run_id`）都做：
```python
with self._lock:
    state = json.load(f)   # 1. 读
    state[...].update(...)  # 2. 改
    json.dump(state, f)     # 3. 写
```

`threading.Lock` 保护了同一进程内的并发。但：

1. **不是原子写入**——写到一半进程被 kill，JSON 文件损坏（Round 5 已发现）
2. **resume 路径创建了新的 StateManager 实例**（`orchestrator.py:187` 旧代码，当前已复用 `self.state_mgr`）——如果有人恢复旧模式，两个实例的锁不互斥
3. 每次写入都重新加载整个文件——高并发下（5 个 module 频繁 mark_step_completed），这是性能瓶颈

**正确做法：** 用 `tempfile + os.replace` 做原子写入，或用 SQLite。

---

## P2：设计缺陷

### P2-1：`runner._inject_context` 注入的是**文件名**不是**文件内容摘要**——token 浪费

**位置：** `runner.py:317-336`

```python
prior_files = sorted(pipeline_dir.glob("*.json"))[-3:]
for f in prior_files:
    content = f.read_text().strip()
    context_lines.append(f"[{f.name}]:\n{content}")
```

注入了完整的 JSON 文件内容到 CC prompt。但 CC 需要的是关键信息（"上一步创建了 `test_auth.py`"），不是 10KB 的 JSON 全文。

10KB cap 是不错的保护，但 10KB × 每步 = 大量 token 浪费在 cache_read 上。

**建议：** 只注入 JSON 的 key-level 摘要或前 N 行。

---

### P2-2：`on_failure` 跳转后不清理前序步骤的副作用

**位置：** `runner.py:254-278`

当 step3 失败、on_failure 跳回 step1 时：
- step1 和 step2 产生的文件还在 worktree 里
- step1 重新执行时，CC 看到的是**包含自己之前输出的 worktree**

这会导致 CC 可能：
- 认为"已经做过了"直接跳过
- 被之前的错误输出干扰
- 产生不一致的状态

没有 git rollback（Round 6 P1-2 已发现），跳回去等于"在脏状态上重跑"。

---

### P2-3：`orchestrator._try_ai_resolve_conflicts` 给 CC 整个 repo 的写权限

**位置：** `orchestrator.py:401-404`

```python
result = executor.run(
    prompt=prompt,
    cwd=repo,  # ← 用户的真实 repo
    allowed_tools=["Read", "Write", "Edit", "Bash"],  # ← 全权限
)
```

AI 冲突解决在用户的**主 repo 目录**运行，且 CC 拿到了 Bash 权限。它可以：
- 修改/删除 repo 中的任何文件（不只是冲突文件）
- 执行任意 shell 命令

应该限制在冲突文件上，且 cwd 应该是 worktree 不是 repo。

---

### P2-4：`render()` 的 `{.pipeline/...}` 文件引用不 sanitize 路径

**位置：** `render.py:46-52`

```python
if var_name.startswith(".pipeline/"):
    full_path = Path(base_dir) / var_name if base_dir else Path(var_name)
    if full_path.exists():
        result.append(full_path.read_text())
```

如果 prompt 中有 `{.pipeline/../../../etc/passwd}`，且文件存在，内容会被注入到 prompt 中。虽然 `.pipeline/` 前缀看起来限制了范围，但 `..` 可以突破。

---

### P2-5：`compiler._sort_by_dependencies` 算法是 O(n²)

**位置：** `compiler.py:270-304`

```python
while remaining:
    for i, step in enumerate(remaining):
        if step.depends_on is None or step.depends_on in placed_ids:
            result.append(step)
            remaining.pop(i)
            break
```

每次找到一个可放置的 step，就 `break` 然后重新遍历。对于 N 个步骤，最坏 O(n²)。步骤数少时不影响，但如果有人配了 50+ 步骤的 pipeline，编译阶段会明显变慢。

更重要的是：`remaining.pop(i)` 在遍历中修改列表——虽然 break 了所以不会出 bug，但代码模式不安全。

---

### P2-6：rate limit 检测用正则匹配 stderr——误报风险

**位置：** `runner.py:59-65`

```python
RATE_LIMIT_PATTERNS = [
    r"\b429\b",
    r"\brate[ _-]?limit\b",
    r"\btoo many requests\b",
    r"\b1302\b",
]
```

如果 CC 的 stderr 输出碰巧包含 "429" 或 "rate limit"（比如 CC 在分析一个关于 rate limiting 的代码），会被误判为 rate limited，触发 30s sleep + retry。这浪费时间且掩盖真正的问题。

---

## P3：代码质量

### P3-1：`orchestrator.__init__` 中 `__import__("threading").Lock()` 不规范

**位置：** `orchestrator.py:53`

```python
self._merge_lock = __import__("threading").Lock()
```

应该 `import threading` 在文件头，然后 `threading.Lock()`。`__import__` 是 hack 写法。

---

### P3-2：`orchestrator` 导入 `cc_pipeline.cli` 做全局 flag 检查

**位置：** `orchestrator.py:58-62, 96-101`

```python
import cc_pipeline.cli as _cli_mod
_cli_mod._shutdown_requested = False
```

orchestrator 反向依赖 cli 模块的全局变量。这是循环依赖的坏味道。shutdown flag 应该是 orchestrator 自己的实例属性，不应该通过 monkey-patching cli 模块来同步。

---

### P3-3：`postcondition.evaluate` 的 `timeout=300` 硬编码

**位置：** `postcondition.py:24`

```python
def evaluate(shell, expect, cwd, timeout=300):
```

而 `runner._check_postcondition` 调用时没有传 timeout：

```python
# runner.py:487
result = eval_postcondition(
    shell=shell, expect=expect, cwd=self.worktree_path,
)  # ← 没传 timeout
```

postcondition 的 shell 命令总是用 300s 超时，即使 step 配置了 `timeout: 30`。step timeout 只传给了 executor，没传给 postcondition。

---

## 问题汇总

| ID | 级别 | 模块 | 问题 |
|---|---|---|---|
| P1-1 | P1 | orchestrator.py:326 | merge 在用户主工作树上 git checkout |
| P1-2 | P1 | runner.py:503 | _detect_file_changes 检测累积变更不是当前步 |
| P1-3 | P1 | executor.py:50 | --dangerously-skip-permissions 硬编码 |
| P1-4 | P1 | state.py:50-72 | 非原子写入 + O(n) load-modify-save |
| P2-1 | P2 | runner.py:317 | context 注入全文不是摘要 |
| P2-2 | P2 | runner.py:254 | on_failure 跳转不清理副作用 |
| P2-3 | P2 | orchestrator.py:401 | AI 冲突解决权限过大 |
| P2-4 | P2 | render.py:46 | {.pipeline/..} 路径遍历 |
| P2-5 | P2 | compiler.py:270 | O(n²) 依赖排序 |
| P2-6 | P2 | runner.py:59 | rate limit 正则误报 |
| P3-1 | P3 | orchestrator.py:53 | __import__ hack |
| P3-2 | P3 | orchestrator.py:58 | 反向依赖 cli 全局变量 |
| P3-3 | P3 | postcondition.py:24 | timeout 硬编码不传 step.timeout |

---

## 架构级建议

### 1. 每步 git checkpoint（解决 P1-2, P2-2）

当前最大的结构性缺陷是没有 per-step git snapshot。这导致：
- postcondition 诊断不准确
- on_failure 回跳在脏状态上跑
- 无法 diff 两个步骤之间的变更

建议在每步成功后做 `git add -A && git commit`（到 worktree 分支）。这样：
- 失败时可以 `git diff HEAD~1` 看当前步骤做了什么
- on_failure 可以 `git reset --hard <checkpoint>` 回到目标步骤的干净状态

### 2. merge 隔离（解决 P1-1）

不要在用户 repo 上操作。用临时 worktree 或 bare clone 做 merge：
```
git worktree add /tmp/merge-tmp base_branch
cd /tmp/merge-tmp && git merge --squash module_branch
git commit && git push origin base_branch
git worktree remove /tmp/merge-tmp
```

### 3. 配置化权限（解决 P1-3）

```yaml
cc_flags:
  skip_permissions: false  # 用户可选
  allowed_tools: [Read, Write, Edit]  # 不给 Bash
```

---

## 追加发现（第二轮通读）

### P1-5：`_kill_cc_subprocesses` 用 `pkill -f "claude.*-p"` 杀进程——会误杀

**位置：** `cli.py:18-39`

```python
subprocess.run(["pkill", "--f", r"claude.*-p"], capture_output=True)
```

信号处理器里用 `pkill -f "claude.*-p"` 杀所有匹配的进程。问题是：

1. **会杀掉用户正在运行的其他 CC session**——如果用户同时在另一个终端跑 `claude -p`，Ctrl+C 这个 pipeline 会把那个也杀了
2. **pattern 太宽**——`claude.*-p` 匹配任何命令行包含 `claude` 且后面有 `-p` 的进程

**正确做法：** 记录自己启动的 CC 子进程 PID，只杀自己的。

---

### P1-6：`_merge_branch` 的 merge 操作没有原子性保证

**位置：** `orchestrator.py:324-369`

merge 流程是：
1. `git checkout base_branch`（line 326）
2. `git merge --squash branch`（line 335）
3. （如果冲突）AI 解决 or abort
4. `git commit`（line 359）

如果在 step 2 和 step 4 之间进程崩溃（OOM、SIGKILL、断电），用户的 repo 会处于：
- base_branch 已 checkout（用户原来的分支被切走了）
- squash merge 已 stage 但未 commit
- 工作树处于"一半 merge"状态

**这不是低概率事件**——AI 冲突解决（step 3）调 CC 可能要几十秒，期间任何中断都会留下半 merge 状态。

---

### P2-7：`cli._cmd_run` 的 daemon 模式用 `os.fork()`——不支持 Windows

**位置：** `cli.py:424`

```python
pid = os.fork()
```

`os.fork()` 在 Windows 上不存在。虽然 README 说"Linux/macOS only"，但 daemon 模式是一个功能，不应该在非 daemon 模式能跑的平台上悄悄不可用。

---

### P2-8：`render()` 对 `{.pipeline/...}` 引用的文件用 `read_text()` 不指定 encoding

**位置：** `render.py:50`

```python
result.append(full_path.read_text())
```

如果 `.pipeline/` 下的 JSON 文件包含非 UTF-8 字节（CC 可能输出 BOM 或其他编码），`read_text()` 会抛 `UnicodeDecodeError`。Round 5 审计已经发现过类似问题（`runner._inject_context` 的版本已经修了加 `errors="replace"`），但 `render.py` 这里没有修。

---

### P2-9：`config.load_config` 的 step.model 不校验换行符

**位置：** `config.py:168`

```python
model=step_raw.get("model", ""),
```

全局 `model` 字段校验了换行符（line 312-314），但步骤级 `model` 没有这个校验。用户可以在步骤级 model 中注入换行符。Round 5 已发现，Round 6 确认仍然存在——**3 轮审计都发现了但没修**。

---

### P2-10：`orchestrator._run_module` 异常后 worktree 被 preserve，但没有清理 `.pipeline/`

**位置：** `orchestrator.py:287-298`

当 module 异常时：
```python
if wt_path:
    self.worktree_mgr.preserve(module_name)
```

worktree 被保留了。但 worktree 里的 `.pipeline/` 目录（包含 progress.md 和 context JSON 文件）也被保留。下次 resume 时，`_inject_context` 会读取这些**上次失败运行的残留 context**，可能误导 CC。

---

### P2-11：`compiler._sort_by_dependencies` 对 per_file 步骤的依赖排序是**按 step_id** 不是按 step_id+loop_file

**位置：** `compiler.py:275`

```python
all_ids = {s.step_id for s in steps}
```

per_file 展开后，同一个 step_id 有多个 CompiledStep（每个文件一个）。但 `all_ids` 是 step_id 的集合。`depends_on` 匹配的是 step_id，所以如果 step2 depends_on step1，则 step1 的**所有 per_file 实例**都被认为在 step2 前面。这通常是正确的——但如果有复杂的交叉依赖（step2/file_a depends_on step1/file_b），当前排序无法表达这种关系。

---

### P3-4：`report_html.build_dag_mermaid` 不处理 on_failure 边

**位置：** `report_html.py:56-101`

DAG 可视化只画 `depends_on` 边，不画 `on_failure` 回跳边。用户在 HTML 报告中看不到"失败时跳回哪一步"的关系。

---

### P3-5：`logger.log_cc_result` 截断 stdout 到 20000 字符但 transcript.jsonl 可能因此不完整

**位置：** `logger.py:59`

```python
stdout=(cc_result.stdout or "")[:20000],
```

如果用户需要从 transcript 中提取 CC 的完整输出（比如 CC 生成了一个大文件），20000 字符截断会导致数据不完整。至少应该在截断时标注 `[truncated, full size: N chars]`。

---

### P3-6：`config.load_config` 的校验顺序不合理——modules 先于 pipeline 步骤验证

**位置：** `config.py:141-212`

当前流程：
1. 先验证 modules 存在（line 141-143）
2. 再验证 pipeline 存在（line 145-147）
3. 再解析 pipeline steps（line 150-212）
4. 最后验证 modules 的安全字段（line 246-292）

如果 pipeline 和 modules 都有问题，用户先看到 modules 的错误，改完后再看到 pipeline 的。应该一次性收集所有错误。

---

## 问题汇总（累计 Round 7）

| ID | 级别 | 模块 | 问题 | 新发现？ |
|---|---|---|---|---|
| P1-1 | P1 | orchestrator.py:326 | merge 在用户主工作树上 git checkout | ✅ |
| P1-2 | P1 | runner.py:503 | _detect_file_changes 检测累积变更 | ✅ |
| P1-3 | P1 | executor.py:50 | --dangerously-skip-permissions 硬编码 | ✅ |
| P1-4 | P1 | state.py:50-72 | 非原子写入 | ✅ |
| **P1-5** | **P1** | **cli.py:18** | **pkill 误杀其他 CC 进程** | **✅ 新** |
| **P1-6** | **P1** | **orchestrator.py:324-369** | **merge 非原子性，崩溃留半 merge 状态** | **✅ 新** |
| P2-1 | P2 | runner.py:317 | context 注入全文 | ✅ |
| P2-2 | P2 | runner.py:254 | on_failure 不清理副作用 | ✅ |
| P2-3 | P2 | orchestrator.py:401 | AI 冲突解决权限过大 | ✅ |
| P2-4 | P2 | render.py:46 | {.pipeline/..} 路径遍历 | ✅ |
| P2-5 | P2 | compiler.py:270 | O(n²) 依赖排序 | ✅ |
| P2-6 | P2 | runner.py:59 | rate limit 正则误报 | ✅ |
| **P2-7** | **P2** | **cli.py:424** | **os.fork() 不支持 Windows** | **✅ 新** |
| **P2-8** | **P2** | **render.py:50** | **read_text() 不指定 encoding** | **✅ 新** |
| **P2-9** | **P2** | **config.py:168** | **step.model 换行符注入（3 轮未修）** | **复发** |
| **P2-10** | **P2** | **orchestrator.py:287** | **异常后 .pipeline/ 残留误导 resume** | **✅ 新** |
| **P2-11** | **P2** | **compiler.py:275** | **per_file 依赖排序粒度不够** | **✅ 新** |
| P3-1 | P3 | orchestrator.py:53 | __import__ hack | ✅ |
| P3-2 | P3 | orchestrator.py:58 | 反向依赖 cli 全局变量 | ✅ |
| P3-3 | P3 | postcondition.py:24 | timeout 硬编码 | ✅ |
| **P3-4** | **P3** | **report_html.py:56** | **DAG 不画 on_failure 边** | **✅ 新** |
| **P3-5** | **P3** | **logger.py:59** | **transcript 截断无标注** | **✅ 新** |
| **P3-6** | **P3** | **config.py:141** | **校验顺序不合理** | **✅ 新** |

**总计：22 个问题（P1: 6，P2: 11，P3: 6 — 含复发的 step.model 换行符）**

---

## 第三轮通读发现

### P1-7：`_cmd_stop` 用 `os.kill(pid, 0)` 检查进程存活——PID 复用导致误判

**位置：** `cli.py:729`

```python
os.kill(pid, 0)  # check if still alive
```

daemon 停止时，循环 30 次检查 PID 是否存活。问题：

1. **PID 复用**——如果 daemon 在 30 秒内退出，操作系统可能把这个 PID 分配给另一个进程。`os.kill(pid, 0)` 会返回成功（进程存在），但那已经不是你的 daemon 了
2. 结果：报告"still running after 30s"，让用户用 `--force`（SIGKILL）杀一个**完全不相关的进程**

**正确做法：** 检查 `/proc/<pid>/cmdline` 是否包含 `cc-pipeline`，或用 process group。

---

### P1-8：`_cmd_resume` 直接重跑 config.load_config——config 被修改后 resume 会出问题

**位置：** `cli.py:529`

```python
config = load_config(args.config)
```

resume 时重新加载 config.yaml。如果用户在两次运行之间修改了 config（加/删步骤、改 postcondition），resume 会用**新 config 的步骤列表**但**旧 run 的 completed_steps**。这导致：

1. 新增的步骤会被执行（正确）
2. 删除的步骤的完成记录还在 state.json 里（无害但脏）
3. **修改了 postcondition 的步骤不会被识别为"需要重跑"**——因为 step_id 没变，state 里标记为 completed，直接跳过

用户以为"改了 postcondition 再 resume 会重新验证"，实际上被跳过了。

---

### P2-12：`_cmd_status` 把 `orchestrator-state.json` 当 run 列出来

**位置：** `cli.py:646`

```python
runs = sorted(base.iterdir())
for r in runs[-10:]:
    print(f"  {r.name}")
```

直接 `iterdir()` 列出 run 目录下的所有条目，包括文件（如 `orchestrator-state.json`、`cc-pipeline.pid`）和目录（如 `worktrees`、模块目录）。这些不是 run，混在列表里让用户困惑。

UX 审计（之前的报告）已经发现了这个问题，但从代码层面看根因更清楚：status 命令没有区分"run 目录"和"run 目录内的文件"。

---

### P2-13：`_cmd_transcript` 不传 `--module` 时列出所有模块，但跨多次运行混在一起

**位置：** `cli.py:1044-1047`

```python
modules = sorted([
    d.name for d in run_dir.iterdir()
    if d.is_dir() and (d / "transcript.jsonl").exists()
])
```

`run_dir` 是 `~/.cc-pipeline/runs`（默认值）。多次运行的 transcript 都存在子目录中（按模块名命名）。不传 `--module` 时遍历所有子目录，结果是**跨多次运行的 transcript 混在一起**。

用户想看"最近一次运行的 mod1 日志"，但看到的是所有历史运行的 mod1 日志拼接。

---

### P2-14：`_cmd_init` 生成的 UT config 中 `source_files` 写死了 `example.c`

**位置：** `cli.py:1217`

```python
modules:
  - name: {first_module}
    source_dir: {source_dir}
    source_files:
      - path: example.c
        assert_macro: {assert_macro}
```

init 模板硬编码了 `example.c` 作为源文件。用户如果不是 C 项目（比如 Python），生成的 config 里的 `example.c` 毫无意义，而且 dry-run 不会报错（因为 source_files 只是注入到 prompt 的字符串）。

---

### P2-15：`_cmd_clean` 只清 worktree 不清分支和 `.pipeline/`

**位置：** `cli.py:1461`

clean 命令调用 `worktree_mgr.cleanup()` 或扫描 worktree 列表。但：
1. `cleanup()` 删 worktree + 分支，但**不删 `.pipeline/` 目录**
2. `preserve()` 的 worktree 完全不被 clean 清理
3. 多次运行后 `.pipeline/` 下的 context JSON 文件会堆积

---

### P3-7：`_cmd_check` 磁盘空间检查用 `Path.cwd()` 而非 repo 路径

**位置：** `cli.py:1401`

```python
du = shutil.disk_usage(str(Path.cwd()))
```

检查的是当前工作目录的磁盘空间，不是 `config.repo` 所在的磁盘。如果 repo 在另一个挂载点（比如 `/mnt/e`），当前目录在 `/home`，检查的是 `/home` 的空间而非 repo 所在分区。

---

### P3-8：`_cmd_report` 生成 HTML 报告时 Mermaid CDN 在离线环境不可用

**位置：** `report_html.py:270`

```python
parts.append(f'<script src="{_MERMAID_CSS}"></script>')
```

HTML 报告依赖 CDN 加载 Mermaid.js。在离线环境（如公司内网、断网情况）打开报告，DAG 图无法渲染。应该提供内联 fallback 或纯 CSS 替代方案。

---

## 最终问题汇总（Round 7 完整版）

| ID | 级别 | 模块 | 问题 |
|---|---|---|---|
| P1-1 | P1 | orchestrator.py:326 | merge 在用户主工作树上 git checkout |
| P1-2 | P1 | runner.py:503 | _detect_file_changes 检测累积变更 |
| P1-3 | P1 | executor.py:50 | --dangerously-skip-permissions 硬编码 |
| P1-4 | P1 | state.py:50-72 | 非原子写入 |
| P1-5 | P1 | cli.py:18 | pkill 误杀其他 CC 进程 |
| P1-6 | P1 | orchestrator.py:324-369 | merge 非原子性 |
| **P1-7** | **P1** | **cli.py:729** | **PID 复用导致 stop 误判** |
| **P1-8** | **P1** | **cli.py:529** | **resume 用新 config + 旧 state 不匹配** |
| P2-1 | P2 | runner.py:317 | context 注入全文 |
| P2-2 | P2 | runner.py:254 | on_failure 不清理副作用 |
| P2-3 | P2 | orchestrator.py:401 | AI 冲突解决权限过大 |
| P2-4 | P2 | render.py:46 | {.pipeline/..} 路径遍历 |
| P2-5 | P2 | compiler.py:270 | O(n²) 依赖排序 |
| P2-6 | P2 | runner.py:59 | rate limit 正则误报 |
| P2-7 | P2 | cli.py:424 | os.fork() 不支持 Windows |
| P2-8 | P2 | render.py:50 | read_text() 无 encoding |
| P2-9 | P2 | config.py:168 | step.model 换行符注入（3 轮未修） |
| P2-10 | P2 | orchestrator.py:287 | 异常后 .pipeline/ 残留 |
| P2-11 | P2 | compiler.py:275 | per_file 依赖排序粒度不够 |
| **P2-12** | **P2** | **cli.py:646** | **status 把文件当 run 列出** |
| **P2-13** | **P2** | **cli.py:1044** | **transcript 跨多次运行混合** |
| **P2-14** | **P2** | **cli.py:1217** | **init 模板 source_files 硬编码 example.c** |
| **P2-15** | **P2** | **cli.py:1461** | **clean 不清分支和 .pipeline/** |
| P3-1 | P3 | orchestrator.py:53 | __import__ hack |
| P3-2 | P3 | orchestrator.py:58 | 反向依赖 cli 全局变量 |
| P3-3 | P3 | postcondition.py:24 | timeout 硬编码 |
| P3-4 | P3 | report_html.py:56 | DAG 不画 on_failure 边 |
| P3-5 | P3 | logger.py:59 | transcript 截断无标注 |
| P3-6 | P3 | config.py:141 | 校验顺序不合理 |
| **P3-7** | **P3** | **cli.py:1401** | **磁盘检查用 cwd 非 repo 路径** |
| **P3-8** | **P3** | **report_html.py:270** | **Mermaid CDN 离线不可用** |

**Round 7 总计：30 个问题（P1: 8，P2: 15，P3: 8）**
