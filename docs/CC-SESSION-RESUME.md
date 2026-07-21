# CC Session Resume 设计说明书

> 版本：0.3 | 日期：2026-07-17 | CC 审查修正

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
| `--resume <uuid> -p <prompt>` | ⚠️ 待验证 | 如果支持，可同时 resume 上下文 + 注入新 prompt |

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
  
  while retry:
      if first_attempt and not session_id:
          session_id = uuid4()
          state_manager.set_cc_session(module, step_id, loop_file, session_id)
      
      exec_result = _execute_step(step, session_id=session_id,
                                   resume_session=(not first_attempt 
                                   and outcome in (TIMEOUT, UNKNOWN_ERROR)))
      
      if exec_result.outcome == PASS:
          state_manager.clear_cc_session(module, step_id, loop_file)
          break
      elif exec_result.outcome in (TIMEOUT, UNKNOWN_ERROR):
          retry_budget -= 1
          continue  # ← 用同一个 session_id resume
      elif exec_result.outcome == CC_FAILED:
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
                   "--dangerously-skip-permissions"]
        elif session_id:
            cmd = ["claude", "-p", prompt,
                   "--session-id", session_id, "--print",
                   "--dangerously-skip-permissions", "--model", self.default_model]
        else:
            # 降级：无 session
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
| Shell executor | session 管理全部 skip |
| max_retries=0 | 不生成 session |
| 并行执行 | 模块级并行，同 step 不会多线程冲突 |

---

## 七、持久化策略

- UUID 通过 `StateManager` 写入 `state.json`（已有原子写入 + 锁）
- cc-pipeline 崩溃重启后 → 读 `state.json` → UUID 仍存在 → resume 可用
- CC 自身 session 由 CC 管理（磁盘存储）
- 废弃 session 由 CC 自身清理（cc-pipeline 不管理）
