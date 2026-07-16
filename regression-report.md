# cc-pipeline 回归测试报告

> 日期：2026-07-16 | 基于最新文档和代码

---

## ✅ 已修复 (5项)

| # | 问题 | 证据 |
|---|------|------|
| BUG-7 | `{prev_output_path}` 不解析 | 输出 `.pipeline/gen.json` ✅ |
| BUG-9 | `{current_output_path}` 不解析 | 输出 `.pipeline/cur.json` ✅ |
| BUG-8 | `step.modules` 报 Unknown field | 警告消失，过滤正确（beta=0步） ✅ |
| DOC-1 | README 测试数量 225/259 不一致 | 已统一为 712 ✅ |
| BUG-10 | `prompt_prefix` 注入 shell | Round1 已确认修复 ✅ |

---

## ❌ 仍存在 (6项)

| # | 严重度 | 问题 | 错误消息 |
|---|--------|------|---------|
| BUG-1 | P0 | `command` 被静默忽略 | `Unknown field 'command' — ignored` |
| BUG-4 | P0 | glob `*.c` 完全不工作 | `has empty source_files` |
| BUG-2 | P1 | `expect: false` 从不被评估 | `cc_failed: exit 1` → retry |
| BUG-3 | P1 | `prompt`+`prompt_file` 时验证报错 | `prompt_file not found` |
| BUG-5 | P1 | `output: .pipeline/xxx` 被拒 | `no path traversal or slashes allowed` |
| BUG-6 | P1 | `output_branch_prefix: ""` 崩 | `fatal: '/m1' is not a valid branch name` |

---

## 汇总

| | 数量 |
|---|---|
| 总 bug | 11 |
| ✅ 已修复 | 5 |
| ❌ 仍存在 | 6 |
| 修复率 | 45% |
