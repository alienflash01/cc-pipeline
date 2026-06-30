# cc-pipeline 测试方案

> 遵循 TDD：RED → GREEN → REFACTOR，测试先行

---

## 测试策略

### 分层测试

```
┌────────────────────────────────────────────────┐
│  Layer 1: 单元测试（纯逻辑，无外部依赖）          │
│  ├── Config Loader（YAML 解析、变量注入）         │
│  ├── Pipeline Compiler（DSL → Step 序列）        │
│  ├── Postcondition Evaluator（表达式评估）        │
│  ├── State Manager（JSON 读写）                  │
│  └── Gate Evaluator（通过/重试/失败判定）          │
├────────────────────────────────────────────────┤
│  Layer 2: 集成测试（有 git/subprocess，不调 CC）   │
│  ├── Git Checkpoint（commit + tag + rollback）   │
│  ├── Shell Executor（subprocess 执行）           │
│  ├── Worktree Manager（创建/删除/清理）           │
│  └── Retry + Rollback 循环                      │
├────────────────────────────────────────────────┤
│  Layer 3: 端到端测试（调真实 CC 或 mock CC）       │
│  ├── 单 module 单步 pipeline                     │
│  ├── 单 module 多步 pipeline                     │
│  ├── 多 module 并行 pipeline                     │
│  └── retry + 失败恢复                            │
└────────────────────────────────────────────────┘
```

### 测试框架

```python
# pytest + pytest-mock
# Git 操作用真实临时目录（tmp_path fixture）
# CC 调用在 Layer 3 用 mock（除非用户要求真实 E2E）
```

---

## Layer 1: 单元测试

### 1.1 Config Loader

```python
class TestConfigLoader:
    """测试 YAML 配置解析。"""
    
    def test_loads_modules_from_yaml(self, tmp_path):
        """能正确解析 modules 列表。"""
        # RED: 写一个 yaml，assert 解析出的 module 数量正确
    
    def test_parses_pipeline_steps_in_order(self, tmp_path):
        """pipeline 步骤按 YAML 声明顺序排列。"""
    
    def test_default_concurrency_is_5(self, tmp_path):
        """未指定 concurrency 时默认 5。"""
    
    def test_missing_repo_raises_error(self, tmp_path):
        """缺少必填字段 repo 时报错。"""
    
    def test_module_variables_injected_into_prompt(self, tmp_path):
        """module 级变量能注入 prompt 模板。"""
    
    def test_coverage_threshold_parsed_as_int(self, tmp_path):
        """覆盖率阈值解析为整数而非字符串。"""
```

### 1.2 Variable Renderer（变量注入）

```python
class TestVariableRenderer:
    """测试 prompt 模板变量替换。"""
    
    def test_replaces_module_name(self):
        """{module} 被替换。"""
        # RED: render("hello {module}", {"module": "auth"}) == "hello auth"
    
    def test_replaces_multiple_variables(self):
        """多个变量同时替换。"""
    
    def test_injects_json_file_content(self):
        """{.pipeline/scaffold.json} 读文件内容注入。"""
    
    def test_unknown_variable_raises_error(self):
        """未定义变量报错而非静默忽略。"""
    
    def test_nested_file_path_resolved(self):
        """{.pipeline/verified/generate.json} 能正确解析嵌套路径。"""
```

### 1.3 Postcondition Evaluator

```python
class TestPostconditionEvaluator:
    """测试 postcondition 表达式评估。"""
    
    def test_simple_comparison_passes(self):
        """$.line >= 80 在 line=85 时通过。"""
    
    def test_simple_comparison_fails(self):
        """$.line >= 80 在 line=70 时失败。"""
    
    def test_and_expression(self):
        """$.line >= 80 && $.branch >= 70 同时满足才通过。"""
    
    def test_equality_check(self):
        """$.errors == 0 精确匹配。"""
    
    def test_contains_check(self):
        """contains('passed') 在 stdout 含 'passed' 时通过。"""
    
    def test_shell_command_failure_propagates(self):
        """shell 命令退出码非 0 时 postcondition 失败。"""
    
    def test_invalid_json_output_fails(self):
        """shell 输出非 JSON 时评估失败。"""
```

### 1.4 Pipeline Compiler

```python
class TestPipelineCompiler:
    """测试 YAML pipeline → Step 序列编译。"""
    
    def test_compiles_steps_in_order(self):
        """步骤按声明顺序编译。"""
    
    def test_depends_on_resolves_ordering(self):
        """depends_on 能重排步骤顺序。"""
    
    def test_loop_per_file_expands_steps(self):
        """loop: per_file 展开为 N 个子步骤。"""
    
    def test_retry_defaults_to_config(self):
        """未指定 retry 时使用全局 max_retries。"""
    
    def test_executor_type_validated(self):
        """executor 必须是 claude-code/shell/judge 三者之一。"""
    
    def test_step_id_unique(self):
        """重复 step id 报错。"""
```

### 1.5 State Manager

```python
class TestStateManager:
    """测试 pipeline 状态 JSON 管理。"""
    
    def test_writes_orchestrator_state(self, tmp_path):
        """能写入 orchestrator-state.json。"""
    
    def test_reads_and_resumes_state(self, tmp_path):
        """能读取中断的状态并恢复。"""
    
    def test_module_state_tracks_current_step(self, tmp_path):
        """module 状态记录当前执行到哪个步骤。"""
    
    def test_retry_count_incremented(self, tmp_path):
        """每次 retry 后 attempts 计数 +1。"""
```

---

## Layer 2: 集成测试

### 2.1 Git Checkpoint

```python
class TestGitCheckpoint:
    """测试 git commit + tag + rollback。"""
    
    def test_creates_commit_with_tag(self, tmp_path):
        """step 完成后创建 commit + tag。"""
        # RED: init git repo → add file → checkpoint
        # assert git tag exists: pipeline/auth/scaffold/1
    
    def test_rollback_restores_state(self, tmp_path):
        """rollback 回到 checkpoint 状态。"""
        # RED: checkpoint → add more files → rollback → assert files gone
    
    def test_tag_format_correct(self, tmp_path):
        """tag 格式: pipeline/{module}/{step_id}/{attempt}。"""
    
    def test_rollback_preserves_previous_step(self, tmp_path):
        """回滚 generate 不影响 scaffold 的产出。"""
    
    def test_multiple_checkpoints_independent(self, tmp_path):
        """多个 checkpoint 互不影响。"""
```

### 2.2 Shell Executor

```python
class TestShellExecutor:
    """测试 shell 命令执行。"""
    
    def test_runs_command_in_worktree(self, tmp_path):
        """命令在 worktree 目录下执行。"""
    
    def test_captures_stdout_as_json(self, tmp_path):
        """stdout 被 JSON 解析写入 verified.json。"""
    
    def test_command_failure_raises(self, tmp_path):
        """命令退出码非 0 时抛异常。"""
    
    def test_timeout_kills_process(self, tmp_path):
        """超时杀死进程。"""
```

### 2.3 Worktree Manager

```python
class TestWorktreeManager:
    """测试 git worktree 生命周期。"""
    
    def test_creates_worktree_with_branch(self, tmp_path):
        """创建 worktree + 对应分支。"""
    
    def test_worktree_isolated_from_main(self, tmp_path):
        """worktree 内修改不影响主仓库。"""
    
    def test_cleanup_removes_worktree(self, tmp_path):
        """清理后 worktree 不存在。"""
    
    def test_failed_module_worktree_preserved(self, tmp_path):
        """失败 module 的 worktree 被保留。"""
```

### 2.4 Retry + Rollback

```python
class TestRetryRollback:
    """测试重试 + 回滚循环。"""
    
    def test_retry_rolls_back_before_reexecuting(self, tmp_path):
        """重试前自动回滚。"""
    
    def test_max_retries_exhausted_marks_fail(self, tmp_path):
        """重试耗尽后标记 module 失败。"""
    
    def test_pass_on_second_attempt(self, tmp_path):
        """第一次失败第二次通过时继续执行。"""
    
    def test_retry_count_in_state(self, tmp_path):
        """重试次数写入 state.json。"""
```

---

## Layer 3: 端到端测试

### 3.1 Mock CC 策略

```python
# 用 mock CC 代替真实 claude -p
@pytest.fixture
def mock_cc(monkeypatch):
    """Mock claude -p 调用，返回预设结果。"""
    calls = []
    
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # 模拟 CC 写文件
        if "generate" in str(cmd):
            worktree = kwargs.get('cwd', '.')
            Path(worktree + "/tests/test_auth.c").write_text("...")
        return MagicMock(returncode=0, stdout="passed")
    
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls
```

### 3.2 单 Module 单步

```python
class TestSingleModuleSingleStep:
    """端到端：1 module + 1 step。"""
    
    def test_generates_tests_and_passes(self, mock_cc, tmp_path):
        """CC 被调用，postcondition 通过，状态正确。"""
```

### 3.3 单 Module 多步 Pipeline

```python
class TestSingleModuleMultiStep:
    """端到端：1 module + scaffold→generate→evaluate→finalize。"""
    
    def test_four_step_pipeline_completes(self, mock_cc, tmp_path):
        """四步 pipeline 全部通过。"""
    
    def test_evaluate_failure_triggers_generate_retry(self, mock_cc, tmp_path):
        """evaluate 失败后回滚并重试 generate。"""
    
    def test_pr_created_on_success(self, mock_cc, tmp_path, mock_gh):
        """pipeline 完成后创建 PR。"""
```

### 3.4 多 Module 并行

```python
class TestMultiModuleParallel:
    """端到端：3 module 并行。"""
    
    def test_three_modules_run_concurrently(self, mock_cc, tmp_path):
        """3 个 module 同时执行，各自独立完成。"""
    
    def test_one_module_failure_others_continue(self, mock_cc, tmp_path):
        """一个 module 失败不影响其他 module。"""
    
    def test_concurrency_limits_cc_instances(self, mock_cc, tmp_path):
        """并发数限制生效，不超过 N 个 CC 同时运行。"""
```

---

## 测试目录结构

```
tests/
├── unit/                         # Layer 1
│   ├── test_config.py            # Config Loader
│   ├── test_render.py            # Variable Renderer
│   ├── test_postcondition.py     # Postcondition Evaluator
│   ├── test_compiler.py          # Pipeline Compiler
│   └── test_state.py             # State Manager
├── integration/                  # Layer 2
│   ├── test_git_checkpoint.py    # Git Checkpoint
│   ├── test_shell_executor.py    # Shell Executor
│   ├── test_worktree.py          # Worktree Manager
│   └── test_retry.py             # Retry + Rollback
├── e2e/                          # Layer 3
│   ├── test_single_module.py     # 单 module 端到端
│   ├── test_multi_module.py      # 多 module 并行
│   └── test_failure_recovery.py  # 失败恢复
├── fixtures/                     # 测试数据
│   ├── simple.yaml
│   ├── multi-module.yaml
│   └── mock_repo/                # 模拟被测仓库
└── conftest.py                   # pytest fixtures（mock_cc, mock_gh, tmp_repo）
```

---

## TDD 开发节奏

每个 Phase 按 TDD 节奏推进：

```
Phase 1 MVP:
  对每个函数：
    RED:   写 test_config.py::test_loads_modules_from_yaml（失败）
    GREEN: 写最小 ConfigLoader 让测试通过
    REFACTOR: 清理代码

  RED:   写 test_render.py::test_replaces_module_name（失败）
    GREEN: 写最小 render_prompt 函数
    REFACTOR: ...

  （逐函数推进，不一次性写所有测试）
```

---

## CI 集成

```yaml
# .github/workflows/test.yml
name: Test
on: [push, pull_request]
jobs:
  unit-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[dev]"
      - run: pytest tests/unit/ -v --cov=cc_pipeline --cov-report=term-missing
  
  integration-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[dev]"
      - run: pytest tests/integration/ -v
```

---

## 覆盖率目标

| 层 | 目标 | 说明 |
|---|------|------|
| Layer 1（单元） | ≥ 90% | 纯逻辑，必须高覆盖 |
| Layer 2（集成） | ≥ 75% | git 操作有边界情况 |
| Layer 3（E2E） | 关键路径覆盖 | 不追求高覆盖率 |
| 总体 | ≥ 80% | |
