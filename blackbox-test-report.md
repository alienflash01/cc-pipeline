# cc-pipeline 黑盒测试 + 文档审查报告

> 版本：v0.3.2 | 日期：2026-07-16 | 方式：纯 shell executor 黑盒测试（零 CC 调用）+ 文档审查

---

## 一、黑盒测试结果（12 用例，15 项）

| ID | 用例 | 结果 |
|---|---|---|
| BB-01 | 基础 3 步顺序执行 | ✅ |
| BB-02 | postcondition 门控（JSON expect pass/fail） | ✅ |
| BB-03a | per_file batched 展开 | ✅ |
| BB-03b | per_file sequential 展开 | ✅ |
| BB-04 | retry 机制（失败 2 次后第 3 次通过） | ✅ |
| BB-05 | on_failure 回跳 | ✅ |
| BB-06 | depends_on 拓扑排序 | ✅ |
| BB-07a | 重复 step id 检测 | ✅ |
| BB-07b | 循环依赖检测 | ✅ |
| BB-07c | 不存在的 module 引用检测 | ✅ |
| BB-08 | --dry-run 配置预览 | ✅ |
| BB-09 | resume 断点续传（step 级跳过） | ✅ |
| BB-10 | output 文件隔离（per_file gen-{file}.json） | ✅ |
| BB-11 | 边界值 max_retries=0 | ✅ |
| BB-12 | 变量注入 {module} | ✅ |

---

## 二、发现的 Bug（1 P0 + 2 P1）

### BUG-1（P0）：`command` 字段被静默忽略

**现象**：USER-GUIDE §4 step 字段表明确写了 `command` 字段：

> | `command` | string | `""` | shell executor 的命令（替代 `prompt`） |

但实际运行时框架打出警告 `Unknown field 'command' in step 'xxx' — ignored`，命令根本不执行。

**复现**：
```yaml
- id: test
  executor: shell
  command: "echo hello > output.txt"   # ← 被忽略
  postcondition:
    shell: "test -f output.txt"         # ← 永远 fail
```

**影响**：任何按文档使用 `command` 字段的用户都会遇到"shell 步骤不执行但报错说不符合 postcondition"，且不知道为什么。只有看了 `-v` 的 warning 才能发现。

**修复建议**：要么实现 `command` 字段（shell executor 优先用 `command` > `prompt`），要么从文档中删除该字段。

---

### BUG-2（P1）：`prompt_prefix` 被注入到 shell executor

**现象**：配了 `prompt_prefix: "全局前缀行"` 后，shell executor 收到的实际命令变成了：
```
全局前缀行
echo 'auth' > /tmp/.../bb12_module.txt
```
导致 `/bin/sh: 1: 全局前缀行: not found`（exit 127）。

**文档说法**：USER-GUIDE §6 说自动上下文注入"仅对 claude-code 和 judge executor 生效（shell executor 不注入）"。但 `prompt_prefix` 的注入没有排除 shell executor。

**影响**：任何在配了 `prompt_prefix` 的配置中使用 shell executor 的步骤都会失败。

---

### BUG-3（P1）：state.json 跨 run 污染

**现象**：BB-09 resume 测试中，resume 正确跳过了 26 个已完成 step——但这 26 个 step 来自之前所有测试的累积（BB-01 到 BB-08 都用了同一个 module 名 `demo`）。不同测试配置的 state 被混在同一个 state.json 中。

**影响**：如果用户在同一个 repo 上跑不同的 pipeline 配置（比如先跑 UT 生成，再跑代码审查），resume 可能错误地跳过步骤——因为 state.json 不区分 pipeline 配置。

---

## 三、文档审查问题（5 矛盾 + 8 未定义 + 3 断档 + 4 风险）

### 🔴 文档矛盾

| ID | 问题 | 详情 |
|---|---|---|
| DOC-1 | 测试数量三文档三个数字 | README: 225/259, USER-GUIDE: 616 |
| DOC-2 | prompt 注入行为矛盾 | CONFIG-GUIDE 说自动注入 `.pipeline/*.json`；USER-GUIDE §4 说"默认不自动注入" |
| DOC-3 | output_branch_prefix 默认值矛盾 | CONFIG-GUIDE: `ut-auto`; USER-GUIDE: `cc-auto` |
| DOC-4 | 旧 DSL 字段残留 | DESIGN.md (v0.1) 仍列出 `skill`/`rollback`/`on_complete`，代码已删除 |
| DOC-5 | CONFIG-GUIDE 格式重复 | "长 prompt 用 prompt_file" 规则重复出现两次 |

### 🟡 行为未定义

| ID | 输入 | 问题 |
|---|---|---|
| DOC-6 | `concurrency: 0` | 未定义。报错？串行？ |
| DOC-7 | `max_retries: 0` | 未定义。已测：执行 1 次不重试（行为正确但文档没写） |
| DOC-8 | `source_files: []`（glob 无匹配） | 文档说 warn，但没说 step 执行行为 |
| DOC-9 | `timeout: 0` | 未定义。无限等待？报错？ |
| DOC-10 | 两个 step 相同 `id` | 已测：报 Duplicate step ID（行为正确但文档没写） |
| DOC-11 | `depends_on` 循环 | 已测：报 Circular dependency（行为正确但文档没写） |
| DOC-12 | `on_failure` 指向自身 | 未定义（会不会死循环？） |
| DOC-13 | `output` 路径穿越 | 未定义（如 `../../../etc/passwd`） |

### 🟠 功能断档

| ID | 问题 |
|---|---|
| DOC-14 | `output_prompt` 字段在 USER-GUIDE 列出但全文无使用示例（§6 后来补充了示例） |
| DOC-15 | `step.modules` 过滤和 `depends_on` 的交互未说明 |
| DOC-16 | resume × on_failure 的交互未说明（on_failure jump 计数器在 resume 后重置吗？） |

### 🟣 设计风险

| ID | 问题 |
|---|---|
| DOC-17 | DESIGN.md 标注 v0.1 但 README 仍引用，无过期标记 |
| DOC-18 | 错误恢复矩阵不完整（缺少磁盘满、部分成功 resume、CC 输出非 JSON） |
| DOC-19 | prompt 变量注入的安全边界模糊（变量解析在注入之前还是之后？二次解析风险？） |
| DOC-20 | 大文件处理无声（source_files 有 200 个文件时 per_file 会不会爆 context？文档无限制说明） |

---

## 四、表现良好的功能

1. **postcondition JSON expect**：`$.score >= 60` 解析精准，失败信息含 actual value
2. **per_file batched/sequential**：两种展开顺序完全正确
3. **retry 不回滚**：counter 确认第 3 次执行成功，worktree 未清理重建
4. **on_failure 回跳**：jump 正确触发，fixer 执行后 checker 重跑通过
5. **depends_on 拓扑排序**：乱序配置正确排序
6. **错误输入检测**：重复 id、循环依赖、不存在 module 全部 fail-fast
7. **resume step 级跳过**：精确到 per_file 文件粒度
8. **变量注入**：`{module}` / `{file}` 正确替换
9. **output 文件隔离**：`gen-{file}.json` 每文件独立
10. **--dry-run**：配置预览 + 估算 CC 调用次数

---

## 五、修复优先级建议

| 优先级 | 编号 | 问题 | 工作量 |
|---|---|---|---|
| **P0** | BUG-1 | `command` 字段被静默忽略 | ✅ 已删除 command（统一 prompt） |\n| **P1** | BUG-2 | `prompt_prefix` 注入 shell executor | ✅ shell 跳过 prefix |\n| **P1** | BUG-3 | state.json 跨 run 污染 | ✅ 设计确认：每个 run 独立 run_dir |\n| **P2** | DOC-2 | prompt 注入行为文档矛盾 | ✅ §6 已重写 |\n| **P2** | DOC-3 | output_branch_prefix 默认值 | ✅ 统一 cc-auto |\n| **P2** | DOC-4 | DESIGN.md 过时字段 | ✅ 加 v0.1 过期标记 |
| **P3** | DOC-6~13 | 边界值行为未定义 | 中（补文档 + 加测试） |
| **P3** | DOC-1 | 测试数量不一致 | 小（更新数字） |
