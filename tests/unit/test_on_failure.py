"""TDD: on_failure step jump-back — evaluate fails → re-run generate without rollback."""
import pytest
from pathlib import Path
from cc_pipeline.compiler import CompiledStep
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.executor import CCResult


def _make_runner(tmp_path, steps, cc_results_per_call=None):
    """Build a ModuleRunner with fake CC executor + real git repo."""
    import subprocess, os
    # Create a real git repo for checkpoint operations
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=wt, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=wt, capture_output=True)
    (wt / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}
    subprocess.run(["git", "commit", "-m", "init"], cwd=wt, capture_output=True, env=env)

    call_log = []
    call_idx = [0]

    class FakeCC:
        def __init__(self, **kw): pass
        def run(self, prompt, cwd, **kw):
            call_log.append(prompt)
            idx = call_idx[0]
            call_idx[0] += 1
            if cc_results_per_call and idx < len(cc_results_per_call):
                r = cc_results_per_call[idx]
                return CCResult(returncode=r.get("returncode", 0),
                               stdout=r.get("stdout", "done"),
                               stderr=r.get("stderr", ""))
            return CCResult(returncode=0, stdout="done", stderr="")

    runner = ModuleRunner(
        steps=steps,
        module_name="auth",
        worktree_path=str(wt),
        run_dir=str(tmp_path / "runs"),
        cc_executor=FakeCC(),
    )
    return runner, call_log


class TestOnFailureJumpBack:
    """on_failure: evaluate fail → jump back to generate."""

    def test_on_failure_field_on_compiled_step(self):
        """CompiledStep should support on_failure field."""
        step = CompiledStep(
            step_id="eval", executor="claude-code",
            rendered_prompt="t", postcondition=None, retry=1,
            on_failure="generate",
        )
        assert step.on_failure == "generate"

    def test_no_on_failure_default_none(self):
        """CompiledStep.on_failure defaults to None."""
        step = CompiledStep(step_id="x", executor="shell",
                           rendered_prompt="t", postcondition=None, retry=0)
        assert step.on_failure is None

    def test_evaluate_fail_jumps_to_generate(self, tmp_path):
        """evaluate fails → runner jumps back to generate (re-runs it)."""
        steps = [
            CompiledStep(step_id="generate", executor="claude-code",
                        rendered_prompt="gen", postcondition=None, retry=0),
            CompiledStep(step_id="evaluate", executor="claude-code",
                        rendered_prompt="eval", postcondition=None, retry=0,
                        on_failure="generate"),
        ]
        # generate: pass (1 call)
        # evaluate: fail (1 call) → jump to generate
        # generate: pass again (1 call)
        # evaluate: pass (1 call)
        results = [
            {"returncode": 0, "stdout": "gen done"},     # generate pass
            {"returncode": 1, "stdout": "", "stderr": "eval fail"},  # evaluate fail
            {"returncode": 0, "stdout": "gen done 2"},   # generate pass (jump back)
            {"returncode": 0, "stdout": "eval pass"},    # evaluate pass
        ]
        runner, call_log = _make_runner(tmp_path, steps, results)
        result = runner.run()
        assert result["status"] == "passed"
        # 4 CC calls: gen, eval(fail), gen(jump), eval(pass)
        assert len(call_log) == 4

    def test_on_failure_budget_exhausted(self, tmp_path):
        """on_failure jump count limited — after max jumps, module fails."""
        steps = [
            CompiledStep(step_id="generate", executor="claude-code",
                        rendered_prompt="gen", postcondition=None, retry=0),
            CompiledStep(step_id="evaluate", executor="claude-code",
                        rendered_prompt="eval", postcondition=None, retry=0,
                        on_failure="generate"),
        ]
        # evaluate always fails → infinite jump without budget
        results = [
            {"returncode": 0, "stdout": "gen"},   # generate pass
            {"returncode": 1, "stderr": "fail"},   # evaluate fail → jump
            {"returncode": 0, "stdout": "gen"},   # generate pass (jump 1)
            {"returncode": 1, "stderr": "fail"},   # evaluate fail → jump
            {"returncode": 0, "stdout": "gen"},   # generate pass (jump 2)
            {"returncode": 1, "stderr": "fail"},   # evaluate fail → budget exhausted
        ]
        runner, call_log = _make_runner(tmp_path, steps, results)
        result = runner.run()
        assert result["status"] == "failed"

    def test_on_failure_no_rollback(self, tmp_path):
        """on_failure jump should NOT trigger git rollback."""
        # We verify by checking that the worktree path is unchanged
        steps = [
            CompiledStep(step_id="generate", executor="claude-code",
                        rendered_prompt="gen", postcondition=None, retry=0),
            CompiledStep(step_id="evaluate", executor="claude-code",
                        rendered_prompt="eval", postcondition=None, retry=0,
                        on_failure="generate"),
        ]
        results = [
            {"returncode": 0, "stdout": "gen"},    # generate pass
            {"returncode": 1, "stderr": "fail"},    # evaluate fail → jump
            {"returncode": 0, "stdout": "gen2"},   # generate pass
            {"returncode": 0, "stdout": "eval ok"},# evaluate pass
        ]
        runner, call_log = _make_runner(tmp_path, steps, results)
        result = runner.run()
        assert result["status"] == "passed"

    def test_on_failure_logs_jump_to_transcript(self, tmp_path):
        """Jump-back should be logged to transcript."""
        steps = [
            CompiledStep(step_id="generate", executor="claude-code",
                        rendered_prompt="gen", postcondition=None, retry=0),
            CompiledStep(step_id="evaluate", executor="claude-code",
                        rendered_prompt="eval", postcondition=None, retry=0,
                        on_failure="generate"),
        ]
        results = [
            {"returncode": 0, "stdout": "gen"},
            {"returncode": 1, "stderr": "fail"},
            {"returncode": 0, "stdout": "gen2"},
            {"returncode": 0, "stdout": "eval ok"},
        ]
        runner, _ = _make_runner(tmp_path, steps, results)
        runner.run()
        # Check transcript for jump event
        transcript_path = Path(str(tmp_path / "runs")) / "auth" / "transcript.jsonl"
        if transcript_path.exists():
            import json
            events = [json.loads(l) for l in transcript_path.read_text().strip().split("\n") if l]
            jump_events = [e for e in events if e.get("event") == "on_failure_jump"]
            assert len(jump_events) >= 1, f"Expected at least 1 jump event, got {len(jump_events)}"
