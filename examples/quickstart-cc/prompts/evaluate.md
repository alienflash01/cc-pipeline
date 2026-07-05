# Evaluate step

Assess the test quality you produced for the `{module}` module.

- Re-run `/usr/bin/python3 -m pytest tests/test_{module}.py -q`.
- Check coverage and that meaningful edge cases exist (not just happy paths).
- If quality is too low, leave a short summary of what is missing so the
  pipeline can regenerate.
