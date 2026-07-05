#!/bin/bash
# One-shot runner for the shell-only quickstart (no CC, no API key).
# Expected to be run from the repo root:  bash examples/quickstart-shell/run.sh
set -e
cd examples/quickstart-shell
git init && git add -A && git commit -m init
pip install -e ../..
cc-pipeline run config.yaml -v
