"""Pipeline Compiler — convert YAML pipeline + module into executable CompiledSteps."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.render import render

from cc_pipeline.config import _VALID_EXECUTORS as VALID_EXECUTORS


@dataclass
class CompiledStep:
    """A single executable step, with variables resolved."""
    step_id: str
    executor: str
    rendered_prompt: str
    postcondition: dict | None = None
    retry: int = 3
    output: str | None = None
    depends_on: str | None = None
    loop_file: str | None = None  # set when this is a loop expansion
    model: str = ""  # per-step model (empty = use global)
    timeout: int | None = None  # per-step timeout override
    on_failure: str | None = None  # jump-back target on failure (step_id)
    on_failure_max_jumps: int = 2  # max jump-back count
    output_prompt: str | None = None  # custom output injection text
    prev_output_path: str = ""  # .pipeline/xxx.json of previous step


class PipelineCompiler:
    """Compiles a PipelineConfig + module name into a list of CompiledSteps."""

    def __init__(self, config: PipelineConfig, config_dir: str | None = None):
        self.config = config
        self.config_dir = Path(config_dir) if config_dir else None

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
        sf_list = []
        if module.source_files:
            for sf in module.source_files:
                if isinstance(sf, dict):
                    sf_list.append(sf.get("path", str(sf)))
                else:
                    sf_list.append(str(sf))
        base_vars = {
            "module": module.name,
            "source_dir": module.source_dir,
            "spec_id": module.spec_id,
            "source_files": ", ".join(sf_list),
            **module.variables,
        }

        # Validate modules references exist (fail-fast, once per compile)
        all_module_names = {m.name for m in self.config.modules}
        for step in self.config.pipeline:
            if step.modules is not None:
                for m in step.modules:
                    if m not in all_module_names:
                        raise ValueError(
                            f"Step '{step.id}': modules references unknown module '{m}'"
                        )

        # Compile steps (filter by modules if configured)
        compiled: list[CompiledStep] = []
        for step in self.config.pipeline:
            # Skip steps not meant for this module
            if step.modules is not None and module_name not in step.modules:
                continue

            retry = step.retry if step.retry is not None else self.config.max_retries

            # Warn: prompt uses {file} but step has no loop
            if step.loop != "per_file":
                prompt_text = self._resolve_prompt(step)
                if "{file}" in prompt_text:
                    import warnings as _w
                    _w.warn(
                        f"Step '{step.id}': prompt uses {{file}} but step has no "
                        f"loop: per_file — {{file}} will not be replaced",
                        stacklevel=2,
                    )

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
                    # Expand {file} in output filename for per_file isolation
                    rendered_output = step.output or ""
                    if rendered_output and "{file}" in rendered_output:
                        rendered_output = rendered_output.replace("{file}", loop_file)
                    step_vars = {**vars_with_file, "output": rendered_output}
                    compiled.append(CompiledStep(
                        step_id=step.id,
                        executor=step.executor,
                        rendered_prompt=render(self._resolve_prompt(step), step_vars),
                        postcondition=self._render_postcondition(step, vars_with_file),
                        retry=retry,
                        output=rendered_output,
                        depends_on=step.depends_on,
                        loop_file=loop_file,
                        model=step.model,
                        timeout=step.timeout,
                        on_failure=step.on_failure,
                        on_failure_max_jumps=step.on_failure_max_jumps,
                        output_prompt=step.output_prompt,
                    ))
            else:
                step_vars = {**base_vars, "output": step.output or ""}
                compiled.append(CompiledStep(
                    step_id=step.id,
                    executor=step.executor,
                    rendered_prompt=render(self._resolve_prompt(step), step_vars),
                    postcondition=self._render_postcondition(step, base_vars),
                    retry=retry,
                    output=step.output,
                    depends_on=step.depends_on,
                    model=step.model,
                    timeout=step.timeout,
                    on_failure=step.on_failure,
                    on_failure_max_jumps=getattr(step, "on_failure_max_jumps", 2),
                    output_prompt=getattr(step, "output_prompt", None),
                ))

        # Sort by depends_on (topological-ish)
        compiled = self._sort_by_dependencies(compiled)

        # Reorder per_file expansions so each file walks the full flow before the
        # next file starts (sequential), instead of all-files-then-next-step (batched).
        if module.file_order == "sequential":
            compiled = self._reorder_sequential(compiled)

        # Set prev_output_path for each compiled step
        prev_output = ""
        for cs in compiled:
            cs.prev_output_path = f".pipeline/{prev_output}" if prev_output else ""
            prev_output = cs.output or prev_output

        return compiled

    def _resolve_prompt(self, step: PipelineStep) -> str:
        """Return the effective prompt for a step.

        Prepends config.prompt_prefix if set.
        """
        if step.prompt:
            text = step.prompt
        elif step.prompt_file:
            path = Path(step.prompt_file)
            if not path.is_absolute():
                if not path.exists() and self.config_dir:
                    path = self.config_dir / step.prompt_file
            if not path.exists():
                raise FileNotFoundError(f"prompt_file not found: {step.prompt_file}")
            text = path.read_text()
        else:
            text = ""

        # Prepend shared prompt_prefix (CC/judge only, NOT shell)
        prefix = getattr(self.config, "prompt_prefix", "")
        if prefix and step.executor != "shell":
            text = prefix.rstrip() + "\n\n" + text

        # Expand {{snippet:name}} references
        snippets = getattr(self.config, "snippets", {})
        if snippets:
            import re as _re
            import warnings as _w
            def _replace_snippet(m):
                name = m.group(1)
                if name in snippets:
                    return snippets[name]
                _w.warn(f"Snippet '{name}' not defined — kept as-is", stacklevel=2)
                return m.group(0)
            text = _re.sub(r"\{\{snippet:(\w+)\}\}", _replace_snippet, text)

        return text

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

    def _reorder_sequential(self, steps: list[CompiledStep]) -> list[CompiledStep]:
        """Reorder so each file walks every per_file step before the next file starts.

        Operates on a batched expansion (already dependency-sorted). Consecutive
        per_file steps form a group; within each group the order changes from
        "all files for stepA, then all files for stepB" to "all steps for file a,
        then all steps for file b, ...". Non-loop steps (loop_file is None) keep
        their position and break groups.

          batched:     [scaffold, gen[a], gen[b], gen[c], eval[a], eval[b], eval[c], report]
          sequential:  [scaffold, gen[a], eval[a], gen[b], eval[b], gen[c], eval[c], report]
        """
        result: list[CompiledStep] = []
        i = 0
        n = len(steps)
        while i < n:
            if steps[i].loop_file is None:
                # Non-loop step: keep in place.
                result.append(steps[i])
                i += 1
                continue
            # Collect the maximal run of consecutive per_file steps.
            j = i
            while j < n and steps[j].loop_file is not None:
                j += 1
            run = steps[i:j]
            # File order = order of first appearance within the run.
            file_order: list[str] = []
            seen: set[str] = set()
            for s in run:
                if s.loop_file not in seen:
                    seen.add(s.loop_file)
                    file_order.append(s.loop_file)
            # Bucket by file (preserves per-file step order), then emit per file.
            by_file: dict[str, list[CompiledStep]] = {}
            for s in run:
                by_file.setdefault(s.loop_file, []).append(s)
            for f in file_order:
                result.extend(by_file[f])
            i = j
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
