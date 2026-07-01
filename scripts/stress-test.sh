#!/bin/bash
# cc-pipeline 压力测试：构造真实场景，覆盖并行+串行+retry+上下文传递
# 使用 FakeCCExecutor（不需要真实 API），通过 cc-pipeline CLI 运行
#
# 用法：
#   cd cc-pipeline && bash scripts/stress-test.sh
set -euo pipefail

WORKDIR=$(mktemp -d)
echo "📁 Workdir: $WORKDIR"

# ─── 1. 构造 5 模块 C 项目 ───
PROJECT="$WORKDIR/project"
mkdir -p "$PROJECT/src"

for mod in auth payment crypto network parser; do
    mkdir -p "$PROJECT/src/$mod"
    for i in 1 2 3; do
        cat > "$PROJECT/src/$mod/${mod}_$i.c" << EOF
int ${mod}_func_$i(int x) { return x * $i; }
int ${mod}_check_$i(int x) { return x > 0 ? 1 : 0; }
EOF
    done
done

cd "$PROJECT"
git init -q
git config user.name "stress-test"
git config user.email "test@test.com"
git branch -M main
git add -A
git commit -qm "init: 5 modules × 3 files each"

echo "✅ Created 5-module C project"

# ─── 2. 生成配置文件 ───
cat > "$WORKDIR/modules.yaml" << 'YAML'
repo: PROJECT_PLACEHOLDER
base_branch: main
concurrency: 3
max_retries: 2
output_branch_prefix: ut-stress

pipeline:
  - id: scaffold
    executor: shell
    prompt: "mkdir -p tests/{module}"
    postcondition:
      shell: "test -d tests/{module}"

  - id: touch
    executor: shell
    prompt: "echo 'test placeholder' > tests/{module}/test_stub.c"
    postcondition:
      shell: "test -f tests/{module}/test_stub.c"
    depends_on: scaffold

  - id: verify
    executor: shell
    prompt: "echo 'verified' > .pipeline/verify.txt"
    postcondition:
      shell: "test -f .pipeline/verify.txt"
    depends_on: touch

modules:
  - {name: auth, spec_id: S1, source_dir: src/auth/, source_files: [auth_1.c, auth_2.c, auth_3.c], coverage: {line_threshold: 80, branch_threshold: 70}}
  - {name: payment, spec_id: S2, source_dir: src/payment/, source_files: [payment_1.c, payment_2.c, payment_3.c], coverage: {line_threshold: 80, branch_threshold: 70}}
  - {name: crypto, spec_id: S3, source_dir: src/crypto/, source_files: [crypto_1.c, crypto_2.c, crypto_3.c], coverage: {line_threshold: 80, branch_threshold: 70}}
  - {name: network, spec_id: S4, source_dir: src/network/, source_files: [network_1.c, network_2.c, network_3.c], coverage: {line_threshold: 80, branch_threshold: 70}}
  - {name: parser, spec_id: S5, source_dir: src/parser/, source_files: [parser_1.c, parser_2.c, parser_3.c], coverage: {line_threshold: 80, branch_threshold: 70}}
YAML

sed -i "s|PROJECT_PLACEHOLDER|$PROJECT|g" "$WORKDIR/modules.yaml"
echo "✅ Generated config: 5 modules × 3 steps = 15 shell steps (3-way parallel)"

# ─── 3. 运行 ───
echo ""
echo "🚀 Running cc-pipeline..."
START=$(date +%s%N)

cd /mnt/e/02.workspace/cc-pipeline
/usr/bin/python3 -c "
import sys
sys.path.insert(0, 'src')
from cc_pipeline.cli import main
ret = main(['run', '$WORKDIR/modules.yaml', '--run-dir', '$WORKDIR/runs', '--concurrency', '3', '--model', 'glm-4.6'])
sys.exit(ret)
" 2>&1

END=$(date +%s%N)
ELAPSED=$(( (END - START) / 1000000 ))

# ─── 4. 验证结果 ───
echo ""
echo "════════════════════════════════════════"
echo "📊 压力测试结果"
echo "════════════════════════════════════════"

# Check state
STATE="$WORKDIR/runs/orchestrator-state.json"
if [ -f "$STATE" ]; then
    PASSED=$(/usr/bin/python3 -c "import json; d=json.load(open('$STATE')); print(sum(1 for m in d['modules'].values() if m['status']=='passed'))")
    FAILED=$(/usr/bin/python3 -c "import json; d=json.load(open('$STATE')); print(sum(1 for m in d['modules'].values() if m['status']!='passed'))")
    echo "  Modules: $((PASSED+FAILED)) (passed=$PASSED, failed=$FAILED)"
else
    echo "  ⚠️ No state file found"
fi

echo "  Time: ${ELAPSED}ms"

# Check git tags (preserved in main repo after worktree cleanup)
cd "$PROJECT"
TAG_COUNT=$(git tag -l "pipeline/*" 2>/dev/null | wc -l)
echo "  Git tags created: $TAG_COUNT (expected: 15 = 5 modules × 3 steps)"
# Check git branches (cleanup deletes branch on success — 0 is correct)
cd "$PROJECT"
BRANCH_COUNT=$(git branch --list "ut-stress/*" 2>/dev/null | wc -l)
echo "  Git branches remaining: $BRANCH_COUNT (expected: 0, cleaned on success)"

echo ""
if [ "$PASSED" = "5" ] && [ "$TAG_COUNT" = "15" ]; then
    echo "✅ ALL CHECKS PASSED"
else
    echo "❌ SOME CHECKS FAILED — check $WORKDIR/runs for details"
fi

echo ""
echo "📁 Artifacts at: $WORKDIR"
echo "   State:    $STATE"
echo "   Runs:     $WORKDIR/runs/"
echo "   Project:  $PROJECT"
