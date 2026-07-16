# cc-pipeline 黑盒测试报告（Round 2）

> 日期：2026-07-16 | 针对最新文档状态 | 纯 shell executor

---

## 测试用例

| ID | 用例 | 结果 | 关键发现 |
|---|---|---|---|
| BB-1 | `command` 字段是否执行 | ❌ FAIL |   仍被忽略，报 `Unknown field 'command'` |
| BB-2 | `prompt` 作为 shell 命令 | ✅ PASS |   正确执行 |
| BB-3 | `coverage` 字段兼容性 | ✅ PASS |   有 deprecated warning 但生效 |
| BB-4 | `skill` 字段 | ⚠️ WARN |   `Unknown field 'skill'`，被忽略，step 照常执行 |
| BB-5 | `expect: false` 反向断言 | ❌ FAIL |   **postcondition 从未被评估** |
| BB-6 | `prompt` vs `prompt_file` 优先级 | ❌ FAIL |   prompt 优先但 prompt_file 仍触发验证失败 |
| BB-7 | output 未创建时 `{prev_output_path}` | ✅ PASS |   引用不存在文件，s2 结果="not_found" |
| BB-8 | `continue_on_error` | ✅ PASS |   正确跳过失败文件，继续处理 c.c |

---

## 🔴 Bug #1 (P0): `command` 字段仍被静默忽略

**现象**：CONFIG-GUIDE 和 USER-GUIDE 都列出 `command` 字段，但运行时报 `Unknown field 'command'`，命令不执行。

**复现**：
```yaml
- id: s1
  executor: shell
  command: "echo hello > output.txt"   # ← 被忽略
  postcondition:
    shell: "test -f output.txt"         # ← 永远 fail（文件未创建）
```

**影响**：场景 2（代码审查）中 shell executor 的 `command` 字段不工作，文档示例无法运行。

---

## 🔴 Bug #2 (P1): `expect: false` 反向断言完全失效

**现象**：USER-GUIDE §7 明确说 `expect: false` 表示"shell **退出码非 0** 才通过"。但实际流程是：
1. shell 命令 `exit 1` → 框架判定 `cc_failed: exit 1` → 进入 retry
2. **postcondition 从未被评估**

**复现**：
```yaml
- id: s1
  executor: shell
  prompt: "exit 1"
  postcondition:
    shell: "exit 1"
    expect: "false"          # ← 永远不会被检查到
```

**根因**：框架在 shell 执行器返回非零退出码时，直接归为"CC 崩溃"进入 retry，跳过了 postcondition 的评估。`expect: false` 要求 shell 命令先 exit 0 才能触发 postcondition——但这样的话 postcondition 就会读到 exit 0，`expect: false` 永远不会通过。形成死结。

**影响**：`expect: false` 功能完全不可用。所有需要反向断言（"期望某件事失败"）的场景都无法实现。

---

## 🔴 Bug #3 (P1): `prompt` 优先但 `prompt_file` 仍触发验证失败

**现象**：同时设置 `prompt` 和 `prompt_file`，代码正确识别 prompt 优先（打印 warning），但随后的 config validation **仍然检查 prompt_file 是否存在**，检查失败则整体报错退出。

**复现**：
```yaml
- id: s1
  executor: shell
  prompt: "echo hello"
  prompt_file: "/nonexistent/file.md"    # ← 不应该被检查
```

输出：
```
Step s1: both prompt and prompt_file set — prompt takes priority, prompt_file ignored
Error: Config validation failed: prompt_file not found: /nonexistent/file.md
```

**影响**：`prompt` + `prompt_file` 组合写法不可用。用户无法同时保留 prompt_file 作为备份——必须删除 prompt_file 才能跑。

---

## 🟡 文档问题 (P2)

| # | 位置 | 问题 |
|---|---|---|
| DOC-1 | CONFIG-GUIDE L85 | `coverage` 顶级字段仍出现在 module 字段表中，USER-GUIDE 说已迁移到 `variables` |
| DOC-2 | CONFIG-GUIDE L39 | `skill` 字段标记"预留"，但代码不识别且报 Unknown field |
| DOC-3 | CONFIG-GUIDE L52 | 说"shell: prompt 就是 shell 命令本身"，与 L46 的"shell \| command" 矛盾——shell 实际只认 `prompt` |
| DOC-4 | CONFIG-GUIDE L349-358 | "通用 Prompt 原则"表格仍重复出现两次 |

---

## 总结

| 轮次 | 总用例 | 发现 Bug |
|---|---|---|
| Round 1 | 16 项 | 3 个 (command / prompt_prefix / state.json 污染) |
| Round 2 | 8 项 | 3 个 (command 未修 / expect:false 失效 / prompt_file 验证过激) |

**1 个 P0 未修复（command），2 个 P1 新增（expect:false / prompt_file 验证）。**
