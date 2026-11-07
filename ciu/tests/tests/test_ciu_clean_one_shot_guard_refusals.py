"""CIU-141: every fail-closed refusal of the clean one-shot guard.

``engine.guard_clean_one_shots`` decides whether `ciu clean` may let Compose
remove detached `ciu run` containers. Anything it cannot prove about a
container, the checkout or Docker's answer must refuse BEFORE a removal. The
neighbouring tests cover the foreign-checkout and same-checkout verdicts; these
cover each way the evidence can be missing or inconsistent. Docker is replaced
by a scripted ``procutil.run_cmd``; no real Docker is used.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine, worktree  # noqa: E402

PROJECT = "demo-dev-runner"
CID = "a" * 64


def _document(root: Path, **overrides):
    labels = {
        "com.docker.compose.project": PROJECT,
        "com.docker.compose.oneoff": "True",
        "com.docker.compose.project.working_dir": str(root),
        "ciu.run": "1",
        "ciu.run.stack": "tools/runner",
    }
    labels.update(overrides.pop("labels", {}))
    document = {"Id": CID, "Name": "/one-shot", "Config": {"Labels": labels}}
    document.update(overrides)
    return document


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "current"
    stack = root / "tools" / "runner"
    stack.mkdir(parents=True)
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)
    return root, stack


def _docker(monkeypatch, *, ps=(0, CID + "\n"), inspect=(0, None)):
    """Script ``docker ps`` and ``docker inspect``; any other command fails the test."""
    calls = []

    def run_cmd(argv, **_kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(argv, ps[0], ps[1], "")
        if argv[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(argv, inspect[0], inspect[1], "")
        raise AssertionError(f"the guard must not run {argv!r}")

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)
    return calls


def _guard(root, stack):
    engine.guard_clean_one_shots(PROJECT, repo_root=root, stack_dir=stack)


def test_a_stack_outside_the_root_is_refused_before_docker(checkout, tmp_path, monkeypatch):
    root, _stack = checkout
    calls = _docker(monkeypatch)
    with pytest.raises(engine.ComposeError, match=r"cannot prove clean stack .* belongs to CIU root"):
        _guard(root, tmp_path / "elsewhere")
    assert calls == []


@pytest.mark.parametrize("error", [
    OSError("no git"), ValueError("bad record"), worktree.WorktreeError("no identity"),
])
def test_unprovable_checkout_ownership_is_refused_before_docker(checkout, monkeypatch, error):
    root, stack = checkout
    calls = _docker(monkeypatch)

    def boom(_root):
        raise error

    monkeypatch.setattr(engine, "workspace_ownership_labels", boom)
    with pytest.raises(engine.ComposeError, match=f"cannot prove this checkout's ownership.*{error}"):
        _guard(root, stack)
    assert calls == []


def test_a_failed_container_listing_refuses(checkout, monkeypatch):
    root, stack = checkout
    _docker(monkeypatch, ps=(1, ""))
    with pytest.raises(engine.ComposeError, match="Docker failed while guarding clean one-shots"):
        _guard(root, stack)


def test_a_listing_without_full_ids_refuses_before_any_inspect(checkout, monkeypatch):
    root, stack = checkout
    calls = _docker(monkeypatch, ps=(0, CID[:12] + "\n"))
    with pytest.raises(engine.ComposeError, match="did not return full IDs"):
        _guard(root, stack)
    assert not any(argv[:2] == ["docker", "inspect"] for argv in calls)


def test_no_one_shots_passes_without_inspecting(checkout, monkeypatch):
    root, stack = checkout
    calls = _docker(monkeypatch, ps=(0, "\n"))
    _guard(root, stack)
    assert len(calls) == 1


def test_an_inspect_failure_refuses(checkout, monkeypatch):
    root, stack = checkout
    _docker(monkeypatch, inspect=(1, ""))
    with pytest.raises(engine.ComposeError, match=f"one-shot {CID} changed during clean ownership inspection"):
        _guard(root, stack)


@pytest.mark.parametrize("stdout", ["", "not json", "{"])
def test_unparseable_inspect_output_refuses(checkout, monkeypatch, stdout):
    root, stack = checkout
    _docker(monkeypatch, inspect=(0, stdout))
    with pytest.raises(engine.ComposeError, match="invalid inspect data"):
        _guard(root, stack)


def test_a_non_text_inspect_payload_refuses(checkout, monkeypatch):
    root, stack = checkout
    _docker(monkeypatch, inspect=(0, 12))
    with pytest.raises(engine.ComposeError, match="invalid inspect data"):
        _guard(root, stack)


@pytest.mark.parametrize("payload", [
    {}, "x", [], [{}, {}], ["not-a-dict"],
])
def test_an_inspect_document_that_is_not_exactly_one_container_refuses(
    checkout, monkeypatch, payload,
):
    root, stack = checkout
    _docker(monkeypatch, inspect=(0, json.dumps(payload)))
    with pytest.raises(engine.ComposeError, match="did not identify exact one-shot"):
        _guard(root, stack)


def test_an_inspect_answer_for_a_different_container_refuses(checkout, monkeypatch):
    root, stack = checkout
    other = _document(root)
    other["Id"] = "b" * 64
    _docker(monkeypatch, inspect=(0, json.dumps([other])))
    with pytest.raises(engine.ComposeError, match="inspect changed one-shot ID"):
        _guard(root, stack)


@pytest.mark.parametrize("config", [None, {}, [], {"Labels": None}, {"Labels": []}])
def test_a_container_without_a_label_map_refuses(checkout, monkeypatch, config):
    root, stack = checkout
    document = _document(root)
    document["Config"] = config
    _docker(monkeypatch, inspect=(0, json.dumps([document])))
    with pytest.raises(engine.ComposeError, match="no provable ownership labels"):
        _guard(root, stack)


@pytest.mark.parametrize("label", [
    "com.docker.compose.project", "com.docker.compose.oneoff", "ciu.run",
])
def test_a_container_that_stopped_matching_the_listing_filter_refuses(
    checkout, monkeypatch, label,
):
    root, stack = checkout
    document = _document(root, labels={label: "changed"})
    _docker(monkeypatch, inspect=(0, json.dumps([document])))
    with pytest.raises(engine.ComposeError, match="changed its clean ownership labels"):
        _guard(root, stack)


def test_a_worktree_checkout_owns_a_one_shot_carrying_its_ownership_labels(
    checkout, monkeypatch,
):
    root, stack = checkout
    ours = {"ciu.instance": "inst-1", "ciu.repo-root": str(root)}
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: ours)
    # The compose working_dir is deliberately foreign: for a worktree the
    # ownership labels, not working_dir, are the evidence of ownership.
    document = _document(root, labels={**ours, "com.docker.compose.project.working_dir": "/elsewhere"})
    _docker(monkeypatch, inspect=(0, json.dumps([document])))
    _guard(root, stack)


@pytest.mark.parametrize("theirs", [
    {"ciu.instance": "inst-2", "ciu.repo-root": "ROOT"},
    {"ciu.instance": "inst-1"},
    {},
])
def test_a_worktree_checkout_refuses_a_one_shot_owned_by_another_instance(
    checkout, monkeypatch, theirs,
):
    root, stack = checkout
    ours = {"ciu.instance": "inst-1", "ciu.repo-root": str(root)}
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: ours)
    labels = {key: (str(root) if value == "ROOT" else value) for key, value in theirs.items()}
    # working_dir matches this root, which must NOT be enough for a worktree.
    document = _document(root, labels=labels)
    _docker(monkeypatch, inspect=(0, json.dumps([document])))
    with pytest.raises(engine.ComposeError, match="from another checkout or stack"):
        _guard(root, stack)


def test_a_same_root_one_shot_of_another_stack_is_refused(checkout, monkeypatch):
    root, stack = checkout
    document = _document(root, labels={"ciu.run.stack": "tools/other"})
    _docker(monkeypatch, inspect=(0, json.dumps([document])))
    with pytest.raises(engine.ComposeError, match=r"one-shot a{64} \(one-shot\) from another checkout or stack"):
        _guard(root, stack)


def test_prepare_stack_refuses_a_result_without_rendered_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "main_execution", lambda **_kwargs: {"prepared_stack": None})
    with pytest.raises(engine.ComposeError, match=r"\[S8\.3\] stack preparation completed without rendered Compose inputs"):
        engine.prepare_stack(tmp_path)
