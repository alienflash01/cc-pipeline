# cc-pipeline 五轮黑盒测试 — 终极报告

> 日期：2026-07-16 | 纯 shell executor | 54 个用例 | 11 个 Bug

---

## 一、覆盖矩阵

| 特性 | 测试 | 结果 |
|---|---|---|
| **Shell executor** | prompt / prompt_file / 空 prompt / command 字段 | ⚠️ command 不工作 |
| **变量** | `{module}` `{file}` `{source_dir}` `{spec_id}` `{output}` `{var}` | ✅ 全部正常 |
| **上下文变量** | `{prev_output_path}` `{current_output_path}` | ❌ 完全不解析 |
| **per_file** | batched / sequential / dict格式 / 自定义变量 | ✅ 全部正常 |
| **retry** | 成功/失败/预算耗尽/max_retries=0 | ✅ |
| **on_failure** | 跳转/文件级跳转/max_jumps=0,1,2 | ✅ |
| **postcondition** | JSON(>=,==,&&,||,bool) / contains / true/false / 无expect / 命令不存在 | ⚠️ false 不可用 |
| **depends_on** | 排序/循环检测/5步循环/跨per_file | ✅ |
| **output** | 纯文件名/.pipeline/前缀/绝对路径/文件隔离 | ⚠️ .pipeline/被拒 |
| **snippets** | 展开/未定义处理 | ✅ |
| **timeout** | shell sleep 超时 | ✅ |
| **continue_on_error** | 单步/双步 per_file | ✅ |
| **step.modules** | 过滤/不存在的module检测 | ⚠️ 警告误导 |
| **CLI** | --dry-run/resume/resume--dry-run/check/status/report/--version/--module/--concurrency | ✅ |
| **配置校验** | repo缺失/modules空/pipeline空/id重复/循环/名称非法/file_order/concurrency=0/module重复 | ✅ |
| **其他** | prompt_file相对路径/花括号/C代码/glob | ❌ glob不工作 |
| **边界** | output_branch_prefix="" / max_retries=0/并发3 | ⚠️ 空前缀崩 |
| **prompt_prefix** | shell/per_file | ✅ Round1修复已确认 |

---

## 二、全量 Bug 清单

| # | 严重度 | 问题 | 状态 |
|---|--------|------|:--:|
| 1 | **P0** | `command` 字段被静默忽略 | ❌ |
| 2 | **P0** | `source_files` glob `*.c` 完全失效 | ❌ |
| 3 | **P0** | `{prev_output_path}` 不解析（shell） | ❌ |
| 4 | **P0** | `{current_output_path}` 不解析（shell） | ❌ |
| 5 | P1 | `expect: false` 从不被评估 | ❌ |
| 6 | P1 | `prompt`+`prompt_file` 同时存在时验证报错 | ❌ |
| 7 | P1 | `output: .pipeline/xxx` 被拒为 path traversal | ❌ |
| 8 | P1 | `output_branch_prefix: ""` 生成非法分支名 | ❌ |
| 9 | P1 | `step.modules` 生效但报 Unknown field | ❌ |
| 10 | P1 | `prompt_prefix` 注入 shell | ✅ 已修复 |
| 11 | P1 | state.json 跨 run 污染 | 未复测 |

---

## 三、特性覆盖完成度

```
54/54 计划用例全部执行
11 个 bug（4 P0 + 7 P1）
1 个已知修复确认
```
