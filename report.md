# cc-pipeline 产品审视报告

> 视角：资深产品经理，从客户可用性角度审视
> 日期：2026-07-02

---

## 一句话定位

> "让 Claude Code 像流水线工人一样批量干活——多模块并行、自动验证、失败重试、崩了能续。"

定位清晰，方向正确。这个产品解决的是一个真实痛点：**CC 单次调用是"手工作坊"，cc-pipeline 把它升级成"工厂流水线"**。

---

## 🟢 做得好的地方

### 1. 三层信任模型是核心差异化

- `claude-code` 干活 / `shell` 验证 / `judge` 裁判——这个设计非常好，直接回应了"AI 生成的代码靠谱吗"这个核心顾虑
- 客户一听就懂：AI 不可信 → 用确定性 shell 门控 → 不过就打回去重做
- 这个概念应该放到 README 第一屏，目前藏太深

### 2. Git 原生状态管理

- 用 git tag 做 checkpoint，不需要数据库，不需要额外依赖——对目标用户（嵌入式/C 语言工程师）非常友好
- "崩了能续"对夜间无人值守场景是刚需

### 3. 配置即代码

- YAML 声明式 pipeline，一个文件描述全部——学习成本低
- CONFIG-GUIDE 写得很详细，场景示例覆盖了 UT/代码审查/技术债/文档生成

### 4. CO 式错误处理

- Rate limit 自动退避、零工作检测、超时保护——这些是从实战中长出来的需求，不是 YY 的

---

## 🔴 从客户角度看的问题

### 1. 没有 `examples/` 目录——上手门槛太高

README Quick Start 写了个 YAML，但**仓库里根本没有可运行的示例**。用户 clone 下来，改 YAML 里的 repo 路径，然后呢？

**客户真实体验：**
```
1. clone repo
2. pip install
3. 改 YAML → repo 指向我的项目
4. 运行 → 报错（CC 没装/模型没配/API key 没设）
5. 看文档 → 发现要配 ~/.claude/settings.json
6. 配完再跑 → 报错（postcondition 命令找不到）
7. 放弃
```

**建议：** 加 `examples/quickstart/`：
- 一个最小可跑的 demo repo（3 个 Python 文件）
- 对应的 `config.yaml`
- `bash examples/quickstart/run.sh` 一键跑通
- **不需要 CC API**——用 shell executor 模拟，让用户先感受 pipeline 编排能力

### 2. "我是谁、我为谁服务"——目标用户模糊

README 同时面向：
- C 嵌入式工程师（dtest/gcov）
- Python 开发者（pytest）
- 代码审查团队
- 技术债清理
- API 文档生成

**但没有任何一个场景有完整的端到端验证案例。** 客户看完会问："这东西到底适合我吗？"

**建议：** 选**一个杀手场景**深做。UT 自动生成最有说服力（因为可量化：覆盖率从 0→80%）。其他场景作为"扩展用法"轻描淡写。

### 3. 没有运行结果可视化——跑完只能看终端输出

客户跑完 10 个 module × 5 步的 pipeline，得到的是：
```
============================================================
  ✓ auth                  passed
  ✗ payment               failed
============================================================
  1 passed, 1 failed
```

然后呢？payment 为什么失败？要去看 `run_dir/payment/transcript.jsonl`——一个 JSONL 文件。

**客户期望：**
- 哪一步失败的？CC 说了什么？
- postcondition 输出了什么？
- retry 了几次？每次失败原因一样吗？
- 跟上次比，这次好还是差了？

**建议（低成本）：** 加一个 `cc-pipeline report --run-dir <dir>` 命令，生成 Markdown 报告：
- 每个 module 一张表：步骤 × 状态 × 耗时 × 失败原因
- 失败 module 的 CC stdout/stderr 摘要
- 不需要 Web UI，Markdown 足够

### 4. 没有"成功指标"——客户无法向上汇报

客户用 cc-pipeline 跑了一晚上 UT，第二天老板问：
- "效果怎么样？"
- "覆盖率提升了多少？"
- "多少 PR 合了？"
- "值不值这个 API 费用？"

**cc-pipeline 目前不回答这些问题。**

**建议：** 在 `orchestrator-state.json` 或最终输出中加：
- 总 CC 调用次数 / 总 token 消耗估算
- 成功率（passed / total）
- 各步骤平均耗时
- retry 次数分布

### 5. CC prompt 是黑盒——出了问题无法调试

客户设置了 `prompt: "为 {module} 生成测试"`，CC 返回的测试质量很差。客户问：
- "CC 实际收到的 prompt 是什么？"
- "上下文注入了什么？"
- "progress.md 内容是什么？"

**当前：** 这些信息不记录。transcript 只记了 `step_start`/`pass`/`fail`，不记实际发送给 CC 的完整 prompt。

**建议：** 在 `_execute_step` 中 log 完整 prompt（至少前 2000 字符）到 transcript。

### 6. "通用框架"定位太宽——什么都想做 = 什么都没做透

README 说"任何多步 + CC + 验证 + 重试的任务都适用"，列了 5 个场景。但客户看到这种描述会想：**"那你到底擅长哪个？"**

**建议：**
- v1.0 收敛到 **"AI 自动生成单元测试 + 覆盖率门控"** 这一个场景
- 把这个场景做到极致：内置 gcov/lcov/pytest 覆盖率解析、内置 dtest 宏识别、内置常见 C/Python 测试模板
- 其他场景留给 v2.0

### 7. 缺少成本意识——API 费用是客户的真实顾虑

10 个 module × 3 步 × 平均 2 次 retry = 60 次 CC 调用。如果用 GLM-4.6，每次几千 token，一晚上跑下来可能几十块。

**cc-pipeline 完全没有成本追踪。** 客户不知道：
- 一次 run 花了多少钱
- 哪个步骤最贵
- 哪个 module 的 retry 浪费了最多的钱

**建议：** 在 CCResult 中捕获 CC 输出的 token 计数（CC stdout 通常包含 usage 信息），累积到 state file。

### 8. 并发限制知识散落各处

- README 没提 GLM ≤5 并发
- USER-GUIDE 第 11 章提了
- 但 `load_config` 默认 concurrency=5 → **新用户不设就会触发限流**

**建议：** CLI 运行时如果 concurrency > 5，打 warning："并发 >5 可能触发 API 限流，建议 --concurrency 5"。

### 9. 缺少"安全网"——客户怕 CC 搞坏代码

QA 问："CC 会不会改坏源码？"
README 说"GitCheckpoint 确保即使 CC 改了源码，rollback 时也会恢复"。

但客户想问的是：
- "能不能限制 CC 只写 tests/ 目录？"
- "能不能在 PR 创建前跑一遍全量测试？"
- "CC 改了 src/ 目录怎么办？有没有告警？"

**建议：**
- 加一个 step 级的 `allowed_paths` 配置（类似 judge 的 `allowedTools`）
- postcondition 支持 `git diff --name-only | grep -v tests/` 检查是否有越界修改

### 10. 没有 dry-run 模式

客户改了 YAML 想验证配置是否正确，但不想真的调 CC API。

**建议：** `cc-pipeline run config.yaml --dry-run` → 只编译 pipeline、检查变量替换、验证 postcondition 语法、列出将要执行的步骤——但不调 CC。

---

## 📊 总结评分

| 维度 | 评分 | 说明 |
|---|:-:|---|
| **核心价值** | ⭐⭐⭐⭐⭐ | 三层信任 + git 原生 + 并行编排，差异化明显 |
| **上手门槛** | ⭐⭐☆☆☆ | 无可运行示例，配置链路长，新用户容易放弃 |
| **文档完整度** | ⭐⭐⭐⭐ | USER-GUIDE + CONFIG-GUIDE 很详细，但缺 quickstart demo |
| **可观测性** | ⭐⭐☆☆☆ | 只有 JSONL transcript，无报告/可视化/成本追踪 |
| **调试体验** | ⭐⭐☆☆☆ | CC prompt 不记录，失败原因难追溯 |
| **安全信心** | ⭐⭐⭐☆☆ | git checkpoint 有保护，但缺路径白名单和越界告警 |
| **通用性** | ⭐⭐⭐⭐ | YAML DSL 灵活，但太宽导致没一个场景做透 |

---

## 如果只能做 3 件事

1. **加 `examples/quickstart/`** — 5 分钟内跑通第一个 pipeline（用 shell executor，不需要 CC API）
2. **加 `cc-pipeline report`** — 生成 Markdown 运行报告，客户能直接贴到 IM 群里汇报
3. **记录完整 CC prompt 到 transcript** — 这是调试体验的最大短板
