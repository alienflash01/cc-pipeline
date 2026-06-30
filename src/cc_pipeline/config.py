"""Config Loader — parse modules.yaml into typed config objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PipelineStep:
    """A single step in the pipeline."""
    id: str
    executor: str  # "claude-code" | "shell" | "judge"
    prompt: str = ""
    loop: str | None = None  # "per_file" | None
    retry: int | None = None
    rollback: str = "git-checkpoint"
    output: str | None = None
    depends_on: str | None = None
    postcondition: dict | None = None
    on_complete: list | None = None
    skill: str | None = None


@dataclass
class Module:
    """A module to process."""
    name: str
    spec_id: str = ""
    source_dir: str = ""
    source_files: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)


@dataclass
class PipelineConfig:
    """Top-level pipeline configuration."""
    repo: str
    base_branch: str = "main"
    concurrency: int = 5
    max_retries: int = 3
    output_branch_prefix: str = "ut-auto"
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
    pipeline = []
    for step_raw in raw["pipeline"]:
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
        )
        pipeline.append(step)
    
    # Parse modules
    modules = []
    for mod_raw in raw["modules"]:
        mod = Module(
            name=mod_raw["name"],
            spec_id=mod_raw.get("spec_id", ""),
            source_dir=mod_raw.get("source_dir", ""),
            source_files=mod_raw.get("source_files", []),
            coverage=mod_raw.get("coverage", {}),
            variables=mod_raw.get("variables", {}),
        )
        modules.append(mod)
    
    return PipelineConfig(
        repo=raw["repo"],
        base_branch=raw.get("base_branch", "main"),
        concurrency=raw.get("concurrency", 5),
        max_retries=raw.get("max_retries", 3),
        output_branch_prefix=raw.get("output_branch_prefix", "ut-auto"),
        pipeline=pipeline,
        modules=modules,
    )
