# cc-pipeline Agent Guidelines

## Testing Rules

When writing tests, follow TESTING-RULES.md strictly:
- Every `except` block must have a capsys test
- Every failure path must print to terminal + have a test
- New features need success + failure path tests (paired)
- capsys assertions must check terminal output, not just return code
- mock'd error paths need at least 1 E2E with real git/subprocess
