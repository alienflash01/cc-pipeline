"""Config Loader — parse modules.yaml into typed config objects."""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass
class PipelineStep:
    """A single step in the pipeline."""
    id: str
    executor: str  # "claude-code" | "shell" | "judge"
    prompt: str = ""
    command: str = ""  # shell executor uses this instead of prompt
    prompt_file: str | None = None  # load prompt from external file
    model: str = ""  # per-step model override (empty = use global)
    loop: str | None = None  # "per_file" | None
    retry: int | None = None
    rollback: str = "git-checkpoint"
    output: str | None = None
    depends_on: str | None = None
    postcondition: dict | None = None
    on_complete: list | None = None
    skill: str | None = None
    timeout: int | None = None
    on_failure: str | None = None  # jump-back target step_id on failure


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


@dataclass
class PipelineConfig:
    """Top-level pipeline configuration."""
    repo: str
    base_branch: str = "main"
    concurrency: int = 5
    max_retries: int = 3
    output_branch_prefix: str = "ut-auto"
    model: str = ""  # global default model (empty = CC decides)
    worktree_root: str = ""  # where to create worktrees (empty = framework decides)
    pr_labels: list[str] = field(default_factory=list)
    pr_title_template: str = ""
    pipeline: list[PipelineStep] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)


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
    with open(path) as f:
        raw = yaml.safe_load(f)
    
    if raw is None:
        raise ValueError("Config file is empty")
    
    # Required: repo
    if "repo" not in raw or not raw["repo"]:
        raise ValueError("Missing required field: repo")
    
    # Required: modules (non-empty)
    if "modules" not in raw or not raw["modules"]:
        raise ValueError("Missing required field: modules (or empty list)")
    
    # Required: pipeline (non-empty)
    if "pipeline" not in raw or not raw["pipeline"]:
        raise ValueError("Missing required field: pipeline (or empty list)")
    
    # Parse pipeline steps
    _KNOWN_STEP_FIELDS = {"id", "executor", "prompt", "command", "prompt_file", "model",
                          "loop", "retry", "rollback", "output", "depends_on",
                          "postcondition", "on_complete", "skill", "timeout"}
    pipeline = []
    for step_raw in raw["pipeline"]:
        # Warn on unknown fields
        import warnings as _w
        for key in step_raw:
            if key not in _KNOWN_STEP_FIELDS:
                _w.warn(f"Unknown field '{key}' in step '{step_raw.get('id','?')}' — ignored", stacklevel=2)
        step = PipelineStep(
            id=step_raw["id"],
            executor=step_raw.get("executor", "claude-code"),
            prompt=step_raw.get("prompt", ""),
            loop=step_raw.get("loop"),
            retry=step_raw.get("retry"),
            rollback=step_raw.get("rollback", "git-checkpoint"),
            output=step_raw.get("output"),
            depends_on=step_raw.get("depends_on"),
            postcondition=step_raw.get("postcondition"),
            on_complete=step_raw.get("on_complete"),
            skill=step_raw.get("skill"),
            model=step_raw.get("model", ""),
            command=step_raw.get("command", ""),
            prompt_file=step_raw.get("prompt_file"),
            timeout=step_raw.get("timeout"),
            on_failure=step_raw.get("on_failure"),
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
        for sf in mod.source_files:
            if ".." in sf or "/" in sf or "\\" in sf:
                raise ValueError(
                    f"Invalid source_file '{sf}' in module '{mod.name}': "
                    f"no path traversal or slashes allowed"
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

    # Warn about unimplemented fields
    import warnings as _warnings
    for step in pipeline:
        if step.on_complete:
            _warnings.warn(f"Step '{step.id}': on_complete field is not yet implemented, ignored", stacklevel=2)
        if step.skill:
            _warnings.warn(f"Step '{step.id}': skill field is not yet implemented, ignored", stacklevel=2)

    # Warn when executor field is missing (defaults to claude-code)
    for step_raw in raw["pipeline"]:
        if "executor" not in step_raw:
            _warnings.warn(
                f"Step '{step_raw['id']}': executor field missing, defaulting to 'claude-code'",
                stacklevel=2,
            )

    return PipelineConfig(
        repo=raw["repo"],
        base_branch=raw.get("base_branch", "main"),
        concurrency=raw.get("concurrency", 5),
        max_retries=raw.get("max_retries", 3),
        output_branch_prefix=raw.get("output_branch_prefix", "ut-auto"),
        model=raw.get("model", ""),
        worktree_root=_resolve_worktree_root(raw.get("worktree_root", ""), raw["repo"]),
        pr_labels=raw.get("pr_labels", []),
        pr_title_template=raw.get("pr_title_template", ""),
        pipeline=pipeline,
        modules=modules,
    )
