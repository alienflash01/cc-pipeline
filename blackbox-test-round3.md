# cc-pipeline 黑盒测试报告（Round 3）

> 日期：2026-07-16 | 纯 shell executor | 8 用例 + 附加验证

---

## 测试用例

| ID | 用例 | 结果 |
|---|---|---|
| BB-1 | `contains('PASS')` + `contains('FAIL')` (不匹配) | ✅ s1 pass, s2 fail (预期) |
| BB-2 | 空 prompt | ✅ 执行但无效果 |
| BB-3 | `expect: ... \|\| ...` OR 运算 | ✅ `$.score >= 80 \|\| $.grade == "B"` 通过 |
| BB-4 | `{output}` 变量 + output 路径 | ❌ 见 Bug #4 |
| BB-5 | source_files glob `*.c` | ❌ 见 Bug #5 |
| BB-6 | prompt_prefix（Round 1 修复验证）| ✅ 确认修复 |
| BB-7 | `--module alpha,beta` 多选 | ✅ 正确过滤 |
| BB-8 | module 名含连字符/下划线 | ✅ 通过 |
| BB-12 | `max_retries: 0` | ✅ 执行 1 次即停 |
| BB-13 | `concurrency: 0` | ✅ 编译期报错 |
| BB-14 | `{file}` 在非 loop 步 | ✅ warning + 原样保留 |
| BB-15 | 重复 module 名 | ✅ 编译期报错 |
| BB-16 | postcondition 命令不存在 (exit 127) | ⚠️ 错误消息可改进 |
| BB-18 | 5 步循环依赖 | ✅ 检测到 |
| BB-19 | 缺 `repo` 字段 | ✅ 编译期报错 |
| BB-20 | 空 modules 列表 | ✅ 编译期报错 |
| BB-21 | 空 pipeline 列表 | ✅ 编译期报错 |
| BB-22 | `on_failure` 指向自身 | ⚠️ 静默忽略，无警告 |
| BB-23 | `file_order` 无效值 | ✅ 编译期报错 |
| BB-24 | `output_branch_prefix: ""` | ❌ 见 Bug #6 |

---

## 🔴 Bug #4 (P0): `source_files` glob 展开完全不工作

**现象**：`source_files: ["*.c"]` 返回空列表，`loop: per_file` 报 `has empty source_files`。但显式列表 `source_files: [a.c, b.c, c.c]` 正常。

**已验证**：
- `source_dir: src` + `source_files: ["*.c"]` → 空（BB-5）
- `source_dir: .` + `source_files: ["*.c"]` → 空（BB-5a）
- `source_dir: .` + `source_files: ["*.md"]` → 空（BB-5b）
- `source_dir: src` + `source_files: [a.c, b.c, c.c]` → 正常运行（BB-5f）
- `ls -la src/` 确认 worktree 中文件存在（BB-5c）

**影响**：USER-GUIDE §4 和 CONFIG-GUIDE 都展示了 glob 用法。任何使用 glob 的配置都会在编译期失败。

---

## 🔴 Bug #5 (P1): `output` 拒绝 `.pipeline/` 前缀路径

**现象**：`output: .pipeline/output.json` 报错 `Invalid output: no path traversal`。但 `.pipeline/` 是框架自身的输出目录。

**已验证**：
- `output: result.json` → ✅ 通过（BB-4d）
- `output: .pipeline/output.json` → ❌ 报错（BB-4b）
- `output: /tmp/abs/path.json` → ❌ 报错（BB-4c）

**影响**：如果用户在 `output` 中写 `.pipeline/xxx.json`（模仿文档示例中的路径），会得到一个令人困惑的 "path traversal" 错误。框架应将 `.pipeline/` 前缀自动去除，或给出更清晰的错误消息。

**根因推测**：路径校验规则对 `.` 开头的目录做了过于宽泛的 path traversal 检测。

---

## 🔴 Bug #6 (P1): `output_branch_prefix: ""` 产生非法分支名

**现象**：`output_branch_prefix: ""` 导致 `git worktree add -b /m1` → `fatal: '/m1' is not a valid branch name`

**影响**：配置层面未校验 `output_branch_prefix` 非空。用户留空后到运行期才报错（且错误信息是 git 底层错误，非框架自有消息）。

**修复建议**：编译期校验 `output_branch_prefix` 非空且符合 git 分支名规范。

---

## 与前两轮关联

| Bug | 状态 |
|---|---|
| Round 1 BUG-1: `command` 字段被忽略 | ❌ 仍存在 |
| Round 1 BUG-2: `prompt_prefix` 注入 shell | ✅ 已修复 |
| Round 1 BUG-3: state.json 跨 run 污染 | 未重测 |
| Round 2 BUG-2: `expect: false` 失效 | ❌ 仍存在 (同根因) |
| Round 2 BUG-3: `prompt_file` 验证过激 | ❌ 仍存在 |

---

## 三回合汇总

| 轮次 | 新增 Bug | P0 | P1 |
|---|---|---|---|
| Round 1 | 3 | 1 | 2 |
| Round 2 | 3 | 1 | 2 |
| Round 3 | 3 | 1 | 2 |
| **总计** | **9** | **3** | **6** |
