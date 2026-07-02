# cc-pipeline 配置文件完全指南

> 所有字段的完整说明、默认值、示例

---

## 全局字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:----:|--------|------|
| `repo` | string | ✅ | — | 被处理的 git 仓库根路径 |
| `base_branch` | string | | `main` | worktree 创建基准分支 |
| `concurrency` | int | | `5` | module 间并行数（GLM 建议 ≤5） |
| `max_retries` | int | | `3` | 全局默认重试次数（可被 step 级覆盖） |
| `output_branch_prefix` | string | | `ut-auto` | worktree 分支前缀 |

---

## Pipeline 字段

`pipeline` 是一个列表，每个元素是一个 step。

### Step 字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|:----:|--------|------|
| `id` | string | ✅ | — | 步骤唯一标识（用于 git tag、日志、depends_on） |
| `executor` | enum | | `claude-code` | 执行器类型：`claude-code` / `shell` / `judge` |
| `prompt` | string | | `""` | 发送给 executor 的指令（支持 `{变量}` 注入） |
| `loop` | string | | `null` | `per_file` = 对 source_files 逐文件串行执行 |
| `retry` | int | | 全局 `max_retries` | 该步最大重试次数 |
| `depends_on` | string | | `null` | 前置步骤 id（声明依赖，控制执行顺序） |
| `postcondition` | dict | | `null` | 通过条件（见下方） |
| `output` | string | | `null` | CC 产出状态文件名（写入 `.pipeline/{output}`） |
| `skill` | string | | `null` | CC 加载的 skill 名称（预留） |

### Executor 类型详解

| Executor | prompt 行为 | 信任度 | 典型用途 |
|---------|------------|--------|---------|
| `claude-code` | 注入上下文 + output 指令 | ❌ 不可信 | 生成代码/测试/文档 |
| `shell` | 原始命令，不注入 | ✅ 可信 | 编译/测试/lint/覆盖率 |
| `judge` | 注入上下文，只读权限 | 🔶 半可信 | AI 质量评测/审查 |

**关键区别：**
- `claude-code` 和 `judge`：自动注入 `.pipeline/*.json` + `progress.md` 到 prompt
- `shell`：prompt 就是 shell 命令本身，不做任何注入
- `judge`：CC 以只读模式运行（allowedTools = Read + Bash）

### Postcondition 字段

```yaml
postcondition:
  shell: "命令"          # 必填：要执行的 shell 命令
  expect: "表达式"       # 可选：对 stdout 的期望表达式
```

| expect 表达式 | 说明 | 示例 |
|--------------|------|------|
| 省略 | shell 退出码 0 即通过 | — |
| `contains('text')` | stdout 包含文本 | `contains('passed')` |
| `$.field >= N` | JSON 字段数值比较 | `$.line >= 80` |
| `$.field == value` | 等于 | `$.errors == 0` |
| `$.field != value` | 不等于 | `$.status != "fail"` |
| `$.a >= 80 && $.b >= 70` | AND 组合 | 行+分支覆盖率 |
| `$.field > N` | 大于 | `$.score > 60` |
| `$.field < N` | 小于 | `$.errors < 5` |

---

## Module 字段

`modules` 是一个列表，每个元素是一个处理单元。

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `name` | string | ✅ | 模块名（唯一标识，用于 worktree/branch/tag） |
| `spec_id` | string | | 规格编号（注入 `{spec_id}` 变量） |
| `source_dir` | string | | 源码目录（注入 `{source_dir}` 变量） |
| `source_files` | list | | 被处理文件列表（`loop: per_file` 时逐个迭代） |
| `coverage` | dict | | 覆盖率阈值，注入为变量 |
| `variables` | dict | | 自定义变量（注入 prompt） |

### coverage 子字段

| 字段 | 注入变量名 | 示例值 |
|------|-----------|--------|
| `line_threshold` | `{line_threshold}` | 80 |
| `branch_threshold` | `{branch_threshold}` | 70 |

### variables 子字段

```yaml
variables:
  mock_strategy: link-time    # → {mock_strategy} = "link-time"
  target_framework: dtest      # → {target_framework} = "dtest"
```

---

## 变量注入

### 标准变量

| 变量 | 来源 | 示例 |
|------|------|------|
| `{module}` | module.name | `auth` |
| `{file}` | loop 当前文件 | `auth_login.c` |
| `{source_dir}` | module.source_dir | `src/auth/` |
| `{spec_id}` | module.spec_id | `SPEC-001` |
| `{line_threshold}` | module.coverage.line_threshold | `80` |
| `{branch_threshold}` | module.coverage.branch_threshold | `70` |
| `{自定义}` | module.variables.xxx | `link-time` |

### 文件注入

| 语法 | 行为 |
|------|------|
| `{.pipeline/xxx.json}` | 读取文件内容，替换到 prompt 中 |

### 自动注入（仅 claude-code/judge）

| 注入内容 | 来源 | 条件 |
|---------|------|------|
| `进度记录` 段 | `.pipeline/progress.md` | 文件存在时 |
| `前序步骤的上下文` 段 | `.pipeline/*.json` 所有文件 | 目录非空时 |
| output 写入指令 | step.output | output 字段存在时 |

---

## CLI 命令

```bash
# 运行 pipeline
cc-pipeline run <config.yaml> [options]
cc-pipeline run config.yaml --concurrency 5 --model glm-4.6
cc-pipeline run config.yaml --module auth          # 只跑一个 module

# 断点恢复
cc-pipeline resume <config.yaml> --run-dir <prev_run_dir>

# 查看状态
cc-pipeline status
cc-pipeline status --run-id <id>
```

### CLI 参数

| 参数 | 适用命令 | 说明 |
|------|---------|------|
| `config` | run, resume | YAML 配置文件路径 |
| `--concurrency N` | run, resume | module 并行数 |
| `--model MODEL` | run, resume | CC 模型名 |
| `--module NAME` | run | 只运行指定 module |
| `--run-dir DIR` | run, resume | 运行输出目录 |

---

## 完整配置示例

### 场景 1：UT 自动生成（C 嵌入式）

```yaml
repo: /path/to/embedded-project
base_branch: develop
concurrency: 5
max_retries: 3

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "为 {module} 生成 dtest 测试脚手架"
    output: scaffold.json
    postcondition:
      shell: "test -d tests/{module}"

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "为 {file} 生成测试用例，覆盖率要求行≥{line_threshold}%"
    output: generate.json
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold}"
    retry: 3
    depends_on: scaffold

  - id: evaluate
    executor: judge
    prompt: "评估测试质量"
    output: evaluate.json
    postcondition:
      shell: "test $(cat .pipeline/score) -ge 60"
    depends_on: generate

modules:
  - name: auth
    spec_id: SPEC-2026-001
    source_dir: src/auth/
    source_files: [auth_login.c, auth_token.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
    variables: {mock_strategy: link-time}
```

### 场景 2：代码审查

```yaml
repo: /path/to/project
base_branch: main
concurrency: 3

pipeline:
  - id: analyze
    executor: claude-code
    prompt: "审查 {module} 的代码质量，检查 bug/规范/安全风险"
    output: analyze.json
    postcondition:
      shell: "test -f reviews/{module}/analysis.json"

  - id: score
    executor: shell
    prompt: "/usr/bin/python3 score.py reviews/{module}/analysis.json > .pipeline/score.json"
    postcondition:
      shell: "test -f .pipeline/score.json"
    depends_on: analyze

  - id: report
    executor: claude-code
    prompt: "基于分析和评分生成 Markdown 审查报告"
    output: report.json
    postcondition:
      shell: "test -f reviews/{module}/report.md"
    depends_on: score

modules:
  - name: payment
    source_dir: src/payment/
    source_files: [payment_process.py]
  - name: auth
    source_dir: src/auth/
    source_files: [auth.py]
```

### 场景 3：技术债清理

```yaml
repo: /path/to/project
base_branch: main
concurrency: 2

pipeline:
  - id: scan
    executor: shell
    prompt: "cppcheck --xml --enable=all src/{module}/ 2> .pipeline/issues.xml"
    postcondition:
      shell: "test -f .pipeline/issues.xml"

  - id: fix
    executor: claude-code
    loop: per_file
    prompt: "修复 {file} 中的静态分析告警"
    output: fix.json
    postcondition:
      shell: "cppcheck --enable=all src/{module}/{file} 2>&1 | grep -c 'error' || true"
      expect: "contains('0')"
    retry: 2
    depends_on: scan

  - id: test
    executor: shell
    prompt: "make test"
    postcondition:
      shell: "test $? -eq 0"
    depends_on: fix

modules:
  - name: core
    source_dir: src/core/
    source_files: [parser.c, tokenizer.c, ast.c]
  - name: net
    source_dir: src/net/
    source_files: [socket.c, http.c]
```

### 场景 4：API 文档生成

```yaml
repo: /path/to/project
base_branch: main
concurrency: 4

pipeline:
  - id: scan
    executor: shell
    prompt: "ctags -R --output-format=json src/{module}/ > .pipeline/tags.json"
    postcondition:
      shell: "test -f .pipeline/tags.json"

  - id: document
    executor: claude-code
    loop: per_file
    prompt: "为 {file} 生成 API 文档，写入 docs/{module}/{file}.md"
    output: document.json
    postcondition:
      shell: "test -f docs/{module}/{file}.md"
    depends_on: scan

  - id: lint
    executor: shell
    prompt: "markdownlint docs/{module}/ --fix"
    postcondition:
      shell: "markdownlint docs/{module}/ 2>&1 | grep -c 'error' || echo 0"
      expect: "contains('0')"
    depends_on: document

modules:
  - name: api_v1
    source_dir: src/api/v1/
    source_files: [handlers.py, models.py, auth.py]
  - name: api_v2
    source_dir: src/api/v2/
    source_files: [handlers.py, models.py]
```

---

## Prompt 编写经验

### Python 测试场景

| 规则 | 原因 |
|------|------|
| **不要** 创建 `tests/{module}/__init__.py` | 与 `src/{module}` 包名冲突，pytest 9.x 报 ModuleNotFoundError |
| **需要** `src/{module}/__init__.py` | 否则 pytest 不识别为 package |
| 用 `conftest.py` 做 sys.path | 在 worktree 根创建 `conftest.py`，内容 `import sys,os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))` |
| postcondition 用 `/usr/bin/python3` | 系统可能有多版本，避免解析到 venv 的 python3 |
| 不要创建虚拟环境 | CC 容易卡在 `pip install`，浪费时间 |

### 通用 Prompt 原则

| 规则 | 说明 |
|------|------|
| 明确写入路径 | "写入 tests/{module}/test_xxx.py" 而不是 "生成测试文件" |
| 明确不要做什么 | "不要创建虚拟环境，不要安装依赖" |
| 给 output 指令留空间 | CC 会自动收到 "请将结果写入 .pipeline/{output}" 的追加指令 |
| postcondition 用 shell 检查文件 | `test -f path` 比检查内容更可靠 |
