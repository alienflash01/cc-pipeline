# cc-pipeline 竞争力与 DSL 定位

> 圆桌讨论记录，2026-07-13/14
> 参会者：Graham / Hickey / Beck / Ousterhout / Evans

---

## 一、市场定位

### 核心定位：CC 的 Makefile

```
Ruflo = 给 CC 加大脑（智能路由 + 记忆 + 学习）
cc-pipeline = 给 CC 加流水线（声明式步骤 + 门控 + 文件级展开）
```

不比"谁更智能"——比"谁更可控"。

### 类比

| 执行器 | 编排器 |
|--------|--------|
| gcc | Make |
| Docker | Kubernetes |
| `claude -p` | cc-pipeline |

### 推广路径

```
公司验证（真实 C 工程）
  → 数据（手写 vs cc-pipeline 对比）
    → GitHub（有数据支撑的项目）
      → 技术博客（"3 小时跑完 100 个文件的 UT 生成"）
        → 社区
```

---

## 二、竞品全景

### 直接竞品

| 项目 | Stars | 语言 | 定位 | 和 cc-pipeline 的关系 |
|------|:------:|:----:|------|----------------------|
| **Ruflo** | 64K | TS | agent 元编排器 + swarm + 记忆 + 35 插件 | 最强竞品，生态最大 |
| **Omnigent** | 7.2K | Python | CC/Codex/Cursor 多 agent 治理 | 企业级治理 |
| **Golutra** | 3.8K | Rust | Codex/CC/OpenClaw 统一 agent 系统 | Rust 性能 |
| **spec-workflow** | 3.8K | TS | 需求→设计→任务→实现自动化 | spec 驱动 |
| **Kelos** | 253 | Go | K8s 原生 AI agent CRD 编排 | 企业 K8s |
| **Foreman** | 209 | Python | TUI 监督 CC agent + 门控 | 最像 cc-pipeline |

### cc-pipeline 独有能力（无竞品覆盖）

| 能力 | cc-pipeline | 所有竞品 |
|------|:-----------:|:--------:|
| per_file 文件级展开 | ✅ | ❌ |
| 声明式 YAML（可 diff/审计） | ✅ | ❌ |
| postcondition JSON path 表达式 | ✅ | ⚠️ 简单 gate |
| resume 精确到文件级 | ✅ | ❌ |
| 轻量（pip install + 1 YAML） | ✅ | ❌ 重 |

### 竞品有但 cc-pipeline 没有的

| 能力 | 建议追吗？ | 原因 |
|------|:---------:|------|
| 多后端（Codex/Cursor） | ⚠️ 未来 | 扩大用户面 |
| Swarm 多 agent | ❌ | Ruflo 的领域 |
| 记忆/RAG/学习 | ❌ | Omnigent 的领域 |
| K8s 原生 | ❌ | Kelos 的领域 |
| TUI 可视化 | ❌ | Foreman 的领域 |
| 成本追踪（token 预算） | ⚠️ 未来 | 简单加 |
| 路径限制（安全治理） | ⚠️ 未来 | 简单加 |

---

## 三、cc-pipeline vs CC 内置能力

### CC 自身能做什么

| CC 能力 | 说明 |
|---------|------|
| `goal` | 模糊目标持续执行（跨 turn） |
| `loop` | 单 session 内循环 |
| `subagent` | spawn 子 agent |
| `--worktree` | 独立工作区 |

### 核心区别：prompt（建议）vs YAML（合同）

```
CC 的方式：prompt 软约束
  → CC 可能遵守也可能不遵守
  → context 压缩后标准漂移
  → prompt 无法 diff/审计
  → 中断后从头开始

cc-pipeline 的方式：YAML 硬约束
  → 编译期确定步骤，运行期严格执行
  → 相同配置相同行为
  → 可 diff/审计/版本控制
  → 中断后精确恢复
```

### 逐项对比

| 能力 | 纯 CC | cc-pipeline | 增量价值 |
|------|-------|-------------|---------|
| 多步骤编排 | ⚠️ prompt 软约束 | ✅ YAML 硬约束 | 可复现 |
| per_file 展开 | ⚠️ 不可靠 | ✅ 精确展开 | 顺序稳定 |
| postcondition | ⚠️ CC 自判 | ✅ JSON path 断言 | 判断标准固定 |
| retry | ⚠️ CC 自决 | ✅ 预算精确 | 重试可控 |
| **resume** | ❌ 从头开始 | ✅ state.json | **硬壁垒** |
| **审计日志** | ❌ stdout | ✅ transcript.jsonl | **硬壁垒** |
| **并发 worktree + merge** | ❌ | ✅ 多模块 + lock | **硬壁垒** |
| **声明式配置** | ❌ | ✅ YAML 可 diff | **硬壁垒** |

**9 个能力中，CC 通过 prompt 软覆盖 4 个，cc-pipeline 独有 5 个硬壁垒。**

### 用户画像分层

| 层级 | 用户 | 需要什么 |
|------|------|---------|
| 个人开发者 | "帮我写个测试" | CC goal 够用 |
| 小团队 Lead | "15 个文件都生成测试，覆盖率 80%" | cc-pipeline 保证完整性 |
| 企业 CI/CD | 审计/合规/resume/并发/合规 merge | **cc-pipeline 必需** |

---

## 四、DSL 定位

### cc-pipeline 是一门领域特定语言

| cc-pipeline | 编程语言概念 |
|-------------|------------|
| YAML pipeline | 源代码 |
| `loop: per_file` | `for file in files:` |
| `postcondition expect` | `assert` |
| `retry: 3` | `try/catch` + 重试上限 |
| `on_failure: P2` | `goto P2` / 异常跳转 |
| `depends_on` | 模块依赖 / import 顺序 |
| `modules: [A,C]` | `if module in [A,C]:` |
| `variables` | 变量赋值 |
| `snippets` | `#include` / macro |
| `prompt_prefix` | 编译器 preamble |
| `state.json` | checkpoint / 持久化 |
| `resume` | 从 checkpoint 恢复执行 |
| compiler.py → CompiledStep[] | 编译器：源码 → IR |
| runner.py → 逐步执行 | 虚拟机 / 运行时 |

### CC 是"系统调用"——非确定性原语

```
传统程序：result = compile(file)    // 确定性
cc-pipeline：result = cc(prompt)   // 非确定性
```

cc-pipeline 的核心设计围绕：**如何用非确定性原语构建确定性流程。**

### 语言表达力边界

**当前 8 个语法元素覆盖 90% 场景：**
- 顺序执行、循环(per_file)、条件(postcondition)
- 异常处理(retry)、跳转(on_failure)
- 变量、宏(snippets)、模块过滤(modules)

**不加的（保持 DSL 纯粹性）：**
- ❌ if/else 条件分支
- ❌ 嵌套循环
- ❌ 函数定义/调用
- ❌ 步骤间 JSON 数据引用
- ❌ when 条件执行

**类比：Make 没有 if/else/函数调用/数据处理，活了 40 年。cc-pipeline 同理。**

---

## 五、适用场景

### 天然适配（批量 × 多步骤 × 需要验证）

1. **UT 生成** — per_file 逐文件生成 + 覆盖率门控
2. **代码迁移/重构** — Python 2→3、框架升级
3. **代码审查/安全审计** — CVE 扫描 + 自动修复
4. **文档生成** — API 文档、架构文档
5. **性能优化** — profile → optimize → benchmark 门控
6. **多语言翻译/i18n** — 逐文件翻译 + 编译验证

### 不适合

| 场景 | 原因 |
|------|------|
| 单文件一次性任务 | 直接用 CC |
| 交互式对话 | cc-pipeline 是批量非交互的 |
| 实时/流式处理 | 批处理模式 |
| agent 之间协商 | 用 Ruflo swarm |
| 需要记忆/学习 | 用 Omnigent RAG |

---

## 六、共识总结

| 主题 | 结论 |
|------|------|
| 定位 | CC 的 Makefile——声明式 + 确定性 + 文件级 |
| 护城河 | per_file + 声明式 + 深度边界处理 |
| vs CC | prompt=建议，YAML=合同。企业必须用合同 |
| vs 竞品 | 不比智能比可控。深耕 C/嵌入式场景 |
| DSL 边界 | 8 个语法元素足够。不加图灵完备性 |
| 推广 | 先自用出数据，再开源推广 |
| 下一步 | 公司服务器跑真实 C 工程出数据 |
