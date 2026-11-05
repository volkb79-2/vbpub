"""RG-242: self-explaining lane container names plus `run-gate.*` labels.

Pins the name builder (shape, sanitisation, length, uniqueness), the label set,
the two-generation compatibility matcher, and that the shipped consumers
(remote-gate cleanup, run-jobs-window) still find both name generations.
"""

import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import _testdeps  # noqa: F401
from test_run_gate import (RUN_GATE_DIR, docker_runs, fake_docker, make_project,
                           make_worktree, run_gate, run_tool, SIMPLE_LANE)
from test_run_gate import make_repo
# The suite's autouse environment fixtures (ambient slice, profiling off, no
# host profiler socket) must apply to this module's lane runs too.
from test_run_gate import (  # noqa: F401
    ambient_cgroup, isolate_host_profiler_socket, profiling_off_by_default)

SHA = "ccd96cda646015ed0c4af5b3c757e8ad94fb4688"
NS = 1_793_889_725_123_456_789  # 2026-11-05 14:22:05.123456789 UTC-ish
DOCKER_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")
SHAPE = re.compile(
    r"vbpub-run-gate-cmru-assay-r2-s4-ccd96cda-\d{6}-\d{4}-123456789")


@pytest.fixture(autouse=True)
def _fresh_issue_set():
    run_gate._lane_names_issued.clear()
    yield
    run_gate._lane_names_issued.clear()


def ident(repo="vbpub", project="cmru", lane="assay-r2-s4", commit=SHA,
          now_ns=NS):
    return run_gate.lane_container_identity(
        Path("/x") / repo, Path("/x") / repo / project, lane, commit,
        now_ns=now_ns)


class TestNameShape:
    def test_scheme_example(self):
        name = ident()["name"]
        assert SHAPE.fullmatch(name), name
        assert run_gate.is_lane_container_name(name)
        assert DOCKER_NAME.fullmatch(name)

    def test_stamp_is_utc_yymmdd_hhmm_and_ns_is_second_fraction(self):
        name = ident(now_ns=1_000_000_000 * 86400 + 7)["name"]  # 1970-01-02
        assert name.endswith("-700102-0000-000000007"), name

    def test_sha8_is_the_commit_prefix(self):
        assert "-ccd96cda-" in ident()["name"]

    def test_missing_or_malformed_commit_uses_placeholder(self):
        assert "-00000000-" in ident(commit=None)["name"]
        assert "-00000000-" in ident(commit="HEAD")["name"]
        assert "-00000000-" in ident(commit="abc")["name"]  # < 8 hex

    def test_project_falls_back_to_repo_name_when_dir_name_empty(self):
        got = run_gate.lane_container_identity(
            Path("/x/vbpub"), Path("/"), "l", SHA, now_ns=NS)
        assert got["labels"]["run-gate.project"] == "vbpub"
        assert "-run-gate-vbpub-" in got["name"]


class TestSanitisation:
    @pytest.mark.parametrize("raw, expect", [
        ("plain", "plain"),
        ("a b/c:d", "a-b-c-d"),
        ("--lead", "lead"),
        ("trail--", "trail"),
        ("ünï", "n"),
        ("a" * 80, "a" * 10),
        ("", "x"),
        ("///", "x"),
    ])
    def test_parts(self, raw, expect):
        assert run_gate.sanitize_name_part(raw, 10) == expect

    def test_truncate_does_not_leave_trailing_separator(self):
        assert run_gate.sanitize_name_part("abcd-efgh", 5) == "abcd"

    def test_hostile_values_yield_a_valid_docker_name(self):
        got = ident(repo="my repo!", project="p/q", lane="l a;ne$")["name"]
        assert DOCKER_NAME.fullmatch(got), got
        assert run_gate.is_lane_container_name(got)

    def test_labels_keep_the_full_unsanitised_values(self):
        got = ident(repo="my repo!", project="p/q", lane="l a;ne$")["labels"]
        assert got["run-gate.repo"] == "my repo!"
        assert got["run-gate.lane"] == "l a;ne$"


class TestLength:
    def test_long_parts_are_capped(self):
        got = ident(repo="r" * 300, project="p" * 300, lane="l" * 300)["name"]
        assert len(got) == run_gate.LANE_NAME_MAX_LEN == 122
        assert run_gate.is_lane_container_name(got)

    def test_typical_name_well_under_cap(self):
        assert len(ident()["name"]) < 80


class TestUniqueness:
    def test_same_instant_same_process_never_collides(self):
        names = {ident()["name"] for _ in range(200)}
        assert len(names) == 200

    def test_threads_starting_in_the_same_nanosecond_never_collide(self):
        got, lock = [], threading.Lock()

        def go():
            n = ident()["name"]
            with lock:
                got.append(n)

        threads = [threading.Thread(target=go) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(got)) == 32

    def test_ns_field_advances_past_a_used_name(self):
        a, b = ident()["name"], ident()["name"]
        assert a.endswith("-123456789") and b.endswith("-123456790")

    def test_distinct_lanes_or_commits_differ(self):
        assert ident(lane="a")["name"] != ident(lane="b")["name"]
        assert ident(commit="1" * 40)["name"] != ident()["name"]

    def test_ns_rollover_into_the_next_second(self):
        base = 1_000_000_000 * 100 + 999_999_999
        a = ident(now_ns=base)["name"]
        b = ident(now_ns=base)["name"]
        assert a.endswith("-999999999") and b.endswith("-000000000")
        assert a != b

    def test_real_clock_two_processes_differ(self):
        code = ("import sys; sys.path.insert(0, %r); import run_gate, pathlib;"
                "print(run_gate.lane_container_identity(pathlib.Path('/x/r'),"
                "pathlib.Path('/x/r/p'),'l','a'*40)['name'])" % str(RUN_GATE_DIR))
        outs = {subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, check=True).stdout for _ in range(3)}
        assert len(outs) == 3


class TestLabels:
    def test_label_set(self):
        got = ident()["labels"]
        assert got == {
            "run-gate.repo": "vbpub", "run-gate.project": "cmru",
            "run-gate.lane": "assay-r2-s4", "run-gate.commit": SHA,
            "run-gate.name-schema": "2",
            "run-gate.started": got["run-gate.started"],
        }
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                            got["run-gate.started"])

    def test_started_matches_the_name_stamp(self):
        got = ident(now_ns=1_000_000_000 * 86400)
        assert got["labels"]["run-gate.started"] == "1970-01-02T00:00:00Z"

    def test_no_commit_gives_empty_label(self):
        assert ident(commit=None)["labels"]["run-gate.commit"] == ""

    def test_job_and_campaign_labels_only_when_set(self, monkeypatch):
        monkeypatch.delenv("RUN_GATE_JOB", raising=False)
        monkeypatch.delenv("RUN_GATE_CAMPAIGN", raising=False)
        assert "run-gate.job" not in ident()["labels"]
        monkeypatch.setenv("RUN_GATE_JOB", " j-7 ")
        monkeypatch.setenv("RUN_GATE_CAMPAIGN", "r25d38")
        got = ident()["labels"]
        assert got["run-gate.job"] == "j-7"
        assert got["run-gate.campaign"] == "r25d38"
        monkeypatch.setenv("RUN_GATE_JOB", "   ")
        assert "run-gate.job" not in ident()["labels"]


class TestRepoName:
    def test_plain_name_is_kept(self):
        assert run_gate.lane_repo_name(Path("/a/vbpub")) == "vbpub"

    def test_sha_named_checkout_uses_origin_url(self, tmp_path):
        repo = tmp_path / SHA
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                        "git@github.com:volkb79/vbpub.git"], check=True)
        assert run_gate.lane_repo_name(repo) == "vbpub"

    def test_sha_named_checkout_without_origin_keeps_dir_name(self, tmp_path):
        repo = tmp_path / SHA
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        assert run_gate.lane_repo_name(repo) == SHA

    def test_sha_named_non_repo_keeps_dir_name(self, tmp_path):
        repo = tmp_path / SHA
        repo.mkdir()
        assert run_gate.lane_repo_name(repo) == SHA

    def test_git_missing_keeps_dir_name(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no git")
        monkeypatch.setattr(run_gate.subprocess, "run", boom)
        assert run_gate.lane_repo_name(Path("/a/" + SHA)) == SHA


class TestCompatibilityMatcher:
    @pytest.mark.parametrize("name", [
        # legacy generation, as printed by the r2 board
        f"run-gate-{SHA}-assay-r2-s4-1149-1793889725",
        "run-gate-repo-suite-123-456",
        # new generation
        "vbpub-run-gate-cmru-assay-r2-s4-ccd96cda-261105-1425-123456789",
        "dstdns-run-gate-dstdns-r2-00000000-261105-1425-000000007",
    ])
    def test_matches(self, name):
        assert run_gate.is_lane_container_name(name)

    @pytest.mark.parametrize("name", [
        "", "nginx", "vbpub-nyxloom-db", "run-gate", "run-gate-",
        "x-run-gate-y",  # new-generation shape needs sha8-stamp-ns tail
        " run-gate-x", "run-gate-x y",
    ])
    def test_rejects(self, name):
        assert not run_gate.is_lane_container_name(name)

    def test_search_semantics_are_not_used(self):
        assert not run_gate.is_lane_container_name("zz/run-gate-x")


class TestEndToEndArgv:
    def test_docker_run_carries_name_and_labels(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, SIMPLE_LANE)
        log = fake_docker(tmp_path, monkeypatch)
        wt = make_worktree(repo, tmp_path)
        proc = run_tool(proj, "suite", "--worktree", str(wt))
        assert proc.returncode == 0, proc.stderr
        call = docker_runs(log)[0]
        name = call[call.index("--name") + 1]
        assert run_gate.is_lane_container_name(name)
        assert name.startswith("repo-run-gate-proj-suite-")
        labels = {call[i + 1].split("=", 1)[0]: call[i + 1].split("=", 1)[1]
                  for i, v in enumerate(call) if v == "--label"
                  and call[i + 1].startswith("run-gate.")}
        assert labels["run-gate.repo"] == "repo"
        assert labels["run-gate.project"] == "proj"
        assert labels["run-gate.lane"] == "suite"
        assert re.fullmatch(r"[0-9a-f]{40}", labels["run-gate.commit"])
        assert f"-{labels['run-gate.commit'][:8]}-" in name
        assert labels["run-gate.name-schema"] == "2"
        assert "run-gate.started" in labels


class TestConsumers:
    """The shipped shell consumers must find BOTH name generations."""

    ROOT = RUN_GATE_DIR.parent

    def test_remote_gate_cleanup_matches_both_generations(self):
        text = (self.ROOT / "scripts/netcup/runner/remote-gate.py").read_text()
        assert "label=run-gate.lane" in text
        assert "^run-gate-" in text  # legacy names kept

    def test_run_jobs_window_matches_both_generations(self):
        text = (self.ROOT / "scripts/netcup/runner/run-jobs-window.sh").read_text()
        assert "label=run-gate.commit" in text
        assert "^run-gate-$commit-" in text

    def test_legacy_prefix_not_the_only_pattern_anywhere(self):
        # every tracked script that lists lane containers must know the label
        for rel in ("scripts/netcup/runner/remote-gate.py",
                    "scripts/netcup/runner/run-jobs-window.sh"):
            assert "run-gate." in (self.ROOT / rel).read_text()
