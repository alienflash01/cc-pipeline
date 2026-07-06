# cc-pipeline 用户体验审计报告

> 审计视角：新用户第一次使用 → clone → 跑通 → 排查 → 推广给同事
> 审计日期：2026-07-05
> 审计基准：master 分支（commit 3aa66d3）
> 审计范围：`cli.py` / `config.py` / `runner.py` / `orchestrator.py` / `git_checkpoint.py` / `executor.py` + 全部 `docs/` + 全部 `examples/` + `scripts/install.sh`

---

## 总评分

| 维度 | 评分 | 一句话结论 |
|------|:----:|-----------|
| 1. 首次上手 | ⭐⭐⭐ 3/5 | 上手「 plumbing 」一流（install.sh / init / check / quickstart-shell），但「指路牌」坏了（指向坏的 simple.yaml，藏起了 init/check） |
| 2. 配置体验 | ⭐⭐⭐⭐ 4/5 | dry-run 与校验做得扎实；缺 postcondition/expect 的加载期校验，且 CONFIG-GUIDE 已过期 |
| 3. 运行体验 | ⭐⭐⭐ 3/5 | verbose 与收尾汇总清晰；但**默认模式全程无输出**、`stop` 误报成功——两个硬伤 |
| 4. 排查体验 | ⭐⭐⭐⭐ 4/5 | `transcript` 命令是亮点；唯一缺点是不截断会刷屏 |
| 5. 推广体验 | ⭐⭐⭐ 3/5 | 资产够强（quickstart-shell / HTML 报告 / 话术），但文档自相矛盾 + 坏示例拉低信任 |
| 6. 安全体验 | ⭐⭐⭐⭐ 4/5 | 机制扎实（worktree + checkpoint + rollback）；差的是透明度——`--dangerously-skip-permissions` 全程未提 |
| **综合** | **⭐⭐⭐½ 3.5/5** | **底子很好，被「最后一公里」的文档一致性与默认输出拖了后腿** |

---

## 1. 首次上手（Onboarding）⭐⭐⭐ 3/5

### 做得好的地方

- **`scripts/install.sh` 是真正的「一键」**：逐项检测 Python(≥3.10) / Git / Claude Code / gh / pip，彩色输出，处理 PEP 668（`--break-system-packages`），`--dev` 顺手跑测试。质量高于多数内部工具。
- **`cc-pipeline init` 是降门槛利器**（`cli.py:1153`）：交互式问答 → 生成 `config.yaml` + `prompts/` 目录，三种任务模板（UT/审查/自定义），用 `str.replace` 而非 `str.format` 巧妙保住 prompt 里的字面 `{var}`。
- **`cc-pipeline check` 是排查前置关**（`cli.py:1239`）：Python/Git/CC/git user.name/磁盘空间 + 配置加载/repo/branch/prompt_file/dry-run 编译，`N/M checks passed` 汇总，永远返回 0（advisory）。
- **`examples/quickstart-shell/`**：0 依赖、0 API key、0 成本，纯 shell 三步流水线，任何人 clone 就能跑通——这是最理想的「5 分钟体验」入口。
- **`examples/quickstart-cc/`**：自包含（含 `prompts/*.md` + `src/*.py`），prompt 里还带了反踩坑指令（"Do NOT create a virtual environment"），质量过关。

### 体验断点

**🔴 BP-1.1〔P1〕入门示例本身是坏的——`examples/simple.yaml` 硬编码了个人路径**
- 场景：新用户照 `README.md` / `install.sh` 的「下一步」打开 `examples/simple.yaml`，第一行就是 `repo: /mnt/e/02.workspace/co-demo`（另一台机器上的路径），`postcondition` 还写了 `/usr/bin/python3`。
- 影响：clone-and-run 第一秒就 `Repo directory not found`。第一印象 = 坏的。
- 修复：改成 `repo: .`（与 quickstart 一致），或直接删除 `simple.yaml`，把 README/install.sh 的「下一步」指向 `examples/quickstart-shell`。

**🟠 BP-1.2〔P1〕`init` 和 `check` 这两个上手命令，在所有用户文档里都找不到**
- 场景：新用户读 README → 没有 init/check；读 USER-GUIDE → 目录 18 章无 init/check，附录「CLI 命令速查」也漏了 init/check/transcript；读 CONFIG-GUIDE → 同样没有。它们**只在 `--help` 里出现**。
- 影响：项目专门为降门槛造了两个命令，却把它们藏在 `--help` 后面。读文档的用户永远不知道捷径存在，只能手写 YAML（README 的 Quick Start 就是让用户 heredoc 手写）。
- 修复：README「Quick Start」改成 `cc-pipeline init → cc-pipeline check → cc-pipeline run config.yaml --dry-run → cc-pipeline run config.yaml` 三步走；USER-GUIDE 补 init/check 章节；速查表补齐 9 个命令。

**🟡 BP-1.3〔P2〕README 前置条件没提 API token 配置**
- 场景：用户照 README 装完直接 `cc-pipeline run`，CC 调用失败，但 README 只说「Claude Code CLI 可选」，没提 `~/.claude/settings.json` 里要配 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`（这步只在 USER-GUIDE §1）。
- 修复：README 前置条件加一行 token 配置指引，或链接到 USER-GUIDE §1。

**🟡 BP-1.4〔P2〕`init --template` 是个「未实现」的死参数**
- 场景：`cc-pipeline init --template foo` 只打印 "Note: --template not yet supported" 然后继续走默认流程（`cli.py:1164`）。
- 影响：用户以为 template 生效了，实际被忽略。
- 修复：要么实现，要么从 argparse 里删掉，别让死参数上线。

---

## 2. 配置体验（Configuration）⭐⭐⭐⭐ 4/5

### 做得好的地方

- **`--dry-run` 输出堪称范本**（`cli.py:185`）：箱线表格渲染每模块文件清单、估算 CC 调用次数（`scaffold=1 + generate=N + ...`）、全局变量、最后一句「✅ Config valid」。0 成本就能让用户看清将要发生什么。
- **配置校验前置且友好**（`config.py`）：必填字段检查、executor 拼写建议（`difflib.get_close_matches` → "did you mean 'shell'?"）、`source_files` 类型错误时直接给出正确 YAML 写法、`on_failure` 目标存在性、`prompt_file` 存在性都在加载期拦截。
- **安全校验到位**：模块名 shell 注入防护、`output`/`source_files` 路径穿越防护、`model` 换行注入防护、数值上下界。
- **变量系统自解释性强**：`{module}`/`{file}`/`{source_dir}`/`{output}`/`{.pipeline/x.json}` + dict 任意 key 全部自动展开。

### 体验断点

**🟠 BP-2.1〔P1〕postcondition 的 shell 命令不校验「工具是否存在」**
- 场景：新用户写 `postcondition.shell: "check_coverage.sh {module} {file}"`，但本地没有 `check_coverage.sh`。dry-run 通过、check 通过（check 只验 prompt_file，不验 postcondition），跑到运行期 → `check_coverage.sh: command not found` → postcondition 退出码非 0 → 反复 retry 到耗尽 → 模块失败。用户看到 "failed" 却不知道是「工具没装」还是「覆盖率不达标」。
- 影响：这是新用户**最高频**的失败模式，且完全没有前置拦截。
- 修复：dry-run 加 `--check-postconditions` 选项，对每个 postcondition shell 跑一次（127/命令不存在 → 标红警告）；或在 `check` 命令里加一项「postcondition shell 可执行性」探测。

**🟠 BP-2.2〔P1〕`expect` 表达式语法错误到运行期才暴露**
- 场景：`expect: "$.line => 80"`（`=>` 拼错）或 `$.line >=`（漏值）。加载期不解析，跑到 postcondition 才抛异常，且异常信息对非作者不友好。
- 修复：在 `config.py` 加载期对 `expect` 做一次 dry-parse（正则/AST 校验操作符与操作数），语法错直接 ValueError 前置。

**🟡 BP-2.3〔P2〕两份配置文档互相打架，CONFIG-GUIDE 已过期到 v0.2 之前**
- 场景：CONFIG-GUIDE 仍把 `coverage:` 当现行字段（§Module 字段、§变量注入表），完全没有 deprecated 提示；而 USER-GUIDE §3 明确说 `coverage` 已删除、迁移到 `variables`。CONFIG-GUIDE 的 Step 字段表还缺 `model`/`timeout`/`on_failure`/`on_failure_max_jumps`/`output_prompt`；CLI 命令节缺 `stop`/`report`/`transcript`/`init`/`check`/`uninstall`/`--daemon`/`--verbose`/`--dry-run`（9 个命令只列了 3 个）。
- 影响：先读到 CONFIG-GUIDE 的用户会被带偏，拷出 `coverage:` 写法，触发 deprecated 警告后困惑。
- 修复：要么删 CONFIG-GUIDE（USER-GUIDE §3-4 已覆盖），要么同步到 v0.3。

**🟡 BP-2.4〔P2〕`coverage:` 在 README / CONFIG-GUIDE / VALUE-STRATEGY 三处示例里仍是「现行写法」**
- 场景：`README.md:136`、`CONFIG-GUIDE.md:203`、`VALUE-STRATEGY.md:103` 的示例 YAML 都用 `coverage: {line_threshold: 80, ...}`，没有迁移提示。
- 影响：用户抄示例 → 触发 deprecated 警告 → 以为配置有问题。
- 修复：三处示例统一改成 `variables: {line_threshold: 80, ...}`。

---

## 3. 运行体验（Runtime）⭐⭐⭐ 3/5

### 做得好的地方

- **verbose 模式（`-v`）干净专业**（`runner.py:148-278`）：`[HH:MM:SS] [module] step START/PASS/FAIL [file]`，rate-limit / retry / on_failure-jump 全部带时间戳实时打印。
- **收尾汇总到位**（`cli.py:442-454`）：每模块 ✓/✗、失败原因、`💡 cc-pipeline transcript --run-dir ... --module X` 一键排查提示、`run_id`。
- **CO 式 4 层错误处理**健壮且可观测：rate-limit 免费重试不耗预算、零工作检测、timeout 捕获、未知异常归类。

### 体验断点

**🔴 BP-3.1〔P0〕默认（非 verbose）模式全程无输出——已用 grep 证实**
- 场景：`cc-pipeline run config.yaml`（不带 `-v`）。grep 全 `src/` 找 `🌙` / `run_id=` / `cc-pipeline 0.` → **NONE FOUND**。也就是说 README.md:122-130 和 USER-GUIDE §2 展示的那段启动横幅：
  ```
  🌙 cc-pipeline 0.3.0
     run_id=2026-07-01T23-00-00  concurrency=3  model=auto (CC default)
     modules=['auth']
  ```
  **在代码里根本不存在**。`_cmd_run` 在非 verbose 模式下，从 `orch.run()` 调用到收尾汇总之间**一行都不打印**。
- 影响：CC 单次调用可能几分钟、N 模块并行可能更久。用户敲完回车，终端死寂，第一反应是「卡死了？」→ Ctrl+C 中断 → 又走 resume。这是**最伤体验的单点**。
- 修复：`_cmd_run` 在 `orch.run()` 前无条件打印启动横幅（run_id / concurrency / model / modules）；并发射模块级 start/finish 事件（哪怕非 verbose 也至少打 `✓ auth passed`）。同步把 README/USER-GUIDE 里那段假输出对齐成真输出。

**🟠 BP-3.2〔P1〕`status` 看不到 step 级进度**
- 场景：daemon/cron 长跑时，`status --run-id X` 只显示模块级 `running/passed/failed`（读 `orchestrator-state.json`）。一个 10 步模块会显示 `running` 很久，看不到当前在第几步。
- 影响：长跑时「进度不可见」，用户只能 `tail daemon.log` 或 `cat transcript.jsonl`。
- 修复：`status` 增补「当前 step / 已完成 steps」一栏（从 transcript 末尾事件或 state 里补 step 进度）。

**🟠 BP-3.3〔P1〕`stop` 会误报成功 + 提前删 PID 文件**
- 场景：`_cmd_stop`（`cli.py:609-649`）发 SIGTERM 后轮询 `os.kill(pid, 0)` **最多 30 秒**。但 daemon 此刻可能卡在一个 CC 调用里（默认 timeout 600s）。30 秒后轮询循环退出，打印 `Process {pid} stopped.` 并 `finally` 删掉 PID 文件——**而进程还活着**。下次 `stop` 报 `No PID file found`，用户以为早停了，实际 daemon 还在跑。
- 影响：停止语义不可信，可能造成「以为停了，结果跑了双份 / 抢同一 worktree」。
- 修复：30s 轮询结束后再 `os.kill(pid,0)` 复查一次，仍活着就老实告诉用户「still running after 30s, use --force」，且**不要删 PID 文件**。

**🟡 BP-3.4〔P2〕README 的 rate-limit 常量是错的**
- 场景：`README.md:89`/`:150` 写「前 5 次免费重试 + 60s backoff」，而 `runner.py:38-39` 与 USER-GUIDE §10 实际是 `MAX_FREE_RATE_LIMIT_RETRIES=3` / `RATE_LIMIT_BACKOFF_SECS=30`。
- 修复：README 改成 3 次 / 30s。

**🟡 BP-3.5〔P2〕daemon 仅限 Unix（`os.fork`），未在文档标注**
- 场景：`cli.py:378` 用 `os.fork()` + `os.setsid()`，原生 Windows 不可用（WSL/macOS/Linux 没问题）。
- 修复：README 前置条件标注「daemon 模式需 Unix-like 环境」。

**🟡 BP-3.6〔P2〕「优雅停止」的粒度被高估**
- 场景：USER-GUIDE §12 说「SIGTERM 后在当前 module 边界停止」。但 CC 子进程用 `start_new_session=True`（`executor.py:65`）启动，SIGTERM 不会传给正在跑的 `claude`。实际是「等当前 step 的 CC 调用自然结束（可能 600s），再在下一个 module 边界退出」。
- 修复：文档改述为「当前 step 完成后停止」，或在 shutdown 时给 CC 子进程组也发信号。

---

## 4. 排查体验（Debugging）⭐⭐⭐⭐ 4/5

### 做得好的地方

- **`cc-pipeline transcript` 是明星命令**（`cli.py:931`）：把 `transcript.jsonl` 渲染成人类可读——步骤头（时间戳+step+attempt+loop_file）、`[PROMPT]` 逐行 `│` 引用、CC stdout/stderr 箱线包裹、PASS/FAIL/RETRY/JUMP 带原因。定位「CC 这次到底收到什么、返回什么」的首选工具。
- **失败即给排查命令**：收尾汇总对每个失败模块直接打印 `💡 cc-pipeline transcript --run-dir ... --module X`。
- **异常零吞没**：`orchestrator.py:266-282` 把完整 traceback 写进 transcript（`module_exception` 事件），state 标 `status=error`，结果回传 `error` 字段。
- **失败 worktree 保留**供手动 `git log` / `git tag -l` 取证。
- **`report` 双格式**：Markdown（stdout + 落盘）和 HTML（含 Mermaid DAG + 可折叠 prompt）。

### 体验断点

**🟠 BP-4.1〔P1〕transcript 不截断，CC 长输出直接刷屏**
- 场景：`_cmd_transcript` 的 `cc_result` 分支（`cli.py:992-1006`）对 stdout/stderr **逐行全打**——`for line in stdout.splitlines(): print(...)`，没有上限。USER-GUIDE §16 却写着「stdout（前 15 行）」。CC 一次返回几百行很常见，终端瞬间被淹。
- 影响：排查体验从「清晰」变「灾难」，且文档与实现不符。
- 修复：默认截断到 N 行（如 15）+ `… (M more lines, see transcript.jsonl)` 脚注；加 `--full` 关闭截断。同步修正文档。

**🟡 BP-4.2〔P2〕`status` 无参时只列目录名，无成败摘要**
- 场景：`status` 列最近 10 个 run（时间戳目录名），但不告诉你哪个成功哪个失败，得逐个 `status --run-id X`。
- 修复：每行加 `8 passed, 2 failed` 摘要（读各 run 的 state 文件 head）。

**🟡 BP-4.3〔P2〕无 `--follow` / `--tail` 实时跟踪**
- 场景：cron/daemon 长跑时，用户反复敲 `transcript` / `status`。没有 `tail -f` 等价物。
- 修复：`transcript --follow` / `status --watch`（轮询 state + transcript 末行）。

**🟡 BP-4.4〔P2〕state 损坏时的提示没给下一步**
- 场景：`_cmd_status` 读到坏 state 时打印 `State file is corrupt or unreadable. Check transcripts manually.`（`cli.py:576`），但没说「该看哪个 transcript」。
- 修复：补一句 `💡 cc-pipeline transcript --run-dir <dir>`。

---

## 5. 推广体验（Adoption）⭐⭐⭐ 3/5

### 做得好的地方

- **`examples/quickstart-shell/`** 是推广利器：对方团队 clone 后 0 配置、0 API key、0 成本即可看到完整 pipeline / postcondition / depends_on 机制。比任何 PPT 都有说服力。
- **`examples/quickstart-cc/`** 自包含且 prompt 卫生良好，是「真跑一次」的最短路径。
- **HTML 报告**自包含、含 Mermaid DAG、可折叠 CC prompt——**可以直接发给老板/同事**，无需配套解释。
- **VALUE-STRATEGY.md** 有具体的向上/平级/向下三套话术 + 成功指标表 + 风险缓解表，实操性强。

### 体验断点

**🔴 BP-5.1〔P1〕对方团队照 README 走，第一步就踩坏示例**
- 同 **BP-1.1**：`examples/simple.yaml` 的 `/mnt/e/02.workspace/co-demo` 是审计者本机路径。推广时对方第一印象就是坏的。
- 修复：把所有「下一步」入口改成 quickstart-shell（保底）/ quickstart-cc（进阶）。

**🟠 BP-5.2〔P1〕文档自相矛盾，伤信任**
- 场景：对方团队里较真的工程师会注意到——测试数 README badge 写 225、README 正文写 259、USER-GUIDE 写 492；rate-limit README 写 5×/60s、代码是 3×/30s；`coverage:` 三处文档当现行、USER-GUIDE 说已删。
- 影响：发现一处对不上的工程师，会默认怀疑其余所有数字（包括「492 tests」「95% coverage」）。推广信任崩塌。
- 修复：统一所有数字（建议跑一次 `pytest --cov` 把真值写回 badge），统一 `coverage:` → `variables:`，统一 rate-limit 表述。

**🟠 BP-5.3〔P1〕最快的上手路径（init/check）在 README 里消失**
- 同 **BP-1.2**：推广时对方读 README，看不到 `cc-pipeline init` / `cc-pipeline check`，只能照 heredoc 手写 YAML。每多手写一行，流失率 +N%。
- 修复：README Quick Start 改成 init → check → dry-run → run。

**🟡 BP-5.4〔P2〕VALUE-STRATEGY 的示例用了过期字段和不一致默认值**
- 场景：`VALUE-STRATEGY.md:103` 用 `coverage:`；`output_branch_prefix: ut-auto`（CONFIG-GUIDE 默认值），而代码默认是 `cc-auto`（`config.py:64`）。
- 修复：示例改 `variables:`；统一分支前缀默认值表述。

**🟡 BP-5.5〔P2〕缺一份「对方团队接入 runbook」**
- 场景：VALUE-STRATEGY 是「为什么 + 话术」，不是「对方团队 clone 后 5 步跑通」的操作手册。
- 修复：补一份 `docs/ADOPT.md`：clone → install.sh → `cc-pipeline check` → `cc-pipeline init` → `--dry-run` → `run` → `report --format html`。

---

## 6. 安全体验（Safety）⭐⭐⭐⭐ 4/5

### 做得好的地方

- **worktree 物理隔离**：每模块独立 git worktree，用户主仓库**不被直接触碰**。成功清理、失败保留。
- **git checkpoint 链**（`git_checkpoint.py`）：每步成功后 `commit + tag`（`pipeline/{module}/{step}/{attempt}`），retry 时 `rollback_to_latest` 回到「上一步最后一次成功」而非 attempt=1，确保回到已验证状态。
- **rollback 保上下文**：`git reset --hard` + `git clean -fd --exclude=.pipeline/`，恢复源码但保留 `.pipeline/` 上下文。
- **dry-run 给信心**：跑前 0 成本预览，不创建 worktree、不调 CC。
- **配置层防注入**：模块名、`output`、`source_files`、`model` 都做了 shell/路径穿越防护。
- **judge 只读**：`allowedTools = ["Read", "Bash"]`（`runner.py:399`）。

### 体验断点

**🟠 BP-6.1〔P1〕CC 用 `--dangerously-skip-permissions` 调用，全程未在文档出现**
- 场景：`executor.py:50` 每次 CC 调用都加 `--dangerously-skip-permissions`，即 CC 在 worktree 内可执行任意 Bash、无需任何权限确认。文档（README §Three Executor Types、USER-GUIDE §5/§17、VALUE-STRATEGY 风险表）反复把安全表述为「worktree + checkpoint」，但**这个更强的真相一字未提**。
- 影响：安全敏感的采纳方（金融/车载/医疗）做 code review 时一定会翻 `executor.py`，发现这个未披露的「全权限」调用，会立刻质疑「还有什么没说」——信任受损。机制本身没问题（worktree 边界兜得住），问题在**不透明**。
- 修复：USER-GUIDE §5/§17 + README 明写「CC 以 `--dangerously-skip-permissions` 运行，可任意执行 worktree 内 Bash；安全性由 worktree 物理隔离 + git checkpoint/rollback 保证，主仓库不受影响」，并给「如何收紧」（如自建 wrapper 限制 allowedTools）。

**🟡 BP-6.2〔P2〕`git clean -fd` 只保留 `.pipeline/`**
- 场景：rollback 时 `--exclude=.pipeline/`，worktree 内**其他未跟踪文件**（如某步生成但未提交的报表）会被清掉。
- 修复：文档明示该行为；或支持 step 级 `preserve_paths` 配置。

**🟡 BP-6.3〔P2〕`check` 不警告 repo 有未提交改动**
- 场景：用户把 `repo:` 指向自己正在干活的工作区（而非干净 clone）。worktree 隔离下风险很低，但 base_branch 脏状态可能让 worktree 基线不一致。
- 修复：`check` 增一项「repo working tree clean?」advisory。

**🟡 BP-6.4〔P2〕`uninstall` 会删 `~/.cc-pipeline/runs`，可能波及在跑的 daemon**
- 场景：`_cmd_uninstall`（`cli.py:888`）`rmtree(default_runs)`。若有另一个 daemon 正在用该目录，其 state 目录被删。
- 修复：uninstall 前检测 PID 文件 / 活跃进程，提示先 stop。

---

## 如果只能做 3 件事

按「投入产出比 × 影响面」排序，先做这三件，整体体验能从 3.5 跳到 4.5：

### ① 修复默认模式的「全程沉默」〔BP-3.1，P0，~30 行代码〕
在 `_cmd_run` 的 `orch.run()` 前，无条件打印启动横幅（run_id / concurrency / model / modules 列表），并在每模块完成时打印 `✓/✗ module`。把 README/USER-GUIDE 里那段假输出对齐成真输出。
**这是当前最大的体验断点**——用户以为程序卡死。

### ② 修好「前门」〔BP-1.1 + BP-1.2 + BP-5.3，P1，~半天〕
- 删掉或修好 `examples/simple.yaml`（改 `repo: .`），把 README/install.sh 的「下一步」全指向 `examples/quickstart-shell`；
- README Quick Start 改成 `init → check → dry-run → run` 四步；
- USER-GUIDE 速查表 + CONFIG-GUIDE 补齐 `init/check/transcript/stop/report/uninstall/--daemon/--verbose/--dry-run`。
**让新用户 5 分钟内走通，而不是对着坏示例报错。**

### ③ 修 `stop` 误报 + 披露 `--dangerously-skip-permissions`〔BP-3.3 + BP-6.1，P1，~半天〕
- `_cmd_stop` 30s 轮询后复查存活，老实报告「still running, use --force」，别提前删 PID 文件；
- 在 README/USER-GUIDE 明写 CC 全权限运行 + 安全边界如何兜底。
**一修可用性、二修信任，都是低成本高收益。**

---

## 体验断点优先级排序表

| ID | 维度 | 优先级 | 断点 | 修复成本 | 影响 |
|----|------|:------:|------|:--------:|------|
| BP-3.1 | 运行 | **P0** | 默认模式全程无输出（横幅是假的） | 小（~30 行） | 最大化——所有用户首跑即中 |
| BP-1.1 | 上手 | **P1** | `simple.yaml` 硬编码个人路径 | 极小 | 入门第一秒坏印象 |
| BP-1.2 | 上手 | **P1** | `init`/`check` 在所有文档消失 | 小 | 上手捷径被藏 |
| BP-3.3 | 运行 | **P1** | `stop` 误报成功 + 提前删 PID | 小 | 停止语义不可信 |
| BP-5.2 | 推广 | **P1** | 文档自相矛盾（测试数/rate-limit/coverage） | 小 | 推广信任崩塌 |
| BP-6.1 | 安全 | **P1** | `--dangerously-skip-permissions` 未披露 | 极小（补文档） | 安全审查信任 |
| BP-2.1 | 配置 | **P1** | postcondition shell 不校验工具存在性 | 中 | 新用户最高频失败 |
| BP-2.2 | 配置 | **P1** | `expect` 语法错到运行期才暴露 | 中 | 排查成本高 |
| BP-3.2 | 运行 | **P1** | `status` 无 step 级进度 | 中 | 长跑进度不可见 |
| BP-4.1 | 排查 | **P1** | transcript 不截断刷屏 | 小 | 排查体验被毁 |
| BP-5.1 | 推广 | **P1** | （=BP-1.1）推广入口坏示例 | 极小 | 同 BP-1.1 |
| BP-5.3 | 推广 | **P1** | （=BP-1.2）README 无 init/check | 小 | 同 BP-1.2 |
| BP-2.3 | 配置 | P2 | CONFIG-GUIDE 过期 | 中 | 误导新用户 |
| BP-2.4 | 配置 | P2 | 三处示例仍用 `coverage:` | 极小 | deprecated 警告困惑 |
| BP-3.4 | 运行 | P2 | README rate-limit 常量错 | 极小 | 文档不准 |
| BP-3.5 | 运行 | P2 | daemon 仅 Unix 未标注 | 极小 | 跨平台预期 |
| BP-3.6 | 运行 | P2 | 「优雅停止」粒度被高估 | 小 | 文档不准 |
| BP-4.2 | 排查 | P2 | `status` 无成败摘要 | 小 | 多 run 难以分诊 |
| BP-4.3 | 排查 | P2 | 无 `--follow` 实时跟踪 | 中 | 长跑监控不便 |
| BP-4.4 | 排查 | P2 | state 损坏提示无下一步 | 极小 | 断头提示 |
| BP-5.4 | 推广 | P2 | VALUE-STRATEGY 用过期字段/默认值 | 极小 | 抄出问题 |
| BP-5.5 | 推广 | P2 | 缺「对方团队接入 runbook」 | 中 | 推广无操作手册 |
| BP-6.2 | 安全 | P2 | `clean -fd` 只保 `.pipeline/` | 小 | 未跟踪产物被清 |
| BP-6.3 | 安全 | P2 | `check` 不警告 repo 脏 | 小 | 基线一致性 |
| BP-6.4 | 安全 | P2 | `uninstall` 可能波及在跑 daemon | 小 | 极端场景 |
| BP-1.3 | 上手 | P2 | README 缺 API token 配置 | 极小 | CC 调用直接失败 |
| BP-1.4 | 上手 | P2 | `init --template` 死参数 | 极小 | 误导 |

---

## 附：审计方法与证据

本次审计实际通读的文件：

- **源码**：`cli.py`（1341 行全读）/ `config.py` / `runner.py` / `orchestrator.py` / `git_checkpoint.py` / `executor.py`
- **文档**：`README.md` / `docs/USER-GUIDE.md` / `docs/CONFIG-GUIDE.md` / `docs/VALUE-STRATEGY.md`
- **示例**：`examples/simple.yaml` / `examples/quickstart-cc/{config.yaml,run.sh,prompts/generate.md,src/math_utils.py}` / `examples/quickstart-shell/{config.yaml,run.sh}`（已 `ls` 确认 quickstart-cc 的 `prompts/` 与 `src/` 齐全）
- **脚本**：`scripts/install.sh`

关键结论的取证方式：

- **BP-3.1（无横幅）**：`grep -rn "🌙\|run_id=\|cc-pipeline 0\." src/` → 0 命中。
- **BP-3.4 / BP-5.2（常量/数字不一致）**：`grep` 跨 README + USER-GUIDE + CONFIG-GUIDE + `runner.py` 比对，确认 README 写 5×/60s、代码与 USER-GUIDE 为 3×/30s；测试数三处分别为 225 / 259 / 492。
- **BP-1.1（坏示例路径）**：直接读取 `examples/simple.yaml:3` 为 `/mnt/e/02.workspace/co-demo`。
- **BP-3.3（stop 误报）**：通读 `_cmd_stop`（`cli.py:609-649`），30s 轮询后无复查、`finally` 删 PID 文件。
- **BP-6.1（全权限）**：`executor.py:50` 确认 `--dangerously-skip-permissions`，`grep` 文档无提及。
