"""Config Loader — parse modules.yaml into typed config objects."""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass
class PipelineStep:
    """A single step in the pipeline."""
    id: str
    executor: str  # "claude-code" | "shell" | "judge"
    prompt: str = ""  # all executors (incl. shell) read from here
    prompt_file: str | None = None  # load prompt from external file
    model: str = ""  # per-step model override (empty = use global)
    loop: str | None = None  # "per_file" | None
    retry: int | None = None
    output: str | None = None
    depends_on: str | None = None
    postcondition: dict | None = None
    timeout: int | None = None
    on_failure: str | None = None  # jump-back target step_id on failure
    on_failure_max_jumps: int = 2  # max jump-back count
    output_prompt: str | None = None  # custom output injection text (default: framework's)


def _resolve_worktree_root(worktree_root: str, repo: str) -> str:
    """Resolve worktree_root: absolute path as-is, relative path resolved against repo dir.

    For worktree_root: ../wt with repo=/A/B → resolves to /A/wt (sibling of repo).
    """
    if not worktree_root:
        return ""
    from pathlib import Path
    p = Path(worktree_root)
    if p.is_absolute():
        return str(p)
    # Relative to repo directory itself
    return str((Path(repo) / p).resolve())


@dataclass
class Module:
    """A task module to process. Generic — no UT-specific fields."""
    name: str
    spec_id: str = ""
    source_dir: str = ""
    source_files: list = field(default_factory=list)  # list[str | dict]
    variables: dict = field(default_factory=dict)
    file_order: str = "batched"  # 'batched' | 'sequential' — per_file expansion order


@dataclass
class PipelineConfig:
    """Top-level pipeline configuration."""
    repo: str
    base_branch: str = "main"
    concurrency: int = 5
    max_retries: int = 3
    output_branch_prefix: str = "cc-auto"
    model: str = ""  # global default model (empty = CC decides)
    worktree_root: str = ""  # where to create worktrees (empty = framework decides)
    prompt_prefix: str = ""  # shared context prepended to every step's prompt
    snippets: dict = field(default_factory=dict)  # named text blocks, referenced via {{snippet:name}}
    commit_message: str = ""  # squash merge commit message template (default: auto-generated)
    auto_resolve_conflicts: bool = False  # use CC to auto-resolve merge conflicts
    auto_merge: bool = False  # auto squash-merge to base_branch after success (false = leave in worktree)
    pipeline: list[PipelineStep] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)


# Step fields recognized in YAML. Anything else triggers an "unknown field"
# warning and is silently ignored. Kept module-level so it is testable.
_KNOWN_STEP_FIELDS = {
    "id", "executor", "prompt", "prompt_file", "model",
    "loop", "retry", "output", "depends_on",
    "postcondition", "timeout",
    "on_failure", "on_failure_max_jumps", "output_prompt",
}


def load_config(path: str) -> PipelineConfig:
    """Load and validate a YAML config file.
    
    Args:
        path: Path to the YAML config file.
        
    Returns:
        PipelineConfig instance.
        
    Raises:
        ValueError: If required fields are missing.
        FileNotFoundError: If file doesn't exist.
    """
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(
            f"YAML syntax error: {e}\n"
            f"提示：检查缩进是否一致（用空格而非 Tab），"
            f"key 后面必须有冒号+空格。"
        ) from e

    if raw is None:
        raise ValueError("Config file is empty")
    
    # Required: repo
    if "repo" not in raw or not raw["repo"]:
        raise ValueError("Missing required field: repo")

    # Auto-detect base_branch if not specified
    if not raw.get("base_branch"):
        import subprocess as _sp
        try:
            _r = _sp.run(
                ["git", "-C", raw["repo"], "symbolic-ref", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            raw["base_branch"] = _r.stdout.strip() if _r.returncode == 0 else "main"
        except Exception:
            raw["base_branch"] = "main"
    
    # Required: modules (non-empty)
    if "modules" not in raw or not raw["modules"]:
        raise ValueError("Missing required field: modules (or empty list)")
    
    # Required: pipeline (non-empty)
    if "pipeline" not in raw or not raw["pipeline"]:
        raise ValueError("Missing required field: pipeline (or empty list)")
    
    # Parse pipeline steps
    pipeline = []
    for step_raw in raw["pipeline"]:
        # Warn on unknown fields
        import warnings as _w
        for key in step_raw:
            if key not in _KNOWN_STEP_FIELDS:
                _w.warn(f"Unknown field '{key}' in step '{step_raw.get('id','?')}' — ignored", stacklevel=2)
        if "id" not in step_raw or not step_raw["id"]:
            raise ValueError(f"Pipeline step missing required field: id (step data: {step_raw})")
        step = PipelineStep(
            id=step_raw["id"],
            executor=step_raw.get("executor", "claude-code"),
            prompt=step_raw.get("prompt", ""),
            loop=step_raw.get("loop"),
            retry=step_raw.get("retry"),
            output=step_raw.get("output"),
            depends_on=step_raw.get("depends_on"),
            postcondition=step_raw.get("postcondition"),
            model=step_raw.get("model", ""),
            prompt_file=step_raw.get("prompt_file"),
            timeout=step_raw.get("timeout"),
            on_failure=step_raw.get("on_failure"),
            on_failure_max_jumps=step_raw.get("on_failure_max_jumps", 2),
            output_prompt=step_raw.get("output_prompt"),
        )
        # Warn when both prompt and prompt_file are set (prompt wins)
        if step.prompt and step.prompt_file:
            import warnings as _pp_w
            _pp_w.warn(
                f"Step {step.id}: both prompt and prompt_file set — "
                f"prompt takes priority, prompt_file ignored",
                stacklevel=2,
            )

        # Validate executor is a string (before the value check below)
        if not isinstance(step_raw.get("executor", "claude-code"), str):
            raise ValueError(
                f"Step '{step_raw.get('id', '?')}': executor must be a string, "
                f"got {type(step_raw.get('executor')).__name__}"
            )

        # Validate depends_on does not reference itself
        if step.depends_on == step.id:
            raise ValueError(f"Step '{step.id}': depends_on cannot reference itself")

        # Validate postcondition type and required fields
        if step.postcondition is not None and not isinstance(step.postcondition, dict):
            raise ValueError(
                f"Step '{step.id}': postcondition must be a dict with 'shell' and "
                f"'expect' keys, got {type(step.postcondition).__name__}"
            )
        if step.postcondition is not None and isinstance(step.postcondition, dict):
            if "shell" not in step.postcondition:
                raise ValueError(
                    f"Step '{step.id}': postcondition missing required 'shell' field"
                )

        pipeline.append(step)
    
    # Parse modules
    modules = []
    for mod_raw in raw["modules"]:
        # Migration: fold deprecated 'coverage' into 'variables'
        variables = mod_raw.get("variables", {})
        if "coverage" in mod_raw:
            import warnings as _cov_w
            _cov_w.warn(
                f"Module '{mod_raw.get('name', '?')}': 'coverage' is deprecated, "
                f"fold its contents into 'variables' instead",
                stacklevel=2,
            )
            variables = {**mod_raw["coverage"], **variables}
        mod = Module(
            name=mod_raw["name"],
            spec_id=mod_raw.get("spec_id", ""),
            source_dir=mod_raw.get("source_dir", ""),
            source_files=mod_raw.get("source_files", []),
            variables=variables,
            file_order=mod_raw.get("file_order", "batched"),
        )
        modules.append(mod)
    
    # Validate module names (security: prevent command injection)
    import re as _re
    _SAFE_NAME = _re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_\-]*$")
    seen_names = set()
    for mod in modules:
        if not _SAFE_NAME.match(mod.name):
            raise ValueError(
                f"Invalid module name '{mod.name}': only alphanumeric, underscore, "
                f"and hyphen allowed (no shell metacharacters or slashes)"
            )
        if mod.name in seen_names:
            raise ValueError(f"Duplicate module name '{mod.name}'")
        seen_names.add(mod.name)

    # Validate output fields (security: prevent path traversal)
    for step in pipeline:
        if step.output and (".." in step.output or "/" in step.output):
            raise ValueError(
                f"Invalid output '{step.output}': no path traversal or slashes allowed"
            )

    # Validate numeric fields
    concurrency = raw.get("concurrency", 5)
    max_retries = raw.get("max_retries", 3)
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError(f"concurrency must be a positive integer, got: {concurrency}")
    if concurrency > 100:
        raise ValueError(f"concurrency must be <= 100, got: {concurrency}")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError(f"max_retries must be a non-negative integer, got: {max_retries}")
    if max_retries > 20:
        raise ValueError(f"max_retries must be <= 20, got: {max_retries}")

    # Validate source_files (security: prevent path traversal)
    for mod in modules:
        if not isinstance(mod.source_files, list):
            raise ValueError(
                f"Module '{mod.name}': source_files must be a list, got {type(mod.source_files).__name__}. "
                f"Use:\n  source_files:\n    - {mod.source_files}\n  # NOT: source_files: {mod.source_files}"
            )
        for sf in mod.source_files:
            sf_str = sf["path"] if isinstance(sf, dict) else sf
            if ".." in sf_str or "/" in sf_str or "\\" in sf_str:
                raise ValueError(
                    f"Invalid source_file '{sf}' in module '{mod.name}': "
                    f"no path traversal or slashes allowed"
                )

    # Validate module file_order
    for mod in modules:
        if mod.file_order not in ("batched", "sequential"):
            raise ValueError(
                f"Module '{mod.name}': file_order must be 'batched' or 'sequential', "
                f"got '{mod.file_order}'"
            )

    # Validate timeout fields
    for step in pipeline:
        if step.timeout is not None:
            if not isinstance(step.timeout, int) or step.timeout <= 0:
                raise ValueError(
                    f"Invalid timeout '{step.timeout}' in step '{step.id}': "
                    f"must be a positive integer"
                )

    # Validate model field (security: no newlines/spaces for injection)
    model_val = raw.get("model", "")
    if model_val and ("\n" in model_val or "\r" in model_val):
        raise ValueError(f"Invalid model '{model_val}': no newlines allowed")

    # Warn about empty source_dir
    for mod in modules:
        if mod.source_dir == "":
            import warnings as _sd_w
            _sd_w.warn(f"Module '{mod.name}' has empty source_dir", stacklevel=2)

    # Warn when executor field is missing (defaults to claude-code)
    import warnings as _warnings
    for step_raw in raw["pipeline"]:
        if "executor" not in step_raw:
            _warnings.warn(
                f"Step '{step_raw['id']}': executor field missing, defaulting to 'claude-code'",
                stacklevel=2,
            )

    # Validate executor types (fail early with helpful message)
    _VALID_EXECUTORS = {"claude-code", "shell", "judge"}
    for step in pipeline:
        if step.executor not in _VALID_EXECUTORS:
            # Suggest closest match
            import difflib
            suggestions = difflib.get_close_matches(step.executor, _VALID_EXECUTORS, n=1, cutoff=0.5)
            hint = f" (did you mean '{suggestions[0]}'?)" if suggestions else f" Must be one of: {_VALID_EXECUTORS}"
            raise ValueError(
                f"Step '{step.id}': invalid executor '{step.executor}'{hint}"
            )

    # Validate prompt_file paths exist (fail early, not at runtime)
    _all_step_ids = {s.id for s in pipeline}
    for step in pipeline:
        # Validate on_failure target exists
        if step.on_failure and step.on_failure not in _all_step_ids:
            raise ValueError(
                f"Step '{step.id}': on_failure '{step.on_failure}' "
                f"does not match any step id"
            )
        if step.prompt_file:
            from pathlib import Path as _P
            p = _P(step.prompt_file)
            if not p.exists():
                cfg_dir = _P(path).parent
                if not (cfg_dir / step.prompt_file).exists():
                    raise FileNotFoundError(
                        f"prompt_file not found: {step.prompt_file}"
                    )

    return PipelineConfig(
        repo=raw["repo"],
        base_branch=raw.get("base_branch", "main"),
        concurrency=raw.get("concurrency", 5),
        max_retries=raw.get("max_retries", 3),
        output_branch_prefix=raw.get("output_branch_prefix", "cc-auto"),
        model=raw.get("model", ""),
        worktree_root=_resolve_worktree_root(raw.get("worktree_root", ""), raw["repo"]),
        prompt_prefix=raw.get("prompt_prefix", ""),
        snippets=raw.get("snippets", {}),
        commit_message=raw.get("commit_message", ""),
        auto_resolve_conflicts=raw.get("auto_resolve_conflicts", False),
        auto_merge=raw.get("auto_merge", False),
        pipeline=pipeline,
        modules=modules,
    )
