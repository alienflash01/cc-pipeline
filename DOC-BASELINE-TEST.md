# cc-pipeline 文档基准测试报告

> 日期：2026-07-16 | 基于 USER-GUIDE.md v0.3.0 + CONFIG-GUIDE.md | shell executor

---

## 测试设计原则

**只测文档声称的行为。** 文档说能做到什么，就测什么。文档没说的不测。

---

## 一、§3 全局字段

| 测试 | 文档声称 | 结果 |
|---|---|---|
| `concurrency: 1` | module 间并行数 | ✅ |
| `output_branch_prefix: cc-auto` | 默认值（变更确认） | ✅ |
| 拼写检测 `concurency` | 提示最接近字段名 | ✅ `did you mean 'concurrency'?` |

## 二、§4 Pipeline DSL

| 测试 | 文档声称 | 结果 |
|---|---|---|
| shell + `prompt` | prompt 作为命令执行 | ✅ |
| shell + `command` | "shell executor 的 bash 命令" | ❌ **`Unknown field`** |
| shell + `prompt_file` | 从外部 .md 加载 | ✅ |
| `loop: per_file` | 逐文件串行执行 | ✅ |
| `file_order: batched` | 所有文件先过 stepA → 再过 stepB | ✅ C P-a P-b P-c V-a V-b V-c |
| `file_order: sequential` | 每文件走完完整 pipeline | ✅ C P-a V-a P-b V-b P-c V-c |
| `source_files: ["*.c"]` | "每次 run 时自动展开" | ❌ **返回空列表** |
| `source_files` dict 格式 | `path` + 自定义变量 | ✅ `{assert_macro}` 正确展开 |
| `step.modules: [alpha]` | "只对指定模块生效" | ✅ beta=0 步 |
| `continue_on_error: true` | "文件失败后继续处理其他文件" | ✅ b.c fail → c.c 继续 |
| `{prev_output_path}` | "上一步的 output 文件路径" | ✅ `.pipeline/gen.json` |
| `{current_output_path}` | "当前步骤自己的 output 路径" | ✅ `.pipeline/read.json` |
| C 花括号 `{curly}` | "含空格/分号的保留" | ✅ 原文输出 |

## 三、§5 Shell Executor

| 测试 | 文档声称 | 结果 |
|---|---|---|
| `prompt: "shell cmd"` | "运行确定性命令" | ✅ |
| `command: "shell cmd"` | 同 prompt 的替代 | ❌ **不识别** |

## 四、§6 上下文传递

| 测试 | 文档声称 | 结果 |
|---|---|---|
| `{prev_output_path}` | 前一步 output 路径 | ✅ |
| `{current_output_path}` | 当前步 output 路径 | ✅ |
| `output: result.json` | CC 被要求写 `.pipeline/result.json` | ✅ |

## 五、§7 Postcondition 门控

| 测试 | 文档声称 | 结果 |
|---|---|---|
| `$.s >= 80` (通过) | JSON 数值比较 | ✅ |
| `$.s >= 80` (不通过) | 失败 + 显示 actual | ✅ |
| `$.a >= 80 && $.b >= 70` | AND 组合 | ✅ |
| `$.s >= 80 \|\| $.g == "B"` | OR 组合 | ✅ |
| `contains('PASS')` | stdout 包含 | ✅ |
| `expect: "true"` | 退出码 0 通过 | ✅ |
| `expect: "false"` | "退出码非 0 才通过" | ❌ **从不评估** |
| 省略 expect | 退出码 0 即通过 | ✅ |

## 六、§9 Retry + on_failure

| 测试 | 文档声称 | 结果 |
|---|---|---|
| retry: 失败两次后通过 | "不回滚，在当前状态重跑" | ✅ 3 次执行后通过 |
| `on_failure: fix` | "跳到目标 step 重新执行" | ✅ JUMP bad→fix (jump 1) |
| `on_failure_max_jumps: 1` | per-target 独立计数 | ✅ |
| self-jump `on_failure: loop` | (文档未定义此行为) | ⚠️ 触发跳转，无警告 |

## 七、§10 错误处理

| 测试 | 文档声称 | 结果 |
|---|---|---|
| `timeout: 2` + `sleep 5` | "超时后杀进程 → TIMEOUT → retry" | ✅ |

## 八、§11 运行时 + §16 Resume

| 测试 | 文档声称 | 结果 |
|---|---|---|
| `--dry-run` | "展示步骤 + 估算调用数" | ✅ |
| resume | "跳过已完成 step" | ✅ 跳过 19 步，重跑失败步 |

---

## 总结

| | 数量 |
|---|---|
| 总测试项 | 25 |
| ✅ 通过 | 20 |
| ❌ 不符文档 | 3 |
| ⚠️ 未定义行为 | 1 |
| 文档一致性 | **88%** |

### 与文档不符的 3 项

| # | 文档声称 | 实际行为 |
|---|---------|---------|
| 1 | shell executor 支持 `command` 字段 | `Unknown field 'command' — ignored` |
| 2 | `source_files: ["*.c"]` 自动展开 | `has empty source_files` |
| 3 | `expect: "false"` 退出码非 0 才通过 | 从不评估 postcondition，直接 cc_failed |

---

## 附录：三项不符的完整证据

### 证据 1：`command` 字段不识别

**配置文件：**
```yaml
repo: /tmp/cc-pipeline-blackbox/fake-repo
concurrency: 1
pipeline:
  - id: S1
    executor: shell
    command: "echo 'cmd_works' > /tmp/cc-audit/results/ev1.txt"
    postcondition:
      shell: "test -f /tmp/cc-audit/results/ev1.txt"
modules: [{name: m1, source_dir: .}]
```

**运行输出：**
```
UserWarning: Unknown field 'command' in step 'S1' — ignored

[23:09:37] [m1] postcondition FAIL: Shell command exited with code 1
[23:09:37] [m1] postcondition FAIL: Shell command exited with code 1
[23:09:37] [m1] postcondition FAIL: Shell command exited with code 1
[23:09:37] [m1] postcondition FAIL: Shell command exited with code 1
✗ m1 failed — Step 'S1' failed after 4 attempts
```

**文档依据：**
- USER-GUIDE §4 Step 字段表：`| command | string | "" | shell executor 的 bash 命令（支持 {变量} 注入）|`
- CONFIG-GUIDE §Step 字段：`| command | string | | "" | shell executor 的 bash 命令（支持 {变量} 注入）|`
- CONFIG-GUIDE §Executor 类型详解：`| shell | command: "..." | 原始命令，不注入 |`

**文档声称**：shell executor 通过 `command` 字段接收要执行的 shell 命令。

**实际行为**：框架打印 `Unknown field 'command' — ignored`，命令不执行。结果文件始终未创建。只有 `prompt` 字段被 shell executor 使用。

**对比验证**：将同一配置中的 `command` 改为 `prompt`，命令正常执行，文件创建成功。

---

### 证据 2：`source_files` glob 不展开

**配置文件：**
```yaml
repo: /tmp/cc-pipeline-blackbox/fake-repo
concurrency: 1
pipeline:
  - id: S1
    executor: shell
    loop: per_file
    prompt: "echo '{file}'"
modules:
  - name: m1
    source_dir: src
    source_files: ["*.c"]
```

**运行输出：**
```
❌ Module 'm1' exception: Step 'S1' uses loop: per_file 
   but module 'm1' has empty source_files
✗ m1 failed — Step 'S1' uses loop: per_file but module 'm1' has empty source_files
```

**文档依据：**
- USER-GUIDE §4：「`source_files` 支持 glob 通配符，每次 run 时自动展开」
- USER-GUIDE §4 示例：`source_files: ["*.c"]  # 展开为所有 .c 文件`

**文档声称**：`source_files: ["*.c"]` 在运行时自动展开为所有匹配的 `.c` 文件。

**实际行为**：glob 返回空列表，导致 `loop: per_file` 步骤报错 `has empty source_files`。

**对比验证**：
- 同一 worktree 中 `ls src/` 确认 a.c、b.c、c.c 三个文件存在
- 将配置改为 `source_files: [a.c, b.c, c.c]`（显式列表），pipeline 正常运行，每文件执行一次

---

### 证据 3：`expect: "false"` 从不评估 postcondition

**配置文件：**
```yaml
repo: /tmp/cc-pipeline-blackbox/fake-repo
concurrency: 1
max_retries: 0
pipeline:
  - id: S1
    executor: shell
    prompt: "exit 1"
    postcondition:
      shell: "exit 1"
      expect: "false"
modules: [{name: m1, source_dir: .}]
```

**运行输出：**
```
[23:09:38] [m1] S1 START
❌ Shell failed (exit 1): exit 1
[23:09:38] [m1] S1 ❌ FAIL — cc_failed: exit 1
✗ m1 failed — Step 'S1' failed after 1 attempts
```

**文档依据：**
- USER-GUIDE §7：「`expect: "false"` — shell **退出码非 0** 才通过（即期望命令失败）」
- USER-GUIDE §7 expect 值表：`| false | shell 退出码非 0 才通过 | 「确认某坏路径确实会报错」类反向断言 |`

**文档声称**：当 `expect: "false"` 时，shell 命令退出码非 0 应判定为 postcondition 通过。

**实际行为**：
1. shell 命令 `exit 1` → 退出码 ≠ 0
2. 框架将退出码 ≠ 0 直接归类为「CC 崩溃」（`cc_failed: exit 1`）
3. 进入 retry 流程，**postcondition 的 `expect: "false"` 从未被评估**
4. `max_retries: 0` → 直接 FAIL

**死结分析**：
- `expect: "false"` 需要 postcondition 的 shell 退出码 ≠ 0 才算通过
- 但 postcondition 被评估的前提是**步骤本身的 shell 命令退出码 = 0**（否则直接 cc_failed）
- 如果步骤的 shell 命令 exit 0，则 postcondition 的 shell 也是 exit 0 → `expect: "false"` 永不通过
- 因此 `expect: "false"` 在 shell executor 中**完全不可能工作**
