#!/bin/bash
# One-shot runner for the Claude Code quickstart.
# Requires: `claude` CLI on PATH and ANTHROPIC_API_KEY set.
# Expected to be run from the repo root:  bash examples/quickstart-cc/run.sh
set -e
cd examples/quickstart-cc
git init && git add -A && git commit -m init
pip install -e ../..
cc-pipeline run config.yaml -v
