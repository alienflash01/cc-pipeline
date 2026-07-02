# cc-pipeline 价值主张与实施策略

> 核心结论：UT 覆盖率 ≠ 代码质量。存量代码 UT 生成容易把现有 bug 固化为"正确行为"。
> 真正的价值在于"AI 代码审计 + 安全网建设"。

---

## 一、三层次价值模型

### 层次 1：AI 代码审计（发现 bug）— 最有说服力

CC 分析存量代码时主动发现：
- 空指针未检查
- 边界条件遗漏
- 异常路径未处理
- 类型隐式转换陷阱
- 资源泄漏（malloc 无 free）

**产出：bug 列表（含严重度分级），可直接汇报。**

### 层次 2：安全网建设（回归测试）— 重构的前提

存量代码没设计没测试 → 没人敢改。CC 生成的 UT 即使只验证"当前行为"，也是让代码可修改的前提。

**推广话术：** "以前改一行怕崩三处，现在有 80% 覆盖率的回归测试托底。"

### 层次 3：UT 质量验证（变异测试）— 反向证明

UT 生成后，故意改源码（`>` 改 `>=`、`return 0` 改 `return 1`），跑 UT 看是否捕获。
变异测试得分 = UT 能检测到的变异比例。

**比覆盖率更能说明 UT 质量。**

---

## 二、推荐 Pipeline 设计

```
analyze（CC 分析代码 → 发现潜在 bug）
  ↓
scaffold（创建测试目录 + 编译配置）
  ↓
generate（CC 生成 UT → 覆盖率门控）
  ↓
mutate（变异测试验证 UT 质量）  ← 可选，夜间离线跑
  ↓
report（汇总：bug 数 / 覆盖率 / 变异得分）
```

### YAML 配置模板

```yaml
repo: /path/to/embedded-project
base_branch: main
concurrency: 5
max_retries: 3
output_branch_prefix: ut-auto

pipeline:
  # Step 1: AI 代码审计 — 发现潜在 bug
  - id: analyze
    executor: claude-code
    prompt_file: prompts/analyze.md
    output: analyze.json
    postcondition:
      shell: "test -f .pipeline/analyze.json"
    # analyze.json 格式：
    # { module, bugs: [{severity, file, line, desc}], summary }

  # Step 2: 创建测试基础设施
  - id: scaffold
    executor: claude-code
    prompt_file: prompts/scaffold.md
    output: scaffold.json
    postcondition:
      shell: "test -d tests/{module}"
    depends_on: analyze

  # Step 3: 逐文件生成 UT（覆盖率门控）
  - id: generate
    executor: claude-code
    loop: per_file
    prompt_file: prompts/generate.md
    output: generate.json
    postcondition:
      shell: "make test MODULE={module} 2>&1 | tail -1"
      expect: "contains('passed')"
    retry: 3
    depends_on: scaffold

  # Step 4: 变异测试（可选，夜间离线）
  - id: mutate
    executor: shell
    command: "mull test --json-output .pipeline/mutation.json tests/{module}/"
    postcondition:
      shell: "test -f .pipeline/mutation.json"
    depends_on: generate

modules:
  - name: auth
    source_dir: src/auth/
    source_files: [auth_login.c, auth_token.c, auth_check.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
  # ... 更多模块
```

---

## 三、推广话术模板

### 向上汇报（技术总监 / 部门经理）

> "AI 分析了 500 个 C 文件，发现 23 个潜在 bug（其中 7 个高危）。
> 自动生成回归测试覆盖 80% 代码。
> 变异测试得分 65%——意味着三分之二的代码变更会被 UT 自动捕获。
> 总成本 ¥XX，耗时 X 小时（无人值守）。"

### 向平级推广（其他产品线负责人）

> "我们用 AI 给存量代码做了三件事：
> 1. 找 bug — AI 读代码主动报告问题
> 2. 建安全网 — 80% 覆盖率，以后改代码有底了
> 3. 验质量 — 变异测试证明 UT 不是摆设
>
> 每个团队改一个 YAML 就能跑自己的工程。"

### 向下沟通（开发团队）

> "这不是让你手写 UT。
> AI 生成，编译验证，覆盖率门控，失败自动重试。
> 你只需要 review PR 和确认 AI 发现的 bug。"

---

## 四、关键实施措施

### 4.1 试点选择标准

- 选一个 **中等规模** 模块（10-20 个 .c 文件）
- 代码相对稳定（不是正在大改的模块）
- 有明确的公开接口（便于 CC 理解和测试）
- 最好已知有一些 bug（展示"AI 代码审计"价值）

### 4.2 成功指标

| 指标 | 目标 | 来源 |
|------|------|------|
| 发现 bug 数 | ≥3 个/100 文件 | analyze 步骤 |
| 高危 bug | ≥1 个 | analyze 步骤 |
| UT 覆盖率 | ≥70% | generate postcondition |
| 变异测试得分 | ≥50% | mutate 步骤 |
| 生成成功率 | ≥80% modules passed | orchestrator state |
| 成本 | ≤¥5/模块 | token 追踪（待实现） |
| 耗时 | ≤5min/模块 | transcript 时间戳 |

### 4.3 风险与缓解

| 风险 | 缓解 |
|------|------|
| CC 把现有 bug 固化为"正确行为" | analyze 步骤先于 generate，发现 bug 后人工确认 |
| 生成的 UT 质量差 | 变异测试反向验证 |
| CC 修改了 src/ 代码 | git checkpoint + rollback |
| API 限流 | concurrency=5, daemon 夜间跑 |
| 历史代码编译依赖复杂 | scaffold 步骤让 CC 先理解构建系统 |

---

## 五、下一步执行清单

- [ ] 选定试点模块
- [ ] 确认 dtest 断言宏 + 编译命令
- [ ] 确认覆盖率工具（gcov?）
- [ ] 在公司服务器配置 claude CLI + API
- [ ] 编写 analyze.md / scaffold.md / generate.md prompt 文件
- [ ] 先跑单模块验证 pipeline
- [ ] 批量推广到全量模块
- [ ] 收集数据，准备推广材料
