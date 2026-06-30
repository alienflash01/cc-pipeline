# cc-pipeline — Claude Code Pipeline Orchestrator

> 多阶段串行流水线编排框架，支持 module 间并行 + module 内串行 + CC 间上下文传递

## 项目定位

`cc-pipeline` 是一个 **声明式 Pipeline 编排框架**，用于编排 Claude Code（CC）的多阶段串行任务。

核心场景：为 C 嵌入式工程自动生成单元测试（UT），但不限于此场景。

## 快速链接

- [设计方案](docs/DESIGN.md) — 架构、Pipeline DSL、状态管理、Executor 模型
- [开发计划](docs/ROADMAP.md) — 分阶段里程碑、任务分解、时间线
