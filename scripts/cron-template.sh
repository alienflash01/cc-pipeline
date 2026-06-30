#!/bin/bash
# cc-pipeline cron template
# Edit and add to crontab: crontab -e
#
# Run UT generation every night at 23:00
# 0 23 * * * /path/to/cc-pipeline-cron.sh

set -euo pipefail

# ─── Configuration ───
CONFIG_FILE="/path/to/modules.yaml"
CONCURRENCY=5
MODEL="glm-4.6"
RUN_DIR_BASE="$HOME/.cc-pipeline/runs"

# ─── Run ID ───
RUN_ID=$(date -u +%Y-%m-%dT%H-%M-%S)
RUN_DIR="${RUN_DIR_BASE}/${RUN_ID}"
LOG_FILE="${RUN_DIR}/cron.log"

mkdir -p "$RUN_DIR"

echo "[$(date)] Starting cc-pipeline run $RUN_ID" | tee "$LOG_FILE"
echo "  config: $CONFIG_FILE" | tee -a "$LOG_FILE"
echo "  concurrency: $CONCURRENCY" | tee -a "$LOG_FILE"
echo "  model: $MODEL" | tee -a "$LOG_FILE"

# ─── Execute ───
cc-pipeline run "$CONFIG_FILE" \
  --concurrency "$CONCURRENCY" \
  --model "$MODEL" \
  --run-dir "$RUN_DIR" \
  2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=$?
echo "[$(date)] cc-pipeline exited with code $EXIT_CODE" | tee -a "$LOG_FILE"

# ─── Post-run: check for failures ───
FAILED=$(cc-pipeline status --run-id "$RUN_ID" 2>/dev/null | grep -c "failed" || echo "0")
if [ "$FAILED" -gt 0 ]; then
  echo "⚠️  $FAILED module(s) failed. Check $RUN_DIR for details."
fi

exit $EXIT_CODE
