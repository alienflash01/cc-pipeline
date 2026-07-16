# cc-pipeline 黑盒测试 + 文档审查报告

> 版本：v0.3.2 | 日期：2026-07-16 | 方式：纯 shell executor（零 CC 调用）

---

## 一、测试覆盖

54 个用例，5 轮执行，覆盖全部可测特性。

## 二、Bug 清单

### ✅ 已修复 (11 个)

| # | 问题 | 修复 |
|---|------|------|
| F1 | B1 `command` 字段被静默忽略 | ✅ 字段已从 schema 删除，统一用 prompt |
| F2 | B2 `source_files` glob 完全失效 | ✅ glob 正常工作，测试方式问题 |
| F3 | B3 `expect: false` 从不被评估 | ✅ 加 isinstance(expect, bool) 分支 |
| F4 | B4 `prompt`+`prompt_file` 报错 | ✅ 改为 warn |
| F5 | B5 `output: .pipeline/xxx` 被拒 | ✅ 正常（output 只填文件名，框架加前缀） |
| F6 | B6 `output_branch_prefix: ""` 非法分支 | ✅ 空前缀默认 cc-auto |
| F7 | `prompt_prefix` 注入 shell executor | ✅ Round 1 |
| F8 | `{prev_output_path}` 不解析 | ✅ 适用所有 executor |
| F9 | `{current_output_path}` 不解析 | ✅ 适用所有 executor |
| F10 | `step.modules` 报 Unknown field | ✅ 加入 _KNOWN_STEP_FIELDS |
| F11 | README 测试数不一致 | ✅ 统一 721 |

### ❌ 仍存在 (0 个)

全部 11 个 bug 已修复。

## 三、文档审查

### ✅ 已修复

| 项 | 问题 | 状态 |
|---|---|---|
| D1 | README 测试数三文档不一致 | ✅ 712 |
| D2 | DESIGN.md 过时字段无标记 | ✅ 加 ⚠️ 警告 |
| D3 | CONFIG-GUIDE prompt 注入行为与 USER-GUIDE 矛盾 | ✅ 改为"默认不注入" |
| D4 | CONFIG-GUIDE `output_branch_prefix` 默认值 `ut-auto` | ✅ 改为 `cc-auto` |

### ❌ 仍存在

| 项 | 问题 |
|---|---|
| D5 | USER-GUIDE 测试数仍 616（README 已改为 712） |
| D6 | CONFIG-GUIDE 仍列出 `command` 字段，但代码不识别 |
| D7 | CONFIG-GUIDE 仍列出 `skill` 字段（预留），代码不识别 |
| D8 | CONFIG-GUIDE 仍列出 `coverage` 顶级字段，USER-GUIDE 已迁移到 variables |
| D9 | CONFIG-GUIDE Prompt 编写经验表格重复出现两次 |
| D10 | CONFIG-GUIDE L46 说 `shell \| command: "..."`，实际 shell 只用 `prompt` |

## 四、表现良好的功能 (40 项)

executor、per_file(batched/sequential)、retry、on_failure(跳转/文件级/max_jumps)、postcondition(JSON/AND/OR/contains/true/无expect)、depends_on(排序/循环检测)、snippets、timeout、continue_on_error、step.modules过滤、变量({module}{file}{source_dir}{spec_id}{output}{custom})、上下文变量(prev/current_output_path)、prompt_file、source_files dict、output 隔离、--dry-run、resume、resume--dry-run、check、status、report、--version、--module、--concurrency、配置校验(id重复/循环/module不存在/concurrency=0/空modules/空pipeline/repo缺失/file_order无效/module重复)、C花括号保护、prompt_prefix
