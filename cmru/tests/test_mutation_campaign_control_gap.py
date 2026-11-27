"""Campaign-level coverage for an unjudgeable known-good control gap."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "assay" / "src"))
from assay import diff as assay_diff  # noqa: E402
from assay import git as assay_git  # noqa: E402
from assay import mutation as assay_mutation  # noqa: E402
from assay.resource_limits import ResourceLimitCounters  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "cmru" / "tools"))
import mutation_campaign  # noqa: E402


def _counters() -> ResourceLimitCounters:
    return ResourceLimitCounters(
        pids_max=0, memory_oom=0, memory_max=0, memory_oom_kill=0,
        memory_oom_group_kill=0,
        limit_signature=(("/cg", 1, True, True, True, True, True, True, False, False),),
        limit_values=((100, 200),),
    )


@pytest.mark.parametrize("resume_exists", [False, True])
def test_control_gap_too_wide_is_inconclusive_without_evidence_write(
    tmp_path, monkeypatch, capsys, resume_exists,
):
    import assay

    repo = tmp_path / "repo"
    project = repo / "cmru"
    project.mkdir(parents=True)
    assay_source = repo / "assay"
    assay_package = assay_source / "src" / "assay"
    assay_package.mkdir(parents=True)
    (assay_package / "__init__.py").write_text("__version__ = 'test'\n", encoding="utf-8")
    (assay_source / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")

    # _run_campaign prepends the fake assay source to sys.path; restore it after the test.
    monkeypatch.setattr(sys, "path", list(sys.path))
    evidence_path = tmp_path / "mutation.json"
    resume_bytes = b"existing resumable campaign evidence\n"
    if resume_exists:
        evidence_path.write_bytes(resume_bytes)
        monkeypatch.setattr(mutation_campaign, "_resume_results", lambda **_kwargs: [])

    resource_reads = iter((_counters(), _counters()))
    monkeypatch.setattr(mutation_campaign, "_assay_source_package_paths", lambda _source: ["src"])
    monkeypatch.setattr(mutation_campaign, "_assay_source_tree_status", lambda _source: "")
    monkeypatch.setattr(mutation_campaign, "_assay_import_is_bound_to_source", lambda *_args: True)
    monkeypatch.setattr(mutation_campaign, "_campaign_tree_status", lambda *_args: "")
    monkeypatch.setattr(mutation_campaign, "_git_head", lambda _root: "head")
    monkeypatch.setattr(mutation_campaign, "_test_manifest", lambda *_args: {})
    monkeypatch.setattr(mutation_campaign, "_fixture_matches_manifest", lambda **_kwargs: True)
    monkeypatch.setattr(mutation_campaign, "copy_project_fixture", lambda **_kwargs: None)
    monkeypatch.setattr(
        mutation_campaign, "_copy_frozen_fixture",
        lambda *, workspace, **_kwargs: workspace,
    )
    monkeypatch.setattr(
        mutation_campaign, "_read_resource_limit_counters", lambda: next(resource_reads),
    )

    def fake_run(*_args, **kwargs):
        return SimpleNamespace(
            returncode=0, stdout="", descendants_killed=0,
            process_tree_evidence={"leader_status": "exited"},
            silence_evidence={
                "longest_output_gap_upper_bound_seconds": (
                    mutation_campaign._CANDIDATE_IDLE_WINDOW_MAX_SECONDS + 1
                ),
            },
        )

    monkeypatch.setattr(mutation_campaign, "_run", fake_run)
    monkeypatch.setattr(assay_git, "resolve_base", lambda *_args, **_kwargs: "base")
    monkeypatch.setattr(assay_git, "run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(assay_diff, "parse_added_lines", lambda _diff: [])
    monkeypatch.setattr(assay_mutation, "resolve_mutation_targets", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(assay_mutation, "collect_mutation_sites", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(assay, "__file__", str(assay_package / "__init__.py"))

    progress_path = tmp_path / "progress.jsonl"
    argv = [
        "--repo-root", str(repo), "--project-root", str(project),
        "--assay-source", str(assay_source), "--base", "base",
        "--max-mutants", "1",
    ]
    if resume_exists:
        argv.append("--resume")
    argv.extend([
        "--evidence", str(evidence_path), "--progress", str(progress_path),
        "--", sys.executable, "-m", "pytest", "tests",
    ])

    result = mutation_campaign._run_campaign(
        argv, template_root=tmp_path / "fixture-template",
    )

    progress = [
        json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    output = capsys.readouterr().out
    assert result == 2
    assert "mutation: INCONCLUSIVE" in output and "exit=2" in output
    assert progress[-1]["event"] == "complete"
    assert progress[-1]["status"] == "inconclusive"
    if resume_exists:
        assert evidence_path.read_bytes() == resume_bytes
    else:
        assert not evidence_path.exists()
