"""Pipeline Compiler — convert YAML pipeline + module into executable CompiledSteps."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.render import render

VALID_EXECUTORS = {"claude-code", "shell", "judge"}


@dataclass
class CompiledStep:
    """A single executable step, with variables resolved."""
    step_id: str
    executor: str
    rendered_prompt: str
    postcondition: dict | None = None
    retry: int = 3
    rollback: str = "git-checkpoint"
    output: str | None = None
    depends_on: str | None = None
    loop_file: str | None = None  # set when this is a loop expansion
    model: str = ""  # per-step model (empty = use global)
    timeout: int | None = None  # per-step timeout override
    on_failure: str | None = None  # jump-back target on failure (step_id)
    on_failure_max_jumps: int = 2  # max jump-back count


class PipelineCompiler:
    """Compiles a PipelineConfig + module name into a list of CompiledSteps."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def compile_module(self, module_name: str) -> list[CompiledStep]:
        """Compile the pipeline for a specific module.

        Args:
            module_name: Name of the module to compile for.

        Returns:
            Ordered list of CompiledSteps.

        Raises:
            ValueError: If module not found, duplicate step IDs, or invalid executor.
        """
        # Find module
        module = None
        for m in self.config.modules:
            if m.name == module_name:
                module = m
                break
        if module is None:
            raise ValueError(f"Module not found: {module_name}")

        # Validate step IDs are unique
        seen_ids = set()
        for step in self.config.pipeline:
            if step.id in seen_ids:
                raise ValueError(f"Duplicate step ID: {step.id}")
            seen_ids.add(step.id)

        # Validate executors
        for step in self.config.pipeline:
            if step.executor not in VALID_EXECUTORS:
                raise ValueError(
                    f"Invalid executor '{step.executor}' in step '{step.id}'. "
                    f"Must be one of: {VALID_EXECUTORS}"
                )

        # Build base variables for this module
        base_vars = {
            "module": module.name,
            "source_dir": module.source_dir,
            "spec_id": module.spec_id,
            **module.variables,
        }

        # Compile steps
        compiled: list[CompiledStep] = []
        for step in self.config.pipeline:
            retry = step.retry if step.retry is not None else self.config.max_retries

            if step.loop == "per_file":
                if not module.source_files:
                    raise ValueError(
                        f"Step '{step.id}' uses loop: per_file but module "
                        f"'{module.name}' has empty source_files"
                    )
                for entry in module.source_files:
                    # Support both string and dict entries
                    if isinstance(entry, str):
                        vars_with_file = {**base_vars, "file": entry}
                        loop_file = entry
                    elif isinstance(entry, dict):
                        if "path" not in entry:
                            raise ValueError(
                                f"Step '{step.id}' module '{module.name}': "
                                f"source_files dict entry missing 'path' key: {entry}"
                            )
                        # Expand all dict keys as variables, path → file
                        entry_vars = {k: v for k, v in entry.items() if k != "path"}
                        vars_with_file = {**base_vars, **entry_vars, "file": entry["path"]}
                        loop_file = entry["path"]
                    else:
                        raise ValueError(
                            f"Step '{step.id}' module '{module.name}': "
                            f"source_files entry must be string or dict, got {type(entry)}"
                        )
                    compiled.append(CompiledStep(
                        step_id=step.id,
                        executor=step.executor,
                        rendered_prompt=render(self._resolve_prompt(step), vars_with_file),
                        postcondition=self._render_postcondition(step, vars_with_file),
                        retry=retry,
                        rollback=step.rollback,
                        output=step.output,
                        depends_on=step.depends_on,
                        loop_file=loop_file,
                        model=step.model,
                        timeout=step.timeout,
                        on_failure=step.on_failure,
                        on_failure_max_jumps=step.on_failure_max_jumps,
                    ))
            else:
                compiled.append(CompiledStep(
                    step_id=step.id,
                    executor=step.executor,
                    rendered_prompt=render(self._resolve_prompt(step), base_vars),
                    postcondition=self._render_postcondition(step, base_vars),
                    retry=retry,
                    rollback=step.rollback,
                    output=step.output,
                    depends_on=step.depends_on,
                    model=step.model,
                    timeout=step.timeout,
                    on_failure=step.on_failure,
                    on_failure_max_jumps=getattr(step, "on_failure_max_jumps", 2),
                ))

        # Sort by depends_on (topological-ish)
        compiled = self._sort_by_dependencies(compiled)

        return compiled

    def _resolve_prompt(self, step: PipelineStep) -> str:
        """Return the effective prompt/command for a step.

        Rules:
          1. Shell executor: use step.command if set, then step.prompt, then prompt_file.
          2. CC/judge executor: use step.prompt if set, then prompt_file.
          3. prompt_file is loaded from disk if both prompt and command are empty.
        """
        if step.executor == "shell":
            if step.command:
                return step.command
            if step.prompt:
                return step.prompt

        # CC / judge
        if step.prompt:
            return step.prompt

        if step.prompt_file:
            path = Path(step.prompt_file)
            if not path.exists():
                raise FileNotFoundError(f"prompt_file not found: {step.prompt_file}")
            return path.read_text()
        return ""

    def _render_postcondition(self, step: PipelineStep, variables: dict) -> dict | None:
        """Render variables inside postcondition shell command."""
        if step.postcondition is None:
            return None
        result = dict(step.postcondition)
        if "shell" in result:
            result["shell"] = render(result["shell"], variables)
        if "expect" in result:
            result["expect"] = render(result["expect"], variables)
        return result

    def _sort_by_dependencies(self, steps: list[CompiledStep]) -> list[CompiledStep]:
        """Reorder steps so that depends_on targets come first.

        Raises ValueError on circular dependencies or dangling depends_on.
        """
        all_ids = {s.step_id for s in steps}

        # Check for dangling depends_on
        for step in steps:
            if step.depends_on and step.depends_on not in all_ids:
                raise ValueError(
                    f"Step '{step.step_id}' depends_on '{step.depends_on}' "
                    f"which does not exist"
                )

        result: list[CompiledStep] = []
        remaining = list(steps)
        placed_ids: set[str] = set()

        while remaining:
            progressed = False
            for i, step in enumerate(remaining):
                if step.depends_on is None or step.depends_on in placed_ids:
                    result.append(step)
                    placed_ids.add(step.step_id)
                    remaining.pop(i)
                    progressed = True
                    break
            if not progressed:
                remaining_names = [s.step_id for s in remaining]
                raise ValueError(
                    f"Circular dependency detected among steps: {remaining_names}"
                )

        return result
