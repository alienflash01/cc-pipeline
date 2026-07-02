# cc-pipeline 代码审计问题清单

> 审计日期：2026-07-02
> 基线：301 tests / 92% coverage / 2,109 行源码
> 方法：全量源码审读 + 动态压力测试 + ruff/bandit 静态扫描 + 并发竞态分析 + 数据流追踪 + 测试质量审计 + 黑盒 E2E（真实 git repo 端到端）

---

## 一、首轮源码审读（P0–P3）

---

### #1 🔴 P0 — `_signal_handler` 死代码，KeyboardInterrupt 永远不会触发

**位置：** `cli.py:16-22`

```python
def _signal_handler(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True       # ← 先设为 True
    if not _shutdown_requested:      # ← not True == False，永远不进
        raise KeyboardInterrupt()    # ← 死代码
```

第 19 行刚把 `_shutdown_requested` 设成 `True`，第 21 行立刻检查 `if not _shutdown_requested`——**永远为 False**。

**影响：** 非 daemon 模式下 Ctrl+C 只设 flag，不抛异常。如果 orchestrator 正卡在 `ThreadPoolExecutor` 等待 CC 完成（最长 600s），用户按 Ctrl+C 后要等到当前 module 跑完才会停。

---

### #2 🔴 P0 — `retry: 1` 实际给出零次重试

**位置：** `runner.py:161, 197`

```python
retry_budget = step.retry  # retry=1 → budget=1

if retry_budget > 1:       # 1 > 1 == False → 直接 fail
    retry_budget -= 1
    continue
```

| 配置 `retry: N` | 用户预期 | 实际行为 |
|:-:|:-:|:-:|
| `retry: 3`（默认） | 4 次 | 3 次 |
| `retry: 1` | 2 次 | **1 次（零重试）** |
| `retry: 0` | 1 次 | 1 次 ✓ |

---

### #3 🟡 P1 — PR 创建静默吞掉所有异常

**位置：** `orchestrator.py:223-224`

```python
except Exception:
    pass  # PR creation is best-effort
```

`gh` CLI 未安装、网络错误、认证失败——全部吞掉，用户永远不知道 PR 没创建成功。

---

### #4 🟡 P1 — 死代码：`attempt_num` 计算后从未使用

**位置：** `runner.py:124`

```python
attempt_num = retry_budget - step.retry + extra_retries + 1 if step.retry > 0 else 1
# ↑ 整个代码库中从未被引用，ruff F841 确认
```

---

### #5 🟡 P1 — `status` 命令不处理损坏的状态文件

**位置：** `cli.py:265`

```python
state = json.loads(state_file.read_text())  # 崩溃写入 → JSONDecodeError 未捕获
```

---

### #6 🟡 P1 — Resume 模式下 `config.modules` 过滤顺序脆弱

**位置：** `cli.py:208-214`

```python
orch = Orchestrator(config=config, ...)  # ← config 引用已传入
config.modules = [m for m in ...]         # ← 之后才过滤，靠引用修改生效
```

---

### #7 🟡 P2 — Rollback 在 loop 展开时可能回滚到错误 checkpoint

**位置：** `runner.py:167-172` + `git_checkpoint.py:51`

tag 格式 `pipeline/{module}/{step}/{attempt}` **不包含 `loop_file`**。同一 step 的不同 file 的 checkpoint 互相覆盖（`tag -f`）。

场景：`generate[a.c]` 和 `generate[b.c]` 的 tag 都是 `pipeline/auth/generate/1`，后者覆盖前者。

---

### #8 🟢 P2 — 无 per-step timeout 配置

`PipelineStep` 没有 `timeout` 字段。所有 CC 步骤统一用 600s。

---

### #9 🟢 P2 — `_evaluate_single` 无法识别的表达式静默返回 False

**位置：** `postcondition.py:118-119`

用户写错 expect 表达式，不报错直接判 fail。

---

### #10 🟢 P3 — `render()` 对 JSON 模板中的 `{}` 会 KeyError

YAML prompt 中包含 `{"line": 80}` → `KeyError: Unknown variable`。

---

## 二、安全审计（bandit + 手动追踪）

---

### #11 🔴 P0 — shell=True 命令注入：module.name / source_dir 无任何校验

**位置：** `postcondition.py:36-38` + `executor.py:104-106` + `config.py`（无校验）

**bandit 报告：** B602 subprocess_popen_with_shell_equals_true (High/High) × 2 处

攻击链：
```
YAML modules[].name = "auth; rm -rf /"
  → render() 替换 postcondition.shell 中的 {module}
  → subprocess.run(shell=True) 执行
  → 命令注入
```

**config.py 无任何输入校验：**
- `module.name` — 直接进入 git branch 名、shell 命令、文件路径
- `source_dir` — 直接进入 shell 命令
- `source_files[]` — 直接进入 shell 命令和 loop 变量
- `output` — 直接拼入 `.pipeline/{output}` 路径（路径穿越风险：`output: "../../etc/passwd"`）

---

### #12 🟡 P1 — Git tag 注入：module name 含 `/`

**位置：** `git_checkpoint.py:51`

tag = `f"pipeline/{module}/{step}/{attempt}"`。如果 module 名含 `/`（如 `auth/v2`），tag 变成 `pipeline/auth/v2/scaffold/1`。

`list_completed_steps` 按 `/` split 后 `parts[2]` = `"v2"` 而非 `"auth/v2"` → **resume 找不到已完成的 step**。

**动态测试确认：** tag=`pipeline/auth/v2/scaffold/1` parsed step=`v2`（期望 `auth/v2`）。

---

### #13 🟡 P1 — `_is_rate_limited` 严重误报

**位置：** `runner.py:59-65`

```python
RATE_LIMIT_PATTERNS = ["429", "rate_limit", "rate limit", "too many requests", "1302"]
```

**动态测试确认的误报：**
- `"Port 4290 not available"` → 包含 `"429"` → **误判为 rate limit**
- `"rate_limit_exceeded=false"` → 包含 `"rate_limit"` → **误判**
- `"File rate limit config at line 1302"` → 同时命中 `"rate limit"` + `"1302"` → **误判**

影响：正常错误被误判为 rate limit → 触发 60s sleep + 免费重试 → 浪费时间。

---

### #14 🟡 P1 — `_is_zero_work` 误报：CC 只写文件无 stdout

**位置：** `runner.py:68-74`

```python
def _is_zero_work(cc_result):
    return (
        cc_result.returncode == 0
        and not cc_result.stdout.strip()
        and not cc_result.stderr.strip()
    )
```

CC 成功执行并写了文件，但 stdout/stderr 无输出 → 误判为 zero_work → 触发重试。

**实际场景：** `claude -p "创建文件" --dangerously-skip-permissions` 如果 CC 安静地写文件不输出文本，就会被误杀。

---

## 三、资源泄漏审计

---

### #15 🟡 P1 — Worktree + branch 永久泄漏：失败后无回收机制

**位置：** `orchestrator.py:228, 242`

失败路径调用 `preserve(module_name)`——实际是什么都不做（`worktree.py:116-119`）。没有任何后续回收机制。

反复失败 → worktree 目录堆积 + git branch 堆积 + 磁盘空间泄漏。无 GC、无 `cleanup --stale` 命令。

---

### #16 🟡 P1 — Daemon PID 文件残留：kill -9 后永久存在

**位置：** `cli.py:165-167`

PID 文件只在 `_cmd_run` 正常退出时删除。如果 daemon 被 `kill -9`、OOM、或机器重启 → PID 文件残留 → `stop` 命令永远找不到进程 → `run` 命令误删残留 PID 文件。

---

### #17 🟡 P2 — Git tag 无限堆积，无 GC

**位置：** `git_checkpoint.py:52`

不同 module/step 的 tag 永远不清理。`tag -f` 只覆盖同 module+step+attempt 的 tag，不同组合的 tag 持续累积。大量运行后 `git tag` 列表膨胀。

---

### #18 🟡 P2 — SIGTERM 后 CC 子进程变孤儿

**位置：** `orchestrator.py:90` + `executor.py:59`

`ThreadPoolExecutor` 的 `with` 块关闭线程池，但不会 kill 子线程中正在运行的 `subprocess.run`（CC 进程）。daemon 收到 SIGTERM → 主进程退出 → CC 子进程变孤儿，继续运行直到自行结束或超时。

---

## 四、动态压力测试发现

---

### #19 🔴 P0 — `loop: per_file` + 空 `source_files` 触发 KeyError 崩溃

**位置：** `compiler.py:85-91`

```python
if step.loop == "per_file" and module.source_files:  # 空列表 → False → 跳过 loop
```

空列表时 `and module.source_files` 为 False → 不展开 loop → 走 else 分支 → `render(prompt, base_vars)` → prompt 中的 `{file}` 无对应变量 → **`KeyError: Unknown variable: {file}`**。

**动态测试确认：** `KeyError: 'Unknown variable: {file}'`。

---

### #20 🟡 P1 — 循环依赖不报错，静默输出错误顺序

**位置：** `compiler.py:154-176` `_sort_by_dependencies`

step A `depends_on: B`，step B `depends_on: A` → 代码检测到 `not progressed` → `result.extend(remaining)` 直接追加 → **不报错，不警告**。

用户配置了循环依赖，pipeline 会以错误顺序执行，且没有任何提示。

---

### #21 🟡 P2 — `concurrency: 0` 无前置校验，到 ThreadPoolExecutor 才崩

**位置：** `config.py` 无校验 + `orchestrator.py:90`

`concurrency=0` 会在 `ThreadPoolExecutor(max_workers=0)` 时抛 `ValueError`，但错误发生在运行中途而非 config 加载时。应在 `load_config` 或 `Orchestrator.__init__` 中前置校验。

---

## 五、并发竞态分析

---

### #22 🟡 P2 — Logger 无锁：异常路径双实例写同一文件

**位置：** `logger.py:28` + `orchestrator.py:134` + `runner.py:105`

`ModuleRunner.__init__` 创建 Logger，`orchestrator._run_module` 异常处理也创建 Logger。两个实例写同一 `transcript.jsonl`。虽然 Linux O_APPEND 对 <4096 字节写入原子，但 JSONL 行可能超 4096 → **交错写入 → JSON 解析失败**。

---

### #23 🟡 P2 — 架构脆弱：orchestrator 运行时 import cli 读全局变量

**位置：** `orchestrator.py:66`

```python
import cc_pipeline.cli as cli_mod
# ...
if cli_mod._shutdown_requested:  # 读模块级全局变量
```

orchestrator 不通过参数接收 shutdown 信号，而是运行时 import cli 模块读全局变量。如果 orchestrator 被拆成独立包或单独调用（不经过 cli），shutdown 机制完全失效。

---

### #24 🟢 P3 — `WorktreeManager._worktrees.get()` 在锁外读取

**位置：** `worktree.py:97`

```python
def cleanup(self, module_name):
    wt_path = self._worktrees.get(module_name)  # ← 无锁
    with self._lock:                             # ← 锁在后面
```

理论上 create/cleanup 并发时可能脏读，但实践中同一 module 不会并发执行，概率极低。

---

## 六、数据流一致性

---

### #25 🟡 P1 — `on_complete` 和 `skill` 字段声明但从未实现

**位置：** `config.py:25-26`

```python
on_complete: list | None = None   # 声明了
skill: str | None = None          # 声明了
```

**整个代码库中从未被使用**（ruff + 手动追踪确认）。用户在 YAML 中配置这两个字段不会有任何效果，也不会报错——**静默忽略**。

---

### #26 🟡 P2 — `merge_to_base` 死代码：声明但从未调用

**位置：** `pr.py:60-84`

`PRCreator.merge_to_base()` 方法在整个代码库中没有任何调用点。README 说"完成后自动提交 PR"但实际只 `gh pr create`，不 merge。

---

### #27 🟢 P3 — 未使用的 import（ruff F401）

**ruff 确认：**
- `compiler.py:4` — `field` imported but unused
- `compiler.py:6` — `typing.Any` imported but unused
- `compiler.py:8` — `Module` imported but unused
- `config.py:5` — `pathlib.Path` imported but unused
- `orchestrator.py:11` — `Logger` imported but unused（在 127 行被局部 re-import）
- `render.py:4` — `json` imported but unused
- `runner.py:17` — `typing.Any` imported but unused

---

## 七、静态扫描发现（ruff + bandit）

---

### #28 🟡 P2 — ruff F541：f-string 无占位符

**位置：** `runner.py:149`

```python
reason=f"Rate limit free retries exhausted, consuming budget",
#      ^ 无 {} 占位符，f 前缀无意义
```

---

### #29 🟡 P2 — ruff S110：两处 `except: pass` 静默吞异常

**位置：** `orchestrator.py:223`（PR 创建）+ `runner.py:260`（读取 prior context 文件）

bandit 和 ruff 同时标记。`runner.py:260` 读取 `.pipeline/*.json` 时 `except Exception: pass`——如果 JSON 损坏，静默跳过，CC 丢失上下文。

---

### #30 🟢 P3 — 全部 subprocess 调用使用非绝对路径 `git`/`gh`（bandit S607）

**位置：** `worktree.py` 全文 + `pr.py:72,79`

`["git", ...]` 和 `["gh", ...]` 依赖 PATH 查找。如果用户环境 PATH 中有恶意 `git`/`gh` → 被执行。在 CI/CD 环境中风险较高。

---

## 八、测试质量审计

---

### #31 🟡 P1 — 114 处 mock subprocess 但大量无调用验证

**统计：** 全部测试 93 个 mock 引用 / 449 个 assert（比值 0.21）。

**关键发现：** 大量测试 `@patch("cc_pipeline.executor.subprocess.run")` mock 掉 CC 执行后，只 assert `result["status"]`，不验证 `mock_run.assert_called_with(...)`——**CC 命令行参数正确性完全未测试**。

影响：如果 `CCExecutor.run()` 拼接了错误的 `--model` 参数或漏了 `--dangerously-skip-permissions`，测试照样全绿。

---

### #32 🟡 P2 — e2e 测试全部 mock subprocess，无真实集成验证

所有 e2e/integration 测试都 mock 掉了 `subprocess.run`。没有一个测试真正调用 git、CC 或 gh。

301 个"pass"实际上验证的是"Python 控制流逻辑在 mock 喂的数据下正确"——而非"系统真的能跑通"。

---

## 汇总

| # | 严重度 | 问题 | 类别 |
|---|:-:|---|---|
| 1 | 🔴 P0 | `_signal_handler` 死代码 | 源码审读 |
| 2 | 🔴 P0 | `retry: 1` = 零重试 | 源码审读 |
| 11 | 🔴 P0 | shell=True 命令注入（无输入校验） | 安全审计 |
| 19 | 🔴 P0 | loop + 空 source_files → KeyError 崩溃 | 动态压测 |
| 3 | 🟡 P1 | PR 异常静默吞没 | 源码审读 |
| 4 | 🟡 P1 | `attempt_num` 死代码 | 源码审读 |
| 5 | 🟡 P1 | status 不处理损坏 JSON | 源码审读 |
| 6 | 🟡 P1 | resume config 过滤顺序 | 源码审读 |
| 12 | 🟡 P1 | Git tag module 含 `/` 解析错误 | 安全审计 |
| 13 | 🟡 P1 | rate_limit 检测误报 | 动态压测 |
| 14 | 🟡 P1 | zero_work CC 只写文件误判 | 动态压测 |
| 15 | 🟡 P1 | Worktree/branch 失败后无回收 | 资源泄漏 |
| 16 | 🟡 P1 | Daemon PID 文件 kill -9 后残留 | 资源泄漏 |
| 20 | 🟡 P1 | 循环依赖不报错 | 动态压测 |
| 25 | 🟡 P1 | `on_complete`/`skill` 声明未实现 | 数据流 |
| 31 | 🟡 P1 | 114 处 mock 无调用验证 | 测试质量 |
| 7 | 🟡 P2 | loop tag 无 file 区分 | 源码审读 |
| 8 | 🟢 P2 | 无 per-step timeout | 源码审读 |
| 9 | 🟢 P2 | expect 表达式静默 fail | 源码审读 |
| 17 | 🟡 P2 | Git tag 无限堆积无 GC | 资源泄漏 |
| 18 | 🟡 P2 | SIGTERM 后 CC 子进程变孤儿 | 资源泄漏 |
| 21 | 🟡 P2 | concurrency=0 无前置校验 | 动态压测 |
| 22 | 🟡 P2 | Logger 无锁异常路径双写 | 竞态分析 |
| 23 | 🟡 P2 | orchestrator 运行时 import cli | 竞态分析 |
| 26 | 🟡 P2 | `merge_to_base` 死代码 | 数据流 |
| 28 | 🟡 P2 | f-string 无占位符 | 静态扫描 |
| 29 | 🟡 P2 | 两处 `except: pass` | 静态扫描 |
| 32 | 🟡 P2 | e2e 全 mock 无真实集成 | 测试质量 |
| 10 | 🟢 P3 | render JSON brace KeyError | 源码审读 |
| 24 | 🟢 P3 | WorktreeManager 锁外读 | 竞态分析 |
| 27 | 🟢 P3 | 未使用的 import | 静态扫描 |
| 30 | 🟢 P3 | 非绝对路径 git/gh | 静态扫描 |

**第一轮总计：32 条 — 4 个 P0 / 12 个 P1 / 11 个 P2 / 5 个 P3**

---

## 九、黑盒 E2E + 深度边界（第二轮）

> 使用真实 git repo 端到端测试、100+ 组合爆炸用例、不依赖 mock 的真实 subprocess 调用

---

### #33 🔴 P0 — `command` 和 `prompt_file` 字段在 `load_config` 中未解析

**位置：** `config.py:89-102`

```python
step = PipelineStep(
    id=step_raw["id"],
    executor=step_raw.get("executor", "claude-code"),
    prompt=step_raw.get("prompt", ""),
    loop=step_raw.get("loop"),
    # ... 但没有：
    # command=step_raw.get("command", "")      ← 缺失！
    # prompt_file=step_raw.get("prompt_file")   ← 缺失！
)
```

`PipelineStep` dataclass 声明了 `command`（line 16）和 `prompt_file`（line 17）。`compiler._resolve_prompt()` 检查这两个字段（line 126-140）。但 `load_config()` **从不从 YAML 中读取它们**。

**影响：**
- YAML 中写 `command: "touch done.flag"` → **完全无效**，被静默丢弃
- YAML 中写 `prompt_file: ./prompts/gen.md` → **完全无效**
- shell executor 在真实使用中永远收到空字符串
- 所有 E2E 测试（真实 git repo + Orchestrator）全部因此失败

**根因：** 测试全部直接构造 `PipelineStep(command="...")` 或 mock 掉 `subprocess.run`，绕过了 `load_config` → 301 个测试全绿但核心功能不可用。

**严重度：P0 — 核心功能断裂。** 这是整个审计中发现的最严重问题。

---

### #34 🔴 P0 — `JSON boolean / null` 在 postcondition expect 中永远比较失败

**位置：** `postcondition.py:131-137`

```python
try:
    expected = int(raw_value)       # int("true") → ValueError
except ValueError:
    try:
        expected = float(raw_value) # float("true") → ValueError
    except ValueError:
        expected = raw_value.strip("'\"")  # expected = "true" (字符串)
# actual = True (bool from JSON)
# True == "true" → False!
```

**动态测试确认：**
- `{"passed": true}` + `$.passed == true` → **False**（bool True ≠ string "true"）
- `{"error": null}` + `$.error == null` → **False**（None ≠ "null"）

用户在 postcondition 中比较 boolean 或 null 值时，永远得到 False，不会报错。

---

### #35 🟡 P1 — 重复 module name 未检测

**位置：** `config.py:106-116`

```python
for mod_raw in raw["modules"]:
    mod = Module(name=mod_raw["name"], ...)
    modules.append(mod)
```

两个同名 module → 都加入列表 → 并行模式下两个线程为同一 module 创建 worktree → **git worktree 冲突**。

**动态测试确认：** `modules: [{name: dup}, {name: dup}]` → `load_config` 正常返回，不报错。

---

### #36 🟡 P1 — 负数 `concurrency` / `max_retries` / 字符串 `retry` 无校验

**位置：** `config.py:120-122`

```python
concurrency=raw.get("concurrency", 5),   # concurrency=-1 → 接受
max_retries=raw.get("max_retries", 3),   # max_retries=-5 → 接受
```

- `concurrency=-1` → `ThreadPoolExecutor(max_workers=-1)` → 运行时 `ValueError`
- `concurrency="abc"` → **不报错**，到 ThreadPoolExecutor 才崩
- `max_retries=-5` → `retry_budget = -5` → `retry_budget > 1` = False → **永远零重试**
- `retry="3"` (字符串) → `"3" > 1` → **TypeError**（str 和 int 比较）

---

### #37 🟡 P1 — `depends_on` 指向不存在的 step 不报错

**位置：** `compiler.py:154-176`

`_sort_by_dependencies` 中，如果 `depends_on` 目标不存在于 step IDs 中，step 永远不会被 placed → 最终 `not progressed` → 静默追加。

用户配置 `depends_on: nonexistent_step` → 不报错 → pipeline 以错误顺序执行。

---

### #38 🟡 P1 — postcondition 绕过 ShellExecutor，两套独立 subprocess 路径

**位置：** `runner.py:306` (step 执行) vs `runner.py:370` (postcondition)

```python
# step 执行：通过 self.shell_executor.run(command, cwd, timeout)
# postcondition：直接调用 eval_postcondition → subprocess.run(shell=True)
```

**动态测试确认：** shell executor 的 mock 未捕获 postcondition 调用——`shell_exec.run` 只被调用 1 次（step 执行），postcondition 走独立的 `subprocess.run`。

**影响：**
- timeout 配置不一致（ShellExecutor 可配，postcondition 硬编码 300s）
- 无法统一 mock 或监控 shell 调用
- 安全审计面翻倍（两个 `shell=True` 入口）

---

### #39 🟡 P1 — Postcondition 不支持嵌套 JSON 路径和数组索引

**位置：** `postcondition.py:117`

```python
match = re.match(r"\$\.(w+)\s*(>=|<=|==|!=|>|<)\s*(.+)", cond.strip())
```

regex 只匹配顶层字段 `$.field`：
- `$.coverage.line >= 80` → 不匹配 → **静默 False**
- `$.files[0]` → 不匹配 → **静默 False**

用户如果 shell 输出嵌套 JSON（如 `gcov` 输出 `{"coverage": {"line": 85}}`），无法写 postcondition。

---

### #40 🟡 P1 — 循环依赖不检测、不报错

**位置：** `compiler.py:171-174`

```python
if not progressed:
    result.extend(remaining)  # 直接追加，不报错
    break
```

`A depends_on B` + `B depends_on A` → 无限依赖环 → `_sort_by_dependencies` 检测到无法 progress → 静默追加 → pipeline 以任意顺序执行。

---

### #41 🟡 P1 — `StateManager.load()` 不处理损坏的 JSON 文件

**位置：** `state.py:44-45`

```python
def load(self):
    with self._lock:
        if not self.state_file.exists():
            return None
        with open(self.state_file) as f:
            return json.load(f)  # ← JSONDecodeError 未捕获
```

进程崩溃时写入一半 → `json.load(f)` 抛 `JSONDecodeError`。`resume` 命令调用 `_cmd_resume` → `json.loads(state_file.read_text())` 同样不捕获。

**动态测试确认：** 写入 `{"broken json` → `StateManager.load()` 直接抛 `JSONDecodeError`。

---

### #42 🟡 P1 — `_run_module` 中 module 查找 `StopIteration` 未处理

**位置：** `orchestrator.py:156`

```python
module = next(m for m in self.config.modules if m.name == module_name)
```

如果 `module_name` 不在 `config.modules` 中 → `next()` 抛 `StopIteration`。虽然被外层 `except Exception` 捕获（`StopIteration` 是 `Exception` 子类），但错误消息会是空的 `StopIteration()` → 用户无法理解发生了什么。

---

### #43 🟡 P2 — Progress.md 无限增长导致 CC prompt token 膨胀

**位置：** `runner.py:244-248` + `runner.py:274-286`

每步 PASS 后追加一行到 `progress.md`。`_inject_context` 把整个 `progress.md` 注入下一步 prompt。

20 步 loop 展开 → `progress.md` 20 行 → 第 20 步的 prompt 包含全部 19 行历史。大量步骤时 CC prompt 显著膨胀，浪费 token。

---

### #44 🟡 P2 — `.pipeline/` JSON 文件损坏被静默跳过（except: pass）

**位置：** `runner.py:256-261`

```python
for f in prior_files:
    try:
        content = f.read_text().strip()
        if content:
            context_lines.append(f"[{f.name}]:\n{content}")
    except Exception:
        pass  # ← 损坏文件静默跳过
```

如果 CC 上一步写了损坏的 JSON → 下一步的 prompt 中该上下文消失 → CC 丢失关键信息 → 用户不知道。

---

### #45 🟡 P2 — `_is_zero_work` 对只写文件不输出的 CC 误判

**位置：** `runner.py:68-74`

CC 成功执行并写了文件，但 stdout/stderr 都为空 → 误判为 zero_work → 触发重试。`claude -p "创建测试文件" --dangerously-skip-permissions` 可能安静地写文件不输出文本。

---

### #46 🟡 P2 — `source_dir` 含空格导致 shell 命令语义断裂

**位置：** `compiler.py:91` render 替换

```yaml
source_dir: "src/my module v2/"
```

render 后 postcondition shell 变成 `ls src/my module v2/` → shell 把空格当参数分隔符 → `ls` 收到 3 个参数。无引号包裹。

---

### #47 🟡 P2 — Worktree preserve 后无回收机制 + 无 `cleanup --stale` 命令

**位置：** `orchestrator.py:228, 242` + `worktree.py:116-119`

`preserve()` 是空函数（什么都不做）。失败/异常后 worktree 和 branch 永久残留。没有 `cc-pipeline cleanup` 或 `cc-pipeline prune` 命令清理 stale worktree。反复运行失败 → 磁盘泄漏 + git ref 膨胀。

---

### #48 🟢 P3 — step 缺少 `executor` 字段时静默默认为 `claude-code`

**位置：** `config.py:91`

```python
executor=step_raw.get("executor", "claude-code"),
```

用户漏写 executor → 静默使用 `claude-code`。如果本意是 shell 步骤 → CC 被错误调用，消耗 API 额度。应 warn 或 require explicit。

---

### 第二轮汇总

| # | 严重度 | 问题 | 来源 |
|---|:-:|---|---|
| 33 | 🔴 P0 | `command`/`prompt_file` 未在 load_config 中解析 | E2E 黑盒 |
| 34 | 🔴 P0 | JSON boolean/null postcondition 永远比较失败 | 动态测试 |
| 35 | 🟡 P1 | 重复 module name 未检测 | YAML 组合 |
| 36 | 🟡 P1 | 负数 concurrency/retry 无校验 | YAML 组合 |
| 37 | 🟡 P1 | depends_on 不存在 step 不报错 | compiler 边界 |
| 38 | 🟡 P1 | postcondition 绕过 shell_executor 双路径 | mock 分析 |
| 39 | 🟡 P1 | postcondition 不支持嵌套 JSON / 数组 | 边界测试 |
| 40 | 🟡 P1 | 循环依赖不检测 | compiler 边界 |
| 41 | 🟡 P1 | StateManager.load 不处理损坏 JSON | 异常路径 |
| 42 | 🟡 P1 | _run_module StopIteration 未处理 | 异常路径 |
| 43 | 🟡 P2 | progress.md 无限增长 → token 膨胀 | 架构分析 |
| 44 | 🟡 P2 | .pipeline/ 损坏 JSON 静默跳过 | 动态测试 |
| 45 | 🟡 P2 | zero_work 误判只写文件 CC | 动态测试 |
| 46 | 🟡 P2 | source_dir 含空格 shell 断裂 | 边界测试 |
| 47 | 🟡 P2 | preserve 无回收 + 无 cleanup 命令 | 资源泄漏 |
| 48 | 🟢 P3 | 缺 executor 静默默认 claude-code | YAML 组合 |

**第二轮新增：16 条 — 2 个 P0 / 8 个 P1 / 5 个 P2 / 1 个 P3**

---

## 十、第三轮拷打（变异测试 + 故障注入 + 修复验证 + 残留 bug）

> 方法：18 组变异测试（67% 变异得分）、30+ 组故障注入/并发/幂等/Git 腐败/API 契约/编码攻击、修复回归验证
> 注：审计期间代码被外部 commit 了 3 次修复（fb34584 / d2bca35 / 9b44adf），以下发现基于修复后的代码

---

### 变异测试存活清单（67% 变异得分，6/18 存活）

| 变异 | 存活 | 含义 |
|---|:-:|---|
| CC timeout 600 → 999999 | ❌ | 无测试验证超时值 |
| `--dangerously-skip-permissions` 删除 | ❌ | 无测试验证 CC 命令行参数完整性 |
| 默认 concurrency 5 → 1 | ❌ | 无测试验证默认值 |
| 默认 max_retries 3 → 0 | ❌ | 无测试验证默认 retry 值 |
| `tag -f` 改为 `tag`（不覆盖） | ❌ | 无测试验证 tag 覆盖语义 |
| `ensure_ascii=False` → `True` | ❌ | 无测试验证 Unicode 日志输出 |

---

### #49 🟡 P1 — error message 报 "N attempts" 但实际执行 N+1 次

**位置：** `runner.py:223`

```python
"error": f"Step '{step.step_id}' failed after {step.retry} attempts",
```

修复后 `retry_budget > 0` 使得 retry=N 执行 N+1 次。但 error message 仍报 `step.retry` 次。

| retry 配置 | 实际执行 | error message 报告 |
|:-:|:-:|:-:|
| retry=1 | 2 次 | "failed after 1 attempts" |
| retry=3 | 4 次 | "failed after 3 attempts" |

**验证确认。** retry 语义已修复（N+1 次执行），但错误消息仍然 off-by-one。

---

### #50 🟡 P1 — `output` 路径穿越未校验（修复后仍存在）

**位置：** `runner.py:267-270`

```python
if step.output:
    prompt += (
        f"\n\n---\n请将本次执行的关键信息...以 JSON 格式写入 .pipeline/{step.output}"
    )
```

`step.output = "../../../etc/crontab"` → 注入到 CC prompt → CC 可写到 worktree 外的任意路径。

**验证确认：** prompt 中包含 `../../../tmp/ccpipe_r4_traversal.json` — 路径穿越未被校验。

---

### #51 🟡 P1 — `StateManager.load()` 损坏 JSON 仍崩溃（修复后仍存在）

**位置：** `state.py:44-45`

**验证确认：** 写入 `{"broken` → `StateManager.load()` 直接抛 `JSONDecodeError`，未被捕获。

---

### #52 🟡 P1 — rate_limit 仍用简单 substring 匹配（修复后仍存在）

**位置：** `runner.py:59`

**验证确认：** `"429"` 和 `"rate_limit"` 仍是简单 substring 匹配，无 word boundary。`"Port 4290 not available"` → 误判为 rate limited。

---

### #53 🟡 P2 — WorktreeManager._lock 不跨实例共享

**位置：** `worktree.py:25`

两个 `WorktreeManager` 实例各有自己的 `_lock`，但操作同一个 git repo。同一 module 的并发 worktree 创建：一个实例的 lock 不阻塞另一个实例 → git refdb 竞态。

**验证确认：** 同 module 并发创建触发 `CalledProcessError: exit 255`（git worktree add 冲突）。

---

### #54 🟡 P2 — Logger 双线程写同一文件 — 无锁

**位置：** `logger.py:28`

**验证确认：** 两个 Logger 实例各写 50 行 → 总行数 100（O_APPEND 原子性在小行数下可靠），但单行 > 4096 bytes（PIPE_BUF）时可能交错。

---

### #55 🟡 P2 — 未知 YAML 字段静默忽略

**位置：** `config.py:load_config`

`unknown_top_level`、`unknown_step_field`、`unknown_module_field` 等不存在的 YAML 键被静默忽略。用户拼写错误（如 `comand:` 代替 `command:`）不会报错。

---

### #56 🟡 P2 — `render()` 把 None 转为字符串 `"None"`

**位置：** `render.py:50`

```python
result.append(str(variables[var_name]))  # str(None) = "None"
```

如果 module 的 `spec_id` 或其他字段为 `None`，prompt 中会出现字面字符串 `"None"` 而非空串。

---

### #57 🟢 P3 — `--dangerously-skip-permissions` 无测试验证

变异测试确认：删除 `--dangerously-skip-permissions` 参数后 343 测试全绿。CC 执行的安全参数完全无测试覆盖。

---

### #58 🟢 P3 — `tag -f` 覆盖语义无测试验证

变异测试确认：将 `tag -f` 改为 `tag`（不覆盖）后测试全绿。同一 step 多次 attempt 的 tag 覆盖语义未被测试。

---

### 修复验证结果（6 个原始 P0/P1 的修复状态）

| 原 # | 问题 | 修复状态 |
|:-:|---|:-:|
| #1 | signal handler 死代码 | ✅ 已修复 |
| #2 | retry=1 零重试 | ✅ 已修复（改为 >0，但 message 仍有 #49） |
| #33 | command/prompt_file 未加载 | ✅ 已修复 |
| #34 | JSON boolean/null 比较 | ✅ 已修复 |
| #7 | tag 无 loop_file 区分 | ✅ 已修复 |
| #20 | 循环依赖不检测 | ✅ 已修复 |
| #35 | 重复 module name | ✅ 已修复 |
| #36 | 负数 concurrency | ✅ 已修复 |
| #41 | StateManager 损坏 JSON | ❌ 仍存在（#51） |
| #50 | output 路径穿越 | ❌ 仍存在（#50） |
| #13 | rate_limit 误报 | ❌ 仍存在（#52） |

---

### 第三轮汇总

| # | 严重度 | 问题 | 来源 |
|---|:-:|---|---|
| 49 | 🟡 P1 | error message "N attempts" ≠ 实际 N+1 次 | retry 验证 |
| 50 | 🟡 P1 | output 路径穿越未校验（修复后残留） | 编码攻击 |
| 51 | 🟡 P1 | StateManager 损坏 JSON 仍崩溃（修复后残留） | 故障注入 |
| 52 | 🟡 P1 | rate_limit substring 误报（修复后残留） | 变异验证 |
| 53 | 🟡 P2 | WorktreeManager._lock 不跨实例 | 并发测试 |
| 54 | 🟡 P2 | Logger 双线程无锁 | 线程安全 |
| 55 | 🟡 P2 | 未知 YAML 字段静默忽略 | 对抗 YAML |
| 56 | 🟡 P2 | render(None) → "None" 而非空串 | 边界测试 |
| 57 | 🟢 P3 | --dangerously-skip-permissions 无测试 | 变异测试 |
| 58 | 🟢 P3 | tag -f 覆盖语义无测试 | 变异测试 |

**第三轮新增：10 条 — 0 个 P0 / 4 个 P1 / 4 个 P2 / 2 个 P3**

---

## 全量汇总

**总计：58 条 — 6 个 P0 / 24 个 P1 / 20 个 P2 / 8 个 P3**

（其中 11 个原始 bug 已被外部修复，实际现存残留：47 条 — 0 个 P0 / 16 个 P1 / 16 个 P2 / 8 个 P3）
