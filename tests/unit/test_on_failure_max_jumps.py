"""TDD: on_failure_max_jumps customizable."""
import pytest
from pathlib import Path
from cc_pipeline.compiler import CompiledStep
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.executor import CCResult


def _make_runner(tmp_path, steps, cc_results):
    import subprocess, os
    wt = tmp_path / "wt"; wt.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=wt, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=wt, capture_output=True)
    (wt / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com"}
    subprocess.run(["git", "commit", "-m", "init"], cwd=wt, capture_output=True, env=env)

    idx = [0]
    class FakeCC:
        def __init__(self, **kw): pass
        def run(self, prompt, cwd, **kw):
            i = idx[0]; idx[0] += 1
            r = cc_results[i] if i < len(cc_results) else CCResult(0, "ok", "")
            return r

    return ModuleRunner(
        steps=steps, module_name="auth",
        worktree_path=str(wt), run_dir=str(tmp_path / "runs"),
        cc_executor=FakeCC(),
    )


class TestCustomMaxJumps:
    def test_default_max_jumps_is_2(self):
        """CompiledStep without on_failure_max_jumps defaults to 2."""
        step = CompiledStep(
            step_id="eval", executor="claude-code",
            rendered_prompt="t", postcondition=None, retry=0,
            on_failure="gen",
        )
        assert step.on_failure_max_jumps == 2

    def test_custom_max_jumps(self):
        """on_failure_max_jumps can be set to 3."""
        step = CompiledStep(
            step_id="eval", executor="claude-code",
            rendered_prompt="t", postcondition=None, retry=0,
            on_failure="gen", on_failure_max_jumps=3,
        )
        assert step.on_failure_max_jumps == 3

    def test_3_jumps_allows_3_retries_before_fail(self, tmp_path):
        """on_failure_max_jumps=3 allows 3 jumps before giving up."""
        steps = [
            CompiledStep(step_id="gen", executor="claude-code",
                        rendered_prompt="g", postcondition=None, retry=0),
            CompiledStep(step_id="eval", executor="claude-code",
                        rendered_prompt="e", postcondition=None, retry=0,
                        on_failure="gen", on_failure_max_jumps=3),
        ]
        # 3 jumps × (gen + eval) = 6 pairs + initial = need:
        # gen, eval(fail), gen, eval(fail), gen, eval(fail), gen, eval(fail)
        results = []
        for _ in range(4):
            results.append(CCResult(0, "gen", ""))    # gen always passes
            results.append(CCResult(1, "", "fail"))   # eval always fails
        runner = _make_runner(tmp_path, steps, results)
        result = runner.run()
        # 3 jumps allowed → 4 total eval attempts → still failed
        assert result["status"] == "failed"

    def test_1_jump_fails_fast(self, tmp_path):
        """on_failure_max_jumps=1 allows only 1 jump."""
        steps = [
            CompiledStep(step_id="gen", executor="claude-code",
                        rendered_prompt="g", postcondition=None, retry=0),
            CompiledStep(step_id="eval", executor="claude-code",
                        rendered_prompt="e", postcondition=None, retry=0,
                        on_failure="gen", on_failure_max_jumps=1),
        ]
        results = [
            CCResult(0, "gen", ""),     # gen pass
            CCResult(1, "", "fail"),     # eval fail → jump 1
            CCResult(0, "gen", ""),     # gen pass
            CCResult(1, "", "fail"),     # eval fail → no more jumps → fail
        ]
        runner = _make_runner(tmp_path, steps, results)
        result = runner.run()
        assert result["status"] == "failed"
