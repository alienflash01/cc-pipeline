# cc-pipeline 修复审查报告 (docs-only)

> 审查日期：2026-07-16 | 审查方式：仅读文档 + 黑盒重测，不触及源码

---

## 一、已修复 ✅

| 编号 | 问题 | 证据 |
|---|---|---|
| DOC-4 | DESIGN.md 过时字段无标记 | L3 新增 ⚠️ 警告："本文档为 v0.1 历史设计，部分字段已删除（skill/rollback/on_complete/command）。以 USER-GUIDE.md 为准。" |
| BUG-2 | `prompt_prefix` 注入到 shell executor | 黑盒复测通过：shell step + prompt_prefix 正确执行，exit=0 |

---

## 二、部分修复（文档更新了但代码未实现）

| 编号 | 问题 | 现状 |
|---|---|---|
| BUG-1 | `command` 字段被静默忽略 | CONFIG-GUIDE step 字段表新增了 `command` 行（L30），但黑盒复测仍失败——shell executor 不执行 `command`，只执行 `prompt`。 |

---

## 三、未修复 ❌

| 编号 | 问题 | 现状 |
|---|---|---|
| DOC-1 | 测试数量不一致 | README L5 仍显示 `tests-225 passed`，USER-GUIDE L3 仍显示 `616 tests`，数字未统一 |
| DOC-2 | prompt 注入行为矛盾 | CONFIG-GUIDE L48 仍写"claude-code 和 judge：自动注入 `.pipeline/*.json` + `progress.md`"，与 USER-GUIDE §4 "默认不自动注入" 矛盾 |
| DOC-3 | `output_branch_prefix` 默认值矛盾 | CONFIG-GUIDE L15 仍写 `ut-auto`，USER-GUIDE 已改为 `cc-auto` |
| DOC-5 | CONFIG-GUIDE 格式重复 | "长 prompt 用 `prompt_file`" 规则在 Prompt 编写经验表格中仍重复出现（L348-349） |
| — | CONFIG-GUIDE 残留旧字段 | `skill`（L37）和 `coverage` 顶级字段（L83）仍存在，USER-GUIDE 已删除/迁移到 variables |
| BUG-3 | state.json 跨 run 污染 | 未经黑盒复测确认；需开发者确认是否修复 |

---

## 四、总结

| 状态 | 数量 |
|---|---|
| ✅ 已修复 | 2 |
| ⚠️ 部分修复 | 1 |
| ❌ 未修复 | 6 |
