#!/usr/bin/env python3
"""Fake CC script — simulates Claude Code for data-flow testing.

Reads prompt from argv (after -p), writes .pipeline/{output}.json if instructed.
Returns 0 with meaningful stdout (not zero-work).
"""
import sys
import json
import re
import os
from pathlib import Path

# Parse args: look for -p <prompt> and --cwd <dir>
prompt = ""
cwd = "."
allowed_tools = None

args = sys.argv[1:]
i = 0
while i < len(args):
    if args[i] == "-p" and i + 1 < len(args):
        prompt = args[i + 1]
        i += 2
    elif args[i] == "--cwd" and i + 1 < len(args):
        cwd = args[i + 1]
        i += 2
    elif args[i] == "--model" and i + 1 < len(args):
        i += 2
    elif args[i] == "--allowedTools" and i + 1 < len(args):
        i += 2
    else:
        i += 1

# Detect output instruction: "写入 .pipeline/xxx.json"
output_match = re.search(r"\.pipeline/(\S+\.json)", prompt)
if output_match:
    output_file = output_match.group(1)
    pipeline_dir = Path(cwd) / ".pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    output_path = pipeline_dir / output_file

    # Generate meaningful content based on step
    content = {"status": "ok", "prompt_snippet": prompt[:100]}
    if "scaffold" in output_file:
        content["files_created"] = ["test_main.c"]
        content["test_framework"] = "dtest"
    elif "generate" in output_file:
        content["tests_generated"] = 5
        content["coverage"] = {"line": 85, "branch": 72}
    elif "evaluate" in output_file:
        content["score"] = 75
        content["assertion_density"] = 2.5

    output_path.write_text(json.dumps(content, indent=2))

# Print meaningful stdout (avoid zero-work detection)
print(f"Fake CC executed. Prompt length: {len(prompt)}.")
sys.exit(0)
