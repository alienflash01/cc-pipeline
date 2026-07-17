# cc-pipeline 回归复测报告

> 2026-07-17 | 基于最新代码

---

## 复测结果

| # | 问题 | 之前 | 现在 |
|---|------|------|:--:|
| BUG-1 | `command` 字段 | `Unknown field — ignored` → 静默跳过 | ✅ **Config validation failed: Shell executor uses 'prompt'** |
| BUG-6 | `output_branch_prefix: ""` | `fatal: '/m1' is not a valid branch name` | ✅ **不再崩溃，正常运行** |
| BUG-2 | glob `*.c` | `has empty source_files` | ❌ 仍存在 |
| BUG-3 | `expect: false` | 从不评估 postcondition | ❌ 仍存在 |
| BUG-5 | `output: .pipeline/xxx` | `no path traversal` | ❌ 仍存在 |
| BUG-7 | `prompt` + `prompt_file` | 报 prompt_file not found | ❌ 仍存在 |
| BUG-4 | 默认模式失败重复 | 同一失败 3 次 | ⚠️ emoji 统一为 ❌，仍重复 3 次 |

## 特性回归

| 特性 | 状态 |
|------|:--:|
| per_file batched/sequential | ✅ |
| postcondition (>=, ==, !=, &&, \|\|, contains, true, 无expect) | ✅ |
| retry / on_failure jump / max_jumps | ✅ |
| {prev_output_path} / {current_output_path} | ✅ |
| step.modules | ✅ |
| continue_on_error | ✅ |
| --dry-run / check / resume / --module | ✅ |

## 本轮修复

**2 个修复，4 个仍存，1 个改善。修复率从 45% → 54%。**
