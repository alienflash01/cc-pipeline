"""TDD: status command supports --run-dir + cross-project compatibility."""
import json
from pathlib import Path


class TestStatusRunDir:
    """status command respects --run-dir flag."""

    def test_status_with_custom_run_dir(self, tmp_path):
        """status --run-dir /custom/path → reads from custom path."""
        from cc_pipeline.cli import main

        run_dir = tmp_path / "my-runs"
        run_dir.mkdir()
        state = {"run_id": "r1", "modules": {"auth": {"status": "passed", "steps_completed": 1, "steps_total": 1}}}
        (run_dir / "orchestrator-state.json").write_text(json.dumps(state))

        mod_dir = run_dir / "auth"
        mod_dir.mkdir()
        (mod_dir / "transcript.jsonl").write_text(
            json.dumps({"event": "pass", "step": "generate"})
        )

        ret = main(["status", "--run-dir", str(run_dir)])
        assert ret == 0

    def test_status_run_id_with_run_dir(self, tmp_path):
        """status --run-id <id> --run-dir <path> → reads state from specific run."""
        from cc_pipeline.cli import main

        run_dir = tmp_path / "my-runs"
        run_dir.mkdir()
        # For --run-id, it looks for <run_dir>/<run_id>/orchestrator-state.json
        run_with_id = run_dir / "r1"
        run_with_id.mkdir()
        state = {"run_id": "r1", "modules": {"payment": {"status": "passed", "steps_completed": 3, "steps_total": 3}}}
        (run_with_id / "orchestrator-state.json").write_text(json.dumps(state))

        ret = main(["status", "--run-id", "r1", "--run-dir", str(run_dir)])
        assert ret == 0


class TestCrossProjectSupport:
    """cc-pipeline works with any git repo via config.repo."""

    def test_config_repo_field_determines_project(self):
        """The repo field in YAML sets the target project."""
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        config = PipelineConfig(
            repo="/home/user/my-project",
            base_branch="develop",
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok")],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                          variables={"line_threshold": 80, "branch_threshold": 70})],
        )
        assert config.repo == "/home/user/my-project"
        assert config.base_branch == "develop"

    def test_output_branch_prefix_configurable(self):
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        config = PipelineConfig(
            repo="/tmp",
            output_branch_prefix="code-review",
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok")],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                          variables={"line_threshold": 80, "branch_threshold": 70})],
        )
        assert config.output_branch_prefix == "code-review"

    def test_pr_labels_differ_by_project(self):
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        ut_config = PipelineConfig(
            repo="/tmp/proj1",
            pr_labels=["ut", "auto"],
            pr_title_template="UT for {module}",
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok")],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                          variables={"line_threshold": 80, "branch_threshold": 70})],
        )
        review_config = PipelineConfig(
            repo="/tmp/proj2",
            pr_labels=["code-review", "auto"],
            pr_title_template="Code Review: {module}",
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok")],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                          variables={"line_threshold": 80, "branch_threshold": 70})],
        )
        assert ut_config.pr_labels == ["ut", "auto"]
        assert review_config.pr_labels == ["code-review", "auto"]
