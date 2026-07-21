# CC Session Resume 设计说明书

> 版本：0.4 | 日期：2026-07-16 | 秘书审查修正
>
> **v0.4 变更摘要**（基于 v0.3 审查）：
> - ⚠️ `--resume -p` 组合标记为**前置阻塞验证项**
> - 🔧 3.3 伪代码修复 `last_outcome` 变量传递缺口
> - 🔧 resume 分支补齐 `--model` 参数
> - 📝 补充 session TTL、per_file 并发写入安全、降级路径说明
> - 📝 新增测试策略、可观测性、安全考量章节

---

## 一、动机

| 场景 | CC 状态 | 当前行为 | 问题 |
|------|---------|---------|------|
| **超时/崩溃** (TIMEOUT, UNKNOWN_ERROR) | 任务未完成 | retry → 全新 CC 会话 | 浪费部分进度 |
| **正常完成但失败** (exit≠0, postcondition fail) | 任务已完成 | retry → 全新 CC 会话 | **正确行为，不变** |

---

## 二、CC CLI 验证

| 能力 | 支持 | 说明 |
|------|:---:|------|
| `--session-id <uuid>` | ✅ | 必须 UUID 格式 |
| `--resume <uuid>` | ✅ | 恢复指定会话 |
| `--resume <uuid> -p <prompt>` | ⚠️ **前置阻塞验证项** | 核心依赖。需先验证此组合是否被 CC 支持（恢复上下文 + 注入新 prompt）。若不支持，整个 resume 路径需改为 `--resume` 后通过 stdin 注入 prompt，或其他替代方案。**在实现前必须完成验证。** |

---

## 三、设计修正（CC 审查后）

### 3.1 P0-1 修正：Timeout 分类 bug

**当前问题**：`CCExecutor.run()` 内部 catch `TimeoutExpired` 返回 `CCResult(returncode=-1)`，导致 runner 错误分类为 `CC_FAILED`。

**修正**：`CCExecutor.run()` 不再 catch `TimeoutExpired`，让它向上抛。runner 层统一 catch 并分类为 `ExecOutcome.TIMEOUT`。

```python
# executor.py: 删除内部 try/except TimeoutExpired
# runner.py: 已有 catch TimeoutExpired → TIMEOUT (但目前是死代码，修正后激活)
```

### 3.2 P0-2 修正：resume 时传入 prompt

`--resume <uuid> -p <prompt>` 组合传给 CC：恢复上下文 + 注入最新 prompt（含 context var）。

```python
if resume_session and session_id:
    cmd = ["claude", "--resume", session_id, "-p", full_prompt, "--print", ...]
else:
    cmd = ["claude", "-p", full_prompt, "--session-id", session_id, "--print", ...]
```

### 3.3 P0-3 修正：session 管理位置

正确位置：**`runner.run()` 的 retry 循环内部**，不是 `_execute_step`。

```
runner.run():
  session_id = state_manager.get_cc_session(module, step_id, loop_file)
  last_outcome = None  # ← 显式初始化，跨迭代传递上一轮结果

  while retry:
      first_attempt = (last_outcome is None)

      if first_attempt and not session_id:
          session_id = uuid4()
          state_manager.set_cc_session(module, step_id, loop_file, session_id)

      # resume 条件：非首次 且 上一轮为 TIMEOUT/UNKNOWN_ERROR
      resume_session = (not first_attempt
                        and last_outcome in (TIMEOUT, UNKNOWN_ERROR))

      exec_result = _execute_step(step, session_id=session_id,
                                   resume_session=resume_session)
      last_outcome = exec_result.outcome  # ← 更新，供下一轮判断

      if last_outcome == PASS:
          state_manager.clear_cc_session(module, step_id, loop_file)
          break
      elif last_outcome in (TIMEOUT, UNKNOWN_ERROR):
          retry_budget -= 1
          continue  # ← 用同一个 session_id resume
      elif last_outcome == CC_FAILED:
          state_manager.clear_cc_session(module, step_id, loop_file)
          session_id = uuid4()  # ← 换新 UUID
          state_manager.set_cc_session(module, step_id, loop_file, session_id)
          retry_budget -= 1
          continue
```

### 3.4 P1-4 修正：on_failure 跳转清除 cc_sessions

`StateManager.clear_step_completed()` 同步清除对应 `cc_sessions`。

---

## 四、CCExecutor 接口

```python
class CCExecutor:
    def run(self, prompt: str, cwd: str, *,
            session_id: str = None,
            resume_session: bool = False) -> CCResult:
        """Execute CC.

        Args:
            prompt: Full prompt (already resolved, context injected)
            cwd: Working directory
            session_id: UUID for CC session (None = no session)
            resume_session: If True, use --resume instead of -p
        """
        if resume_session and session_id:
            cmd = ["claude", "--resume", session_id,
                   "-p", prompt, "--print",
                   "--dangerously-skip-permissions",
                   "--model", self.default_model]
        elif session_id:
            cmd = ["claude", "-p", prompt,
                   "--session-id", session_id, "--print",
                   "--dangerously-skip-permissions", "--model", self.default_model]
        else:
            # 降级：无 session（仅当 max_retries=0 或显式禁用 session 时触发）
            # 正常流程不会走到这里——runner 层总会生成 UUID
            # 保留此分支作为防御性兜底
            cmd = ["claude", "-p", prompt, "--print",
                   "--dangerously-skip-permissions", "--model", self.default_model]
        
        # 不 catch TimeoutExpired — 由 runner 层处理
        result = subprocess.run(cmd, ...)
        ...
```

---

## 五、StateManager 新增方法

```python
def set_cc_session(self, module: str, step_id: str, loop_file: str, uuid: str)
def get_cc_session(self, module: str, step_id: str, loop_file: str) -> str | None
def clear_cc_session(self, module: str, step_id: str, loop_file: str)
```

### state.json 格式

```json
{
  "modules": {
    "auth": {
      "completed_steps": ["generate/a.c"],
      "cc_sessions": {
        "generate": {"a.c": "550e8400-..."}
      }
    }
  }
}
```

---

## 六、边界条件

| 场景 | 行为 |
|------|------|
| `--resume` 不可用 | 降级：正常 `-p`（CC 不支持时） |
| session 已过期 | CC 报错 → catch 非 0 → 清 UUID → 新 session |
| on_failure 跳转 | `clear_step_completed` 同步清理 `cc_sessions` |
| postcondition fail | 视为 CC_FAILED → 清 UUID → 新 session |
| per_file：同 step 不同文件 | 各自独立 UUID（loop_file 不同） |
| per_file 并发写入 | 同 step 多个 loop_file 并行时，各自写 `cc_sessions[step_id][loop_file]` 不同 key，StateManager 原子写入 + 锁保证安全。**需确认锁粒度覆盖 field 级并发，而非仅 module 级。** |
| Shell executor | session 管理全部 skip |
| max_retries=0 | 不生成 session |
| 并行执行 | 模块级并行，同 step 不会多线程冲突 |
| pipeline 崩溃重启 | 读 `state.json` → UUID 仍存在 → resume 可用 |
| Session TTL | **需验证 CC session 有效期限。** 若 pipeline 暂停数小时/数天后 resume，session 可能已过期。建议：pipeline 层设置 `max_resume_window`（默认 24h），超期则废弃旧 UUID，走全新 session。 |

---

## 七、持久化策略

- UUID 通过 `StateManager` 写入 `state.json`（已有原子写入 + 锁）
- cc-pipeline 崩溃重启后 → 读 `state.json` → UUID 仍存在 → resume 可用
- CC 自身 session 由 CC 管理（磁盘存储）
- 废弃 session 由 CC 自身清理（cc-pipeline 不管理）

---

## 八、测试策略

| 层级 | 方法 | 覆盖场景 |
|------|------|----------|
| **单元测试** | Mock `CCExecutor`，验证 runner 层 session 生命周期 | UUID 生成/resume/清除/CC_FAILED 换新 |
| **单元测试** | Mock `subprocess.run`，验证 CCExecutor 拼装 cmd 参数 | `--session-id` vs `--resume -p` vs 降级路径 |
| **单元测试** | Mock StateManager，验证 `set/get/clear_cc_session` 原子性 | 并发 per_file 写入、崩溃恢复 |
| **集成测试** | 使用 CC smoke test（`echo OK` 级 prompt），验证 resume 真实可用 | 超时 → resume → pass 全链路 |
| **集成测试** | 模拟 CC 返回非 0，验证 CC_FAILED 换新 session | 正常失败不 resume |
| **前置验证** | 手动执行 `claude --resume <uuid> -p <prompt> --print` | 确认 CC 支持此组合（**阻塞性**） |

---

## 九、可观测性

```python
# 建议在以下关键节点输出结构化日志（structlog / logging）

logger.info("cc_session_created", module=module, step_id=step_id,
            loop_file=loop_file, session_id=session_id)

logger.info("cc_session_resumed", module=module, step_id=step_id,
            loop_file=loop_file, session_id=session_id,
            last_outcome=last_outcome)

logger.info("cc_session_cleared", module=module, step_id=step_id,
            loop_file=loop_file, session_id=session_id, reason="pass|cc_failed|on_failure")

logger.warning("cc_session_expired", module=module, step_id=step_id,
               loop_file=loop_file, session_id=session_id,
               reason="ttl_exceeded|cc_error")
```

- 日志级别：`INFO`（正常生命周期）、`WARNING`（降级/过期）、`ERROR`（resume 失败）
- 建议在 `state.json` 中记录 `session_created_at` 时间戳，便于 TTL 判断和调试

---

## 十、安全考量

| 风险 | 评估 | 缓解措施 |
|------|------|----------|
| `--dangerously-skip-permissions` | CC 在无权限确认下执行文件读写、命令执行 | 仅在受控 CI/CD 环境中使用；本地开发时提供 `--require-permissions` 开关降级 |
| Session UUID 泄露 | UUID 写入 `state.json`，若仓库泄露可被用于恢复会话 | `state.json` 加入 `.gitignore`；UUID 为一次性，pass/fail 后清除 |
| Prompt 注入 | resume 时注入新 prompt，可能被恶意 context var 污染 | context var 在注入前做转义/校验；prompt 模板不可被外部输入覆盖 |
