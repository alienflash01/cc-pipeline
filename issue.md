# cc-pipeline 代码审计问题清单

> 审计日期：2026-07-02（第四轮 — 基于修复后代码）
> 基线：349 tests / 91% coverage / 2,109 行源码
> 方法：真实任务异常场景 E2E + 故障注入 + 二进制/编码攻击 + 并发竞态 + 数据流验证 + 资源泄漏 + 变异测试 + 混沌输入（model/timeout/YAML/git）

---

## 确认的 Bug

---

### #1 🟡 P1 — `subprocess.run(text=True)` 遇到二进制 stdout → `UnicodeDecodeError` 崩溃

**位置：** `postcondition.py:36-43` + `executor.py:104-111`

```python
# postcondition.py
result = subprocess.run(shell, shell=True, cwd=cwd, capture_output=True,
                        text=True, timeout=timeout)  # ← text=True + 二进制 = 崩溃

# executor.py ShellExecutor.run
result = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,
                        text=True, timeout=...)  # ← 同上
```

**复现：**
```bash
head -c 1024 /dev/urandom  # 二进制输出 → text=True 解码 → UnicodeDecodeError
```

**影响：** 如果 postcondition shell 命令输出二进制数据（如 `gcov` 输出、`cat` 二进制文件），整个 pipeline 崩溃，`UnicodeDecodeError` 未被 `runner._execute_step` 的 `except Exception` 捕获（因为在 postcondition `evaluate()` 函数内，不在 runner try/except 内）。

实际场景：CC 生成的测试运行 `cat coverage.gcda | xxd` → 二进制输出 → 崩溃。

---

### #2 🟡 P1 — `StateManager.update_module` / `set_run_id` 读损坏 JSON → `JSONDecodeError` 崩溃

**位置：** `state.py:57-61`（`update_module`）+ `state.py:73-77`（`set_run_id`）

```python
def update_module(self, module_name, **kwargs):
    with self._lock:
        if self.state_file.exists():
            with open(self.state_file) as f:
                state = json.load(f)  # ← 无 try/except！
```

`load()` 方法已修复（有 `try/except JSONDecodeError`），但 `update_module` 和 `set_run_id` 中同样读取 JSON 的代码**没有加 try/except**。

**复现：** 进程崩溃时写了一半的 state file → `update_module` 被调用 → `JSONDecodeError` 崩溃。

**影响：** orchestrator._run_module 在第二步调用 `state.update_module(module_name, status="running")`，如果 state file 被前一次崩溃损坏 → 整个 module 运行失败。

---

### #3 🟡 P1 — error message 报 "N attempts" 与实际调用次数严重不符（rate limit 场景）

**位置：** `runner.py:227`

```python
"error": f"Step '{step.step_id}' failed after {step.retry + 1} attempts",
```

**验证数据：**

| 配置 | 场景 | 实际 CC 调用次数 | error 报告 |
|:-:|---|:-:|:-:|
| retry=2 | 全部失败 | 3 | "3 attempts" ✓ |
| retry=2 | 持续 rate limit | **8** | "3 attempts" ❌ |
| retry=3 | 全部失败 | 4 | "4 attempts" ✓ |
| retry=3 | 持续 rate limit | **~13** | "4 attempts" ❌ |

rate limit 场景下：5 次 free retry + (retry+1) 次 budget 消耗 = 实际调用远多于 error 报告。

---

### #4 🟡 P2 — Orchestrator 直接写 transcript.jsonl 绕过 Logger（并发写入风险）

**位置：** `orchestrator.py:204-205` + `orchestrator.py:256-257`

```python
# resume_skip 写入
with open(Path(str(self.run_dir)) / module_name / "transcript.jsonl", "a") as _f:
    _f.write(_resume_json.dumps({...}) + "\n")

# pr_error 写入
with open(Path(str(self.run_dir)) / module_name / "transcript.jsonl", "a") as _pf:
    _pf.write(_pr_json.dumps({...}) + "\n")
```

`ModuleRunner.__init__` 创建 `Logger` 实例写同一文件。Orchestrator 在异常/PR 路径中直接 `open(...,"a")` 写同一文件——**两个写入者无锁**。

虽然 O_APPEND 在小行下原子，但 `json.dumps` 生成的行可能 > 4096 bytes (PIPE_BUF) → 交错写入 → JSON 解析失败。

---

### #5 🟡 P2 — rate_limit 5 次 free retry 总 sleep 300s — CI/CD 超时风险

**位置：** `runner.py:37-38`

```python
MAX_FREE_RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BACKOFF_SECS = 60    # 5 × 60 = 300s
```

持续 rate limit 时，单步阻塞 5 分钟（+ budget 重试时间）。CI/CD pipeline 或 daemon 模式下，用户可能设置 process timeout < 300s → 进程被杀。

---

### #6 🟡 P2 — checkpoint 无文件变更时 tag 指向旧 commit → rollback 语义错误

**位置：** `git_checkpoint.py:47-58`

```python
status = self._run_git(["status", "--porcelain"])
if status.stdout.strip():          # 无变更 → 跳过 commit
    self._run_git(["commit", ...])
# tag 仍创建在当前 HEAD（上一个 commit）
self._run_git(["tag", "-f", tag])
```

场景：step1 创建文件 → checkpoint。step2 CC 没产生新文件 → checkpoint。此时 step2 的 tag 指向 step1 的 commit。

rollback 到 step2 → 实际回滚到 step1 状态 → **丢失了 step1 的部分变更**（如果有多文件）。

---

### #7 🟢 P3 — git commit 失败（磁盘满/权限） → tag 仍创建在旧 HEAD

**位置：** `git_checkpoint.py:49-51`

```python
if status.stdout.strip():
    self._run_git(["commit", "-m", commit_msg])  # 失败 → 无 check=True
# 继续执行 tag 创建 ← commit 失败后仍创建 tag
```

`git commit` 的 `subprocess.run` 没有 `check=True` → commit 失败被静默忽略 → tag 创建在旧 commit → checkpoint 标记了一个实际未保存的状态。

---

## 变异测试存活清单（67% 变异得分，6/18 存活）

| 变异 | 存活 | 含义 |
|---|:-:|---|
| CC timeout 600 → 999999 | ❌ | 无测试验证超时值 |
| `--dangerously-skip-permissions` 删除 | ❌ | 无测试验证 CC 命令行参数完整性 |
| 默认 concurrency 5 → 1 | ❌ | 无测试验证默认值 |
| 默认 max_retries 3 → 0 | ❌ | 无测试验证默认 retry 值 |
| `tag -f` 改为 `tag`（不覆盖） | ❌ | 无测试验证 tag 覆盖语义 |
| `ensure_ascii=False` → `True` | ❌ | 无测试验证 Unicode 日志输出 |

---

## 汇总

| # | 严重度 | 问题 | 来源 |
|---|:-:|---|---|
| 1 | 🟡 P1 | subprocess.run(text=True) 二进制 stdout 崩溃 | 编码攻击 |
| 2 | 🟡 P1 | update_module/set_run_id 损坏 JSON 崩溃 | 故障注入 |
| 3 | 🟡 P1 | error message attempts 与实际不符（rate limit） | retry 验证 |
| 4 | 🟡 P2 | transcript 并发写入绕过 Logger | 架构分析 |
| 5 | 🟡 P2 | rate_limit 300s sleep → CI/CD 超时风险 | 边界分析 |
| 6 | 🟡 P2 | checkpoint 无变更 → tag 指向旧 commit | 真实场景 |
| 7 | 🟢 P3 | git commit 失败 → tag 仍创建 | 故障注入 |

---

## 混沌输入测试（model 注入 / timeout 极端 / source_files 穿越等）

---

### #8 🔴 P0 — `source_files` 含路径穿越 `../../../etc/passwd` → 直接展开到 prompt

**位置：** `compiler.py:91-92`

```python
for filename in module.source_files:    # ["../../../etc/passwd"]
    vars_with_file = {**base_vars, "file": filename}
    compiled.append(CompiledStep(
        rendered_prompt=render(self._resolve_prompt(step), vars_with_file),
        # ↑ prompt 变成 "cat ../../../etc/passwd"
```

**验证确认：** `source_files: ["../../../etc/passwd"]` → 编译后 `rendered_prompt` 直接包含路径穿越。

`load_config` 校验了 `module.name` 和 `step.output`，但 **`source_files` 无任何校验**。

---

### #9 🟡 P1 — `timeout=-1` 被 load_config 接受 → `subprocess.run(timeout=-1)` 崩溃

**位置：** `config.py:26`（`PipelineStep.timeout`）+ `config.py:load_config`（无校验）

```yaml
pipeline:
  - id: s1
    executor: claude-code
    prompt: p
    timeout: -1     # ← 被 load_config 接受
```

`subprocess.run(timeout=-1)` → `ValueError: timeout must be non-negative`，在 runner._execute_step 内被 `except Exception` 捕获 → `ExecOutcome.UNKNOWN_ERROR` → **消耗 retry budget 直到失败**。

用户看到 "unknown_error: timeout must be non-negative" — 完全无法理解。

`timeout=0` 同理：`subprocess.run(timeout=0)` 立即 `TimeoutExpired`。

---

### #10 🟡 P1 — `retry=999` / `concurrency=999` 无上限校验

**位置：** `config.py:153-156`

```python
if not isinstance(concurrency, int) or concurrency < 1:   # 无上限！
if not isinstance(max_retries, int) or max_retries < 0:   # 无上限！
```

- `max_retries=999` → CC 失败时重试 1000 次 × 600s timeout = **可能运行 166 小时**
- `concurrency=999` + 999 个 module → 999 个线程同时创建 worktree → **线程爆炸 + git refdb 锁冲突**

---

### #11 🟡 P1 — `expect` 不支持 `||` 操作符 — 静默判 fail

**位置：** `postcondition.py:95`

```python
conditions = [c.strip() for c in expect.split("&&")]  # ← 只 split &&
# "$.line >= 70 || $.line >= 80" → 作为一个条件
# regex: OP=">=" raw_value="70 || $.line >= 80"
# int("70 || ...") → fail → str="70 || ..."
# 75 >= "70 || ..." → TypeError → False
```

用户写 `$.line >= 70 || $.line >= 80` → **静默判 fail**，不报 "不支持 ||"。

---

### #12 🟡 P1 — git `index.lock` 残留 → checkpoint 静默完成但变更未保存

**位置：** `git_checkpoint.py:45-58`

```python
self._run_git(["add", "-A"])        # ← index.lock 存在 → 失败，无 check=True
status = self._run_git(["status"])   # ← 可能报 staged changes
self._run_git(["commit", ...])       # ← 失败
self._run_git(["tag", "-f", tag])    # ← 创建在旧 HEAD
```

场景：上一次 git 操作崩溃 → `index.lock` 残留 → 下一次 checkpoint → tag 标记了**未保存的状态** → rollback 到这个 tag → **丢失 CC 生成的代码**。

所有 `_run_git` 调用都没有 `check=True` → git 命令失败被完全静默。

---

### #13 🟡 P2 — model 含换行 + flag → CC argv 注入风险

**位置：** `executor.py:48-54`

```python
cmd = [self.claude_path, "-p", prompt, "--dangerously-skip-permissions"]
if self.model:
    cmd.extend(["--model", self.model])  # model="gpt-4\n--flag"
```

`subprocess.run(argv list)` 不经过 shell → 换行保留为参数的一部分。但 CC 内部如果对 `--model` 参数做换行分割或 eval → **额外 flag 注入**。

虽然 argv 模式比 shell 安全，但 model 值直接来自用户 YAML 配置，无任何校验（空格、换行、特殊字符全部接受）。

---

### #14 🟡 P2 — `source_dir` 空字符串 → shell 命令语义错误

**位置：** `compiler.py:72-78` render 替换

```yaml
modules:
  - name: m1
    source_dir: ""     # ← 空
```

postcondition `ls {source_dir}` → `ls ` → `ls` 列出当前目录（非目标目录）→ **postcondition 通过但验证了错误的路径**。

---

### #15 🟡 P2 — render 变量名含空格被接受

**位置：** `render.py:34,52`

```python
pattern = re.compile(r"\{([^}]+)\}")  # 匹配 {my var} → var_name="my var"
if var_name in variables:             # {"my var": "test"} → 匹配
```

`{my var}` 含空格的变量名被 render 接受。用户 typo（`{soucre_dir}` vs `{source_dir}`）不会报错如果碰巧有同名 key。

---

### #16 🟢 P3 — `model='   '` 纯空格 → truthy → 传 `--model "   "` 给 CC

**位置：** `executor.py:53`

```python
if self.model:    # "   " is truthy
    cmd.extend(["--model", self.model])
```

空格字符串是 truthy → CC 收到 `--model "   "` → CC 行为未定义。应 strip 后检查。

---

## 汇总

| # | 严重度 | 问题 | 来源 |
|---|:-:|---|---|
| 1 | 🟡 P1 | subprocess.run(text=True) 二进制 stdout 崩溃 | 编码攻击 |
| 2 | 🟡 P1 | update_module/set_run_id 损坏 JSON 崩溃 | 故障注入 |
| 3 | 🟡 P1 | error message attempts 与实际不符（rate limit） | retry 验证 |
| 4 | 🟡 P2 | transcript 并发写入绕过 Logger | 架构分析 |
| 5 | 🟡 P2 | rate_limit 300s sleep → CI/CD 超时风险 | 边界分析 |
| 6 | 🟡 P2 | checkpoint 无变更 → tag 指向旧 commit | 真实场景 |
| 7 | 🟢 P3 | git commit 失败 → tag 仍创建 | 故障注入 |
| 8 | 🔴 P0 | source_files 路径穿越 → prompt 直接展开 | 混沌输入 |
| 9 | 🟡 P1 | timeout=-1/0 被接受 → subprocess 崩溃 | 混沌输入 |
| 10 | 🟡 P1 | retry/concurrency 无上限校验 | 混沌输入 |
| 11 | 🟡 P1 | expect 不支持 \|\| → 静默 fail | 混沌输入 |
| 12 | 🟡 P1 | git index.lock → checkpoint 静默失败 | 混沌输入 |
| 13 | 🟡 P2 | model 含换行/flag → argv 注入风险 | model 注入 |
| 14 | 🟡 P2 | source_dir 空串 → shell 语义错误 | 混沌输入 |
| 15 | 🟡 P2 | render 变量名含空格被接受 | 混沌输入 |
| 16 | 🟢 P3 | model 纯空格 → 传给 CC | model 边界 |

**总计：16 条 — 1 个 P0 / 6 个 P1 / 6 个 P2 / 3 个 P3**
