# CC Session Resume 设计说明书

> 版本：0.2 | 日期：2026-07-17 | 通过圆桌审查

---

## 一、动机

| 场景 | CC 状态 | 当前行为 | 问题 |
|------|---------|---------|------|
| **超时/崩溃** (TIMEOUT, UNKNOWN_ERROR) | 任务未完成 | retry → 全新 CC 会话 | 浪费部分进度 |
| **正常完成但失败** (exit≠0, postcondition fail) | 任务已完成 | retry → 全新 CC 会话 | **正确行为，不变** |

**目标：超时/崩溃的 retry 复用 CC 会话上下文。**

---

## 二、CC CLI 验证

| 能力 | 支持 | 说明 |
|------|:---:|------|
| `--session-id <uuid>` | ✅ | 必须 UUID 格式 |
| `--resume <uuid>` | ✅ | 恢复指定会话 |
| 输出 session ID | ❌ | 框架自己用 `uuid4()` 生成 |

---

## 三、设计方案（圆桌修正版）

### 3.1 核心原则

1. **UUID 绑定到 step，持久化到 state.json**（cc-pipeline 崩溃也能恢复）
2. **step_key 用 tuple：`(module, step_id, loop_file)`**（不拼字符串）
3. **首次执行生成 UUID，每次超时/崩溃 retry 用同一个 UUID resume；只有"正常失败"才换新 UUID**

### 3.2 执行流程

```
Step (module="auth", step_id="generate", loop_file="a.c") 首次：

  读 state.json → 无 session → uuid4()
  存 state.json: sessions["auth"]["generate"]["a.c"] = uuid
  claude -p "prompt" --session-id <uuid>

Step 执行结果：

  ├── PASS
  │     → 清 state.json 中的 session
  │
  ├── TIMEOUT / UNKNOWN_ERROR
  │     → retry: claude --resume <uuid>  （复用 CC 上下文）
  │     → 仍失败 → retry_budget 减少，继续
  │
  └── CC_FAILED (exit≠0)
        → 清 state.json 中的 session
        → retry: uuid4() → 新 session → 全新 CC 会话

max_retries=3 混合场景：

  尝试 1: TIMEOUT → --resume <uuid-A>
  尝试 2: CC_FAILED → uuid4() → --session-id <uuid-B>
  尝试 3: TIMEOUT → --resume <uuid-B>  （uuid-A 已清，uuid-B 是当前绑定的）
```

### 3.3 state.json 格式

```json
{
  "modules": {
    "auth": {
      "status": "running",
      "completed_steps": ["generate/a.c"],
      "cc_sessions": {
        "generate": {
          "a.c": "550e8400-e29b-41d4-a716-446655440000"
        }
      }
    }
  }
}
```

**每个 step 最多 1 个活跃 UUID。PASS 后清除，失败后换新。**

### 3.4 StateManager 新增方法

```python
def set_cc_session(self, module: str, step_id: str, loop_file: str, uuid: str) -> None
def get_cc_session(self, module: str, step_id: str, loop_file: str) -> str | None
def clear_cc_session(self, module: str, step_id: str, loop_file: str) -> None
```

### 3.5 CCExecutor 改动

```python
class CCExecutor:
    def run(self, prompt, cwd, step_id="", loop_file="",
            resume_session=False, session_id=None):
        if resume_session and session_id:
            cmd = ["claude", "--resume", session_id, "--print"]
        elif session_id:
            cmd = ["claude", "-p", prompt, "--session-id", session_id, "--print"]
        else:
            cmd = ["claude", "-p", prompt, "--print"]  # 降级：无 session
```

**不持有状态。session_id 由 runner 从 state.json 读取并传入。**

### 3.6 runner.py 改动

```python
# _execute_step 重试逻辑：

session_id = self.state_manager.get_cc_session(module, step_id, loop_file)

if exec_result.outcome in (TIMEOUT, UNKNOWN_ERROR):
    # 中断 → 不换 UUID，用同一个 resume
    resume = True
elif exec_result.outcome == CC_FAILED:
    # 正常失败 → 清旧 UUID，生成新 UUID
    self.state_manager.clear_cc_session(module, step_id, loop_file)
    session_id = str(uuid.uuid4())
    self.state_manager.set_cc_session(module, step_id, loop_file, session_id)
    resume = False
else:
    # 首次执行 → 生成 UUID
    session_id = str(uuid.uuid4())
    self.state_manager.set_cc_session(module, step_id, loop_file, session_id)
    resume = False

exec_result = self.cc_executor.run(prompt, cwd, step_id, loop_file,
                                    resume_session=resume, session_id=session_id)
```

---

## 四、边界条件

| 场景 | 行为 |
|------|------|
| CC `--resume` 不可用 | 降级：正常 `-p` |
| session 已过期 | CC 报错 → 清 session → 正常 `-p` 重试 |
| cc-pipeline 崩溃重启 | state.json 有 UUID → resume 可用 |
| per_file：同 step 不同文件 | 各自独立 UUID（loop_file 不同） |
| max_retries=0 | 不生成 session（无意义） |
| postcondition fail | 视为"正常完成但失败"→ 换新 UUID |

---

## 五、测试计划

| # | 测试 | 验证点 |
|---|------|--------|
| 1 | 首次执行 | cmd 含 `--session-id <uuid>` |
| 2 | TIMEOUT retry | cmd 含 `--resume <same-uuid>` |
| 3 | CC_FAILED retry | cmd 含 `--session-id <new-uuid>` |
| 4 | PASS 后清 session | `get_cc_session()` → None |
| 5 | 崩溃后恢复 | state.json 中有 UUID → resume 可用 |
| 6 | per_file 独立 | a.c 和 b.c 不同 UUID |
| 7 | --resume 不可用降级 | cmd 不含 `--resume`，走正常 `-p` |

---

## 六、不改的

- **不影响 postcondition retry。**
- **不传 session_id 给用户层。**
- **Shell executor 不受影响。**
