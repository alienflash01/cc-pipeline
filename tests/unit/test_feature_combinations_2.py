"""Feature combination tests batch 2 — covering 13 untested combinations.

Per TESTING-RULES Rule 8: feature combination matrix coverage.
Priority: HIGH (likely to reveal bugs) → MEDIUM → LOW (edge cases).
"""
import pytest
import subprocess, os, json, tempfile, time
from pathlib import Path
from cc_pipeline.compiler import CompiledStep
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.executor import ShellResult, ShellExecutor, CCExecutor


class TestRetryOnFailureAmplification:
    """retry × on_failure: retry_budget resets on each step + on each jump.
    
    Total executions = (retry+1) × (max_jumps+1).
    This is a design choice, not a bug — but verify behavior.
    """
    
    def test_retry_amplification_count(self, tmp_path):
        """retry=1, on_failure_max_jumps=1 → max 4 executions of failing step."""
        call_count = {"fail_step": 0}
        
        class FakeShell:
            def run(self, command, cwd, timeout=None):
                if command == "false":
                    call_count["fail_step"] += 1
                    return ShellResult(1, "", "fail")
                return ShellResult(0, "ok", "")

        steps = [
            CompiledStep(step_id="ok_step", executor="shell", rendered_prompt="echo ok", retry=0),
            CompiledStep(
                step_id="fail_step", executor="shell", rendered_prompt="false",
                retry=1, on_failure="ok_step", on_failure_max_jumps=1,
            ),
        ]
        runner = ModuleRunner(
            steps, "test", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=FakeShell(),
        )
        result = runner.run()
        
        # retry=1 → 2 attempts per visit
        # max_jumps=1 → 2 visits (original + 1 jump)
        # Total: 2 × 2 = 4
        assert call_count["fail_step"] == 4, \
            f"Expected 4 executions (retry×jump amplification), got {call_count['fail_step']}"


class TestTimeoutShellCombination:
    """timeout × shell: shell step with timeout.
    
    ShellExecutor does NOT catch TimeoutExpired internally.
    Runner catches it and returns ExecOutcome.TIMEOUT.
    Verify: timeout → retry → fail (if persistent).
    """
    
    def test_shell_timeout_produces_timeout_outcome(self, tmp_path):
        """Shell command that times out → ExecOutcome.TIMEOUT → retry."""
        call_count = {"slow": 0}
        
        class SlowShell:
            def run(self, command, cwd, timeout=None):
                call_count["slow"] += 1
                if timeout and timeout < 2:
                    raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
                return ShellResult(0, "ok", "")
        
        steps = [
            CompiledStep(step_id="slow", executor="shell", rendered_prompt="sleep 10", retry=1, timeout=1),
        ]
        runner = ModuleRunner(
            steps, "test", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=SlowShell(),
        )
        result = runner.run()
        
        # Timeout → retry → timeout again → fail
        assert result["status"] == "failed"
        assert call_count["slow"] == 2, f"Expected 2 attempts (retry=1), got {call_count['slow']}"


class TestTimeoutPerFileCombination:
    """timeout × per_file: one file times out, others should not be affected.
    
    In batched order: P1[A] P1[B] P1[C]. If P1[B] times out and exhausts retry,
    module fails. But P1[A] should have already passed.
    """
    
    def test_one_file_timeout_doesnt_block_others(self, tmp_path):
        """P1[A.c] passes, P1[B.c] times out twice → module fails.
        Verify A.c was executed before B.c failed."""
        executed = []
        
        class TimedShell:
            def run(self, command, cwd, timeout=None):
                executed.append(command)
                if "B" in command and timeout and timeout < 2:
                    raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
                return ShellResult(0, "ok", "")
        
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo A", retry=1, timeout=10, loop_file="A.c"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo B", retry=1, timeout=1, loop_file="B.c"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo C", retry=1, timeout=10, loop_file="C.c"),
        ]
        runner = ModuleRunner(
            steps, "test", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=TimedShell(),
        )
        result = runner.run()
        
        # P1[A.c] passed, P1[B.c] timed out twice → module fails
        assert result["status"] == "failed"
        assert "echo A" in executed, "A.c should have executed before B.c failed"
        # B.c tried twice (retry=1)
        b_count = executed.count("echo B")
        assert b_count == 2, f"B.c should have 2 attempts, got {b_count}"
        # C.c should NOT have run (module stopped at B.c failure)
        assert "echo C" not in executed, "C.c should not run after B.c fails"


class TestOutputPostconditionCombination:
    """output × postcondition: output file used as postcondition input.
    
    Step writes JSON to .pipeline/{output}, postcondition reads it.
    """
    
    def test_output_file_read_by_postcondition(self, tmp_path):
        """Step creates output file → postcondition reads it → PASS."""
        import os
        
        class WriteFileShell:
            def run(self, command, cwd, timeout=None):
                # Simulate: command writes a JSON file
                pipe_dir = os.path.join(cwd, ".pipeline")
                os.makedirs(pipe_dir, exist_ok=True)
                with open(os.path.join(pipe_dir, "result.json"), "w") as f:
                    f.write('{"score": 95}')
                return ShellResult(0, "", "")
        
        steps = [
            CompiledStep(
                step_id="gen",
                executor="shell",
                rendered_prompt="echo write_json",
                output="result.json",
                retry=0,
                postcondition={
                    "shell": "cat .pipeline/result.json",
                    "expect": "$.score >= 80",
                },
            ),
        ]
        runner = ModuleRunner(
            steps, "test", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=WriteFileShell(),
        )
        result = runner.run()
        
        assert result["status"] == "passed", \
            f"Should pass: output file written, postcondition reads score=95 >= 80. Got: {result}"


class TestResumePostconditionCombination:
    """resume × postcondition: steps with postcondition skipped correctly.
    
    State.json only records completed_steps when postcondition passes.
    Resume should skip only postcondition-passed steps.
    """
    
    def test_resume_skips_steps_with_passed_postcondition(self, tmp_path):
        """Create runner with state_manager that has completed steps.
        Verify runner skips those steps (doesn't execute them)."""
        from cc_pipeline.state import StateManager
        
        sm = StateManager(str(tmp_path / "runs"))
        sm.mark_step_completed("mod", "P1", "")
        
        executed = []
        
        class TrackingShell:
            def run(self, command, cwd, timeout=None):
                executed.append(command)
                return ShellResult(0, "ok", "")
        
        # Orchestrator does the skip logic, not runner.
        # Simulate what orchestrator does: filter steps before passing to runner.
        skip_steps = sm.get_completed_steps("mod")
        assert "P1" in skip_steps
        
        all_steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo P1", retry=0),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo P2", retry=0),
        ]
        
        def _is_completed(s):
            key = f"{s.step_id}/{s.loop_file}" if s.loop_file else s.step_id
            return key in skip_steps
        
        filtered_steps = [s for s in all_steps if not _is_completed(s)]
        
        # Only P2 should remain
        assert len(filtered_steps) == 1
        assert filtered_steps[0].step_id == "P2"
        
        # Run with filtered steps
        runner = ModuleRunner(
            filtered_steps, "mod", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=TrackingShell(),
            state_manager=sm,
        )
        result = runner.run()
        
        assert result["status"] == "passed"
        assert executed == ["echo P2"], f"P1 should be skipped, got: {executed}"


class TestOnFailurePostconditionCombination:
    """on_failure × postcondition: on_failure triggered by postcondition fail.
    
    CC executes successfully (exit 0) but postcondition fails.
    retry exhausts → on_failure fires → jump to target.
    """
    
    def test_postcondition_fail_triggers_on_failure(self, tmp_path):
        """Shell succeeds but postcondition fails → retry exhausts → on_failure fires → jump."""
        call_count = {"target": 0, "main": 0}
        
        class AlwaysSucceedShell:
            def run(self, command, cwd, timeout=None):
                if "target" in command:
                    call_count["target"] += 1
                if "main" in command:
                    call_count["main"] += 1
                return ShellResult(0, "ok", "")
        
        steps = [
            CompiledStep(
                step_id="target_step",
                executor="shell",
                rendered_prompt="echo target",
                retry=0,
            ),
            CompiledStep(
                step_id="main_step",
                executor="shell",
                rendered_prompt="echo main",
                retry=0,
                on_failure="target_step",
                on_failure_max_jumps=1,
                postcondition={"shell": "false", "expect": None},  # PC always fails
            ),
        ]
        runner = ModuleRunner(
            steps, "test", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=AlwaysSucceedShell(),
        )
        result = runner.run()
        
        # main_step exec succeeds but PC fails → retry=0 exhausted → on_failure → jump to target
        # target passes → main re-runs → PC fails again → jump exhausted → module fails
        assert result["status"] == "failed"
        # target should have been called at least twice (initial + 1 jump)
        assert call_count["target"] >= 2, \
            f"on_failure should have jumped to target at least once. target called {call_count['target']} times"
        # main should have been called at least twice (initial + after jump)
        assert call_count["main"] >= 2, \
            f"main should have re-run after on_failure jump. main called {call_count['main']} times"


class TestRetryPostconditionSharedBudget:
    """retry × postcondition: exec fail and postcondition fail share retry budget.
    
    verify: 1 exec fail + 1 postcondition fail = retry budget exhausted (retry=1).
    """
    
    def test_shared_budget_exec_then_postcondition(self, tmp_path):
        """First attempt: exec fails (retry). Second attempt: exec succeeds but pc fails.
        retry=1 → 2 total attempts → budget exhausted → fail."""
        attempt = {"n": 0}
        
        class MixedShell:
            def run(self, command, cwd, timeout=None):
                attempt["n"] += 1
                if attempt["n"] == 1:
                    return ShellResult(1, "", "exec fail")  # First: exec fail
                return ShellResult(0, "ok", "")  # Second: exec ok
        
        steps = [
            CompiledStep(
                step_id="s1",
                executor="shell",
                rendered_prompt="echo step",
                retry=1,  # 1 retry → 2 attempts
                postcondition={"shell": "false", "expect": None},  # Always fails
            ),
        ]
        runner = ModuleRunner(
            steps, "test", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=MixedShell(),
        )
        result = runner.run()
        
        # Attempt 1: exec fail → retry (budget 1→0)
        # Attempt 2: exec ok, postcondition fail → budget=0 → fail
        assert result["status"] == "failed"
        assert attempt["n"] == 2, f"Expected 2 attempts (shared budget), got {attempt['n']}"
