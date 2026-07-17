# cc-pipeline -v 输出美观度分析

> 逐行解剖 | 2026-07-17

---

## 逐行分析

```
LINE | 内容                                             | 问题
─────┼──────────────────────────────────────────────────┼────────────────────────
  1  | verbose mode ON (level 1) — step progress        | 横幅可简化
  2  | 🌙 cc-pipeline 0.3.2                              | ✅ 好
  3  |    concurrency=1  modules=['app']                 | ✅ 好
  4  |                                                   |
  5  |   [07:52:23] [app] setup START                    | 4个token挤一行
  6  |   [07:52:23] [app] postcondition: echo ok         | postcondition原命令无意义
  7  |   [07:52:23] [app] setup PASS                     | ✅
  8  |   [07:52:23] [app] flaky START                    |
  9  |   ❌ Shell failed (exit 1): C=/tmp/...             | emoji+类型+码+命令=信息爆炸
 10  |   N=$(wc -l < $C); echo x >> $C; N=$((N+1))       | 多行命令截断，if [ $ 断开
 11  |   if [ $                                           | ← 完全不可读
 12  |   [07:52:23] [app] flaky ⚠️  RETRY (1)            | emoji+动作+attempt+原因
 13  |   [07:52:23] [app] flaky START                     | 与line9完全相同无区分
 14  |   ❌ Shell failed (exit 1): ...                     | 又重复一遍
 15  |   ...                                              |
 16  |   [07:52:23] [app] flaky ⚠️  RETRY (2)            |
 17  |   [07:52:23] [app] flaky START                     |
 18  |   [07:52:23] [app] postcondition: echo ok          |
 19  |   [07:52:23] [app] flaky PASS                      | ← PASS埋没在失败噪音中
 20  |   [07:52:23] [app] verify START                    |
 21  |   [07:52:23] [app] postcondition: echo {...}       | 无意义
 22  |   [07:52:23] [app] verify PASS                     |
 23  |   📁 Worktree preserved at ...                     | 3行噪音
 24  |      Branch: cc-auto/app                           |
 25  |      Manual merge: ...                             |
 26  |   ✅ app      passed  (3 steps)                    | 重复汇总1
 27  |                                                   |
 28  | ==========================================         |
 29  |   ✓ app                   passed                  | 重复汇总2
 30  | ==========================================         |
 31  |   1 passed, 0 failed  (run_id: ...)                |
```

## 六大视觉问题

### 1. 时间戳无信息量
所有 22 个事件的时间戳都是 `07:52:23`——同一秒内的操作，时间戳为零信息噪音。
**建议**：只在事件跨越不同秒时才打印时间戳。

### 2. postcondition 原命令无意义
```
[07:52:23] [app] postcondition: echo ok
[07:52:23] [app] postcondition: echo '{"score": 85}'
```
用户不关心 postcondition 跑的具体是什么命令。成功时不显示，失败时显示原因即可。
**建议**：成功时隐藏，失败时显示 `postcondition FAIL: $.score >= 80 (actual: 45)` ← 当前已有此格式，但只在 FAIL 时显示。

### 3. 多行命令粗暴截断
```
❌ Shell failed (exit 1): C=/tmp/ux-v/count.txt; touch $C
N=$(wc -l < $C); echo x >> $C; N=$((N+1))
if [ $
```
第 11 行 `if [ $` 完全不可读。多行 prompt 被当作单行输出，硬切。
**建议**：失败时只显示 exit code + stderr 最后 3 行，不显示完整命令。

### 4. emoji 使用不一致
| emoji | 含义 | 出现位置 |
|-------|------|---------|
| ❌ | 失败 | Shell failed |
| ⚠️ | 警告/重试 | RETRY |
| ✅ | 成功 | module passed |
| ✓ | 成功 | 汇总表 |
| ⏳ | 等待 | rate limit |
| ↩️ | 跳转 | on_failure jump |
| 🧹 | 清理 | worktree 删除重建 |
| 📁 | 文件 | worktree 保留 |
| 🌙 | 品牌 | 启动横幅 |
| 💡 | 提示 | 排查命令 |

**建议**：缩减到 4 个核心 emoji：START→⬇, PASS→✅, FAIL→✗, RETRY→↻。去掉装饰性 emoji（🌙🧹📁）。

### 5. 重试事件无视觉层次
```
[app] flaky START      ← 首次
❌ Shell failed          ← 失败
[app] flaky ⚠️ RETRY    ← 重试
[app] flaky START      ← 与首次完全相同的行，无区分
❌ Shell failed          ← 又失败
[app] flaky ⚠️ RETRY    
[app] flaky START      
[app] flaky PASS        ← 突然 PASS，无视觉强调
```
3 次重试之间没有 `(1/3)` 进度标记。PASS 行与前面 FAIL 行格式完全相同——没有视觉信号告诉用户"这次不一样了"。
**建议**：
```
  flaky  ✗ attempt 1/3 (exit 1)
  flaky  ✗ attempt 2/3 (exit 1)
  flaky  ✓ attempt 3/3
```
PASS 行用绿色或 ✅ 开头做视觉区分。

### 6. 汇总区重复 + worktree 噪音
```
  ✅ app      passed  (3 steps)          ← 汇总1
  📁 Worktree ...                        ← 噪音1
     Branch: cc-auto/app                 ← 噪音2
     Manual merge: ...                   ← 噪音3
=====================================
  ✓ app                   passed        ← 汇总2（与汇总1完全相同）
=====================================
  1 passed, 0 failed                     ← 汇总3
```
三处汇总信息高度重复。worktree 信息占 3 行插入在步骤结束和最终汇总之间。

---

## 重构建议：理想输出

```
cc-pipeline 0.3.2 · app · 17-07-12-06
─────────────────────────────────────────
  setup      ✓
  flaky      ✗ (exit 1)  attempt 1/3
  flaky      ✗ (exit 1)  attempt 2/3
  flaky      ✓            attempt 3/3
  verify     ✓
─────────────────────────────────────────
  app ✓ (3 steps, 3 files)  ·  worktree at runs/worktrees/app
```
12 行 → 8 行。信息密度更高，视觉层次清晰，无冗余。
