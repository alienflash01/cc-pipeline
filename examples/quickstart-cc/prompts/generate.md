# Generate step (per_file)

Write pytest tests for `{file}` in module `{module}`.

- Open `{source_dir}{file}` and enumerate its public functions.
- For each function, add focused pytest cases (normal, edge, error) to
  `tests/test_{module}.py`.
- Run `/usr/bin/python3 -m pytest tests/test_{module}.py -q` and make it green.
- Do NOT create a virtual environment or run `pip install`.
