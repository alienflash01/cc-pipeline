"""cc-pipeline constants — centralized runtime configuration values."""

# Rate limit handling (runner.py)
MAX_FREE_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_SECS = 30

# on_failure jump limits (runner.py)
DEFAULT_MAX_ON_FAILURE_JUMPS = 2

# Context injection limits (runner.py)
PROGRESS_MD_MAX_LINES = 20
CONTEXT_MAX_FILES = 3
CONTEXT_MAX_SIZE_BYTES = 10240  # 10KB

# Logger truncation (logger.py)
LOG_STDOUT_MAX_CHARS = 20000
LOG_STDERR_MAX_CHARS = 10000

# Executor defaults (executor.py)
CC_DEFAULT_TIMEOUT = 600
SHELL_DEFAULT_TIMEOUT = 300
POSTCONDITION_DEFAULT_TIMEOUT = 300

# Config validation limits (config.py)
MAX_CONCURRENCY = 100
MAX_RETRIES = 20
