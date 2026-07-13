# cc-pipeline 测试方法论：失败路径覆盖规则

## 根因

测试验证了"做对了什么"，没验证"做错了时用户看到了什么"。
646 个测试验证正确路径，但 merge 冲突、module 异常等失败路径
从不打印到终端——因为测试只检查 return code，不检查终端输出。

## 6 条规则

### 规则 1：每个 except 必须有 capsys 测试

代码里每写一个 except，就必须有一个测试验证它打印了什么。
检查方法：`grep -n "except" src/` 的数量 ≈ `grep "capsys" tests/` 的数量。

### 规则 2：每个失败状态必须有终端输出测试

`result["status"] = "failed"` 的地方，测试必须断言终端输出了失败原因。
不只是 `assert status == "failed"`，还要 `assert "失败原因" in capsys.out`。

### 规则 3：新功能必须同时写成功路径 + 失败路径测试

新功能 = 成功测试 + 失败测试（配对出现）。
不允许只有成功路径，没有失败路径。

### 规则 4：capsys 断言不能只检查 return code

```python
# ❌ 差
assert result["status"] == "failed"

# ✅ 好
assert result["status"] == "failed"
out = capsys.readouterr().out
assert "失败原因" in out
```

### 规则 5：每个用户可见的 print 语句对应一个测试

```python
# 代码
print(f"⚠️ Merge conflict — worktree preserved")
# 测试
assert "Merge conflict" in capsys.readouterr().out
```

### 规则 6：mock 绕过的错误链必须有 E2E 验证

mock 绕过了真实 git/subprocess 错误。每个 mock 测的错误路径，
至少 1 个 E2E 用真实 git repo + 真实冲突验证。

## 自检清单

每次写新功能时对照：

1. 新功能有失败路径吗？→ 有 → 必须有失败测试
2. 失败路径有 print 吗？→ 有 → capsys 断言
3. 新增了 except 块吗？→ 有 → 对应 capsys 测试
4. 新增了状态变更（status=failed）吗？→ 有 → 终端输出测试
5. mock 绕过了真实错误链吗？→ 至少 1 个 E2E 用真实 git/subprocess

## 规则 7：所有 subprocess 调用必须 check=True 或 try/except

```python
# ❌ 差
result = subprocess.run(["git", "reset", "--hard", tag], capture_output=True)
# 失败静默，后续操作在脏状态上跑

# ✅ 好
result = subprocess.run(["git", "reset", "--hard", tag], capture_output=True, text=True)
if result.returncode != 0:
    raise RuntimeError(f"git reset failed: {result.stderr.strip()}")
```

## 规则 8：功能组合矩阵

新功能不仅要测自身行为，还要检查与现有功能的**每种组合**是否定义了行为。

### 组合矩阵模板

| | per_file | retry | on_failure | resume | postcondition |
|---|:-:|:-:|:-:|:-:|:-:|
| 新功能 | ✓/✗ | ✓/✗ | ✓/✗ | ✓/✗ | ✓/✗ |

每个 ✓ 必须有测试。每个 ✗ 必须说明"为什么不测"。

### 关键假设审计

每个功能实现时写一个"假设清单"。加新功能时检查：

> 新功能是否打破了已有功能的假设？

典型破裂案例：
- `on_failure` 假设 step_id 唯一 → `per_file` 展开后 step_id 不唯一 → bug
- `resume` 假设 step 可用 step_id 标识 → `per_file` 需要 step_id + loop_file → 已修复

### 执行单元 key 一致性

系统中标识一个"执行单元"的 key 必须全局一致：

```
非 loop step：step_id
loop step：    step_id + loop_file

所有操作（jump / skip / complete / retry）必须用同一个 key。
不允许 jump 用 step_id 而 skip 用 step_id + loop_file——不一致就是 bug。
```

### 新增功能检查清单

```
□ 与 per_file 组合时行为是否正确？→ 写组合测试
□ 与 retry 组合时行为是否正确？→ 写组合测试
□ 与 on_failure 组合时行为是否正确？→ 写组合测试
□ 与 resume 组合时行为是否正确？→ 写组合测试
□ 该功能的 key 是否和系统其他部分一致？
□ 该功能假设 step_id 唯一吗？per_file 展开后是否还成立？
□ compiled steps 可视化：展开后 step_id 是否唯一？不唯一时搜索逻辑是否正确？
```
