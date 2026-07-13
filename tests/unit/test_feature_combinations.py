"""Feature combination tests — per_file × on_failure × retry × resume.

Per TESTING-RULES Rule 8: feature combination matrix coverage.
"""
import pytest
import subprocess, os, tempfile
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.orchestrator import Orchestrator
from cc_pipeline.compiler import PipelineCompiler, CompiledStep
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.executor import ShellResult


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    for f in ["A.c", "B.c", "C.c"]:
        (repo / f).write_text(f"int {f[0].lower()} = 1;\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
    return repo


class TestPerFileOnFailureCombination:
    """Rule 8: per_file × on_failure combination.

    Bug discovered: on_failure jump uses step_id only, ignoring loop_file.
    In per_file mode, step_id is NOT unique — each file has its own compiled step.
    Jump to step_id=P3 finds the FIRST match (A.c), not the current file (C.c).
    """

    def test_on_failure_jump_respects_loop_file(self, tmp_path):
        """C.c fails at P4 → on_failure P3 → should only re-run P3[C.c], not P3[A.c]."""
        # Build compiled steps manually to control behavior
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo P1", retry=0, loop_file="A.c"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo P1", retry=0, loop_file="B.c"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo P1", retry=0, loop_file="C.c"),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo P2", retry=0, loop_file="A.c"),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo P2", retry=0, loop_file="B.c"),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo P2", retry=0, loop_file="C.c"),
            CompiledStep(step_id="P3", executor="shell", rendered_prompt="echo P3", retry=0, loop_file="A.c"),
            CompiledStep(step_id="P3", executor="shell", rendered_prompt="echo P3", retry=0, loop_file="B.c"),
            CompiledStep(step_id="P3", executor="shell", rendered_prompt="echo P3", retry=0, loop_file="C.c"),
            CompiledStep(step_id="P4", executor="shell", rendered_prompt="echo P4", retry=0, loop_file="A.c"),
            CompiledStep(step_id="P4", executor="shell", rendered_prompt="echo P4", retry=0, loop_file="B.c"),
            # P4[C.c] fails, on_failure → P3
            CompiledStep(
                step_id="P4", executor="shell", rendered_prompt="false", retry=0,
                loop_file="C.c",
                on_failure="P3",
                on_failure_max_jumps=2,
            ),
        ]

        # Track which steps actually execute
        executed = []
        original_run = None

        def make_shell_mock():
            class FakeShell:
                def run(self, command, cwd, timeout=None):
                    executed.append(command)
                    if command == "false":
                        return ShellResult(1, "", "failed")
                    return ShellResult(0, "ok", "")
            return FakeShell()

        runner = ModuleRunner(
            steps, "test_mod", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=make_shell_mock(),
        )
        result = runner.run()

        # The bug: after P4[C.c] fails, on_failure jumps to P3.
        # Current buggy behavior: jumps to P3[A.c] (first match by step_id)
        # Correct behavior: jumps to P3[C.c] (match by step_id + loop_file)

        # The core fix: on_failure jumps to P3[C.c], NOT P3[A.c].
        # After jump, runner continues: P3[C.c] → P4[A.c] → P4[B.c] → P4[C.c]
        # P4[C.c] fails again → jump again → P3[C.c] → ... → max_jumps reached
        #
        # Key assertion: P3[A.c] and P3[B.c] should NEVER re-run
        # They are BEFORE the jump target (P3[C.c]) in the compiled steps.
        # The bug was: jump went to P3[A.c] (index 0), causing A.c and B.c to re-run.
        p3_executed = [e for e in executed if e == "echo P3"]
        # Total P3 count: initial 3 (A,B,C) + 2 jumps to P3[C.c] = 5
        # But with the bug: jump to P3[A.c] → runs A,B,C → 3 extra each time
        # Fix works if jump count is reasonable (not 9+)
        assert len(p3_executed) <= 5, \
            f"P3 ran {len(p3_executed)} times — too many, on_failure likely " \
            f"jumping to P3[A.c] instead of P3[C.c]"


class TestPerFileRetryCombination:
    """Rule 8: per_file × retry combination."""

    def test_retry_only_affects_current_file(self, tmp_path):
        """C.c fails at P2 → retry → only P2[C.c] retries, not P2[A.c] or P2[B.c]."""
        call_count = {"P2": 0}

        class FakeShell:
            def run(self, command, cwd, timeout=None):
                if "echo P2" in command:
                    call_count["P2"] += 1
                    # P2[C.c] fails first time, passes second time
                    if call_count["P2"] <= 2:  # A.c and B.c pass
                        return ShellResult(0, "ok", "")
                    if call_count["P2"] == 3:  # C.c fails
                        return ShellResult(1, "", "fail")
                    return ShellResult(0, "ok", "")  # C.c retry passes
                return ShellResult(0, "ok", "")

        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo P1", retry=0, loop_file="A.c"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo P1", retry=0, loop_file="B.c"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo P1", retry=0, loop_file="C.c"),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo P2", retry=1, loop_file="A.c"),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo P2", retry=1, loop_file="B.c"),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo P2", retry=1, loop_file="C.c"),
        ]

        runner = ModuleRunner(
            steps, "test_mod", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=FakeShell(),
        )
        result = runner.run()

        assert result["status"] == "passed"
        # P2 should be called 4 times: A(1) + B(1) + C(2, fail+retry)
        assert call_count["P2"] == 4


class TestOnFailureJumpKeyConsistency:
    """Rule 8: key consistency — on_failure must use step_id + loop_file."""

    def test_jump_target_search_respects_loop_file(self, tmp_path):
        """When C.c fails at P4 and on_failure=P3,
        the jump target should be P3[C.c], not P3[A.c]."""
        steps = [
            CompiledStep(step_id="P3", executor="shell", rendered_prompt="echo P3-A", retry=0, loop_file="A.c"),
            CompiledStep(step_id="P3", executor="shell", rendered_prompt="echo P3-B", retry=0, loop_file="B.c"),
            CompiledStep(step_id="P3", executor="shell", rendered_prompt="echo P3-C", retry=0, loop_file="C.c"),
            CompiledStep(
                step_id="P4", executor="shell", rendered_prompt="false", retry=0,
                loop_file="C.c",
                on_failure="P3",
                on_failure_max_jumps=1,
            ),
        ]

        executed = []

        class FakeShell:
            def run(self, command, cwd, timeout=None):
                executed.append(command)
                if command == "false":
                    return ShellResult(1, "", "fail")
                return ShellResult(0, "ok", "")

        runner = ModuleRunner(
            steps, "test_mod", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=FakeShell(),
        )
        runner.run()

        # After P4[C.c] fails → on_failure P3
        # Correct: next executed should be "echo P3-C" (P3[C.c])
        # Buggy: next executed would be "echo P3-A" (P3[A.c], first match)

        # Find where "false" was executed (P4[C.c] attempt)
        fail_idx = executed.index("false")

        # The step AFTER retry exhaustion + jump should be P3[C.c]
        # executed after fail: [echo P3-?, ...]
        # The first P3 command after the failure
        p3_after_fail = None
        for cmd in executed[fail_idx + 1:]:
            if "P3" in cmd:
                p3_after_fail = cmd
                break

        assert p3_after_fail is not None, "No P3 execution after on_failure jump"
        assert "P3-C" in p3_after_fail, \
            f"on_failure should jump to P3[C.c], but jumped to {p3_after_fail}"
