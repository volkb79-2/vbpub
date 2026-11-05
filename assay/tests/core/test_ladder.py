"""B230: the provisioning ladder, the shared template and `assay template ensure`.

Overlay slots are emulated by a copy of the template: assay only sees a
directory that shows the template's content, which is exactly what an overlay
mount is.  The pure parts (environment, plan, key, publish) are tested without
a repository.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest
from test_isolation import TIMEOUT, _CheapCloneWorkspaceProvider, _git, _scratch, _spec
from test_isolation_tmpfs_worker import FILES, _candidate, _open, _policy, _repo

from assay import _ladder, _tmpfs_worker, cli, isolation, mutation, runner
from assay.config import IsolationConfig
from assay.errors import LaneConfigError


@pytest.fixture
def shm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "shm"
    directory.mkdir()
    monkeypatch.setattr(_tmpfs_worker, "_mount_type", lambda path, mounts: "tmpfs")
    return directory


# -- environment, plan, key --------------------------------------------------


def test_environment_is_parsed_and_every_problem_is_named() -> None:
    nothing = _ladder.read_environment({})
    assert (nothing.template_dir, nothing.slots, nothing.problems) == (None, (), ())
    empty = _ladder.read_environment(
        {_ladder.ENV_TEMPLATE_DIR: "", _ladder.ENV_WORKER_SLOTS: ""}
    )
    assert empty == nothing
    good = _ladder.read_environment(
        {_ladder.ENV_TEMPLATE_DIR: "/t/tree", _ladder.ENV_WORKER_SLOTS: "/w/0:/w/1"}
    )
    assert good.template_dir == Path("/t/tree")
    assert good.slots == (Path("/w/0"), Path("/w/1"))
    assert good.problems == ()
    bad = _ladder.read_environment(
        {_ladder.ENV_TEMPLATE_DIR: "rel/x", _ladder.ENV_WORKER_SLOTS: "/w/0::/w/../x"}
    )
    assert bad.template_dir is None
    assert bad.slots == (Path("/w/0"),)
    assert len(bad.problems) == 3


def _env(template: bool, slots: int, problems: tuple[str, ...] = ()):
    return _ladder.TemplateEnvironment(
        Path("/t") if template else None,
        tuple(Path(f"/w/{i}") for i in range(slots)),
        problems,
    )


def test_plan_names_the_top_rung_and_why_higher_ones_are_skipped() -> None:
    top = _ladder.plan_ladder("template", _env(True, 2), cheap_clone=True)
    assert (top.rung, top.name, top.skipped) == ("A", "overlay", ())
    assert top.as_record()["rung"] == "A"

    slots_only = _ladder.plan_ladder("template", _env(False, 1), cheap_clone=True)
    assert slots_only.rung == "B"
    assert "is set but ASSAY_TEMPLATE_DIR is not" in slots_only.skipped[0]
    template_only = _ladder.plan_ladder("template", _env(True, 0), cheap_clone=True)
    assert "without" not in template_only.skipped[0]
    assert "ASSAY_WORKER_SLOTS is not" in template_only.skipped[0]
    neither = _ladder.plan_ladder("template", _env(False, 0, ("odd",)), cheap_clone=False)
    assert neither.rung == "B"
    assert neither.skipped[0] == "odd"
    assert "neither" in neither.skipped[1]

    plain = _ladder.plan_ladder("tmpfs-worker", _env(False, 0), cheap_clone=False)
    assert (plain.rung, plain.skipped) == ("B", ())
    ignored = _ladder.plan_ladder("tmpfs-worker", _env(True, 1), cheap_clone=False)
    assert "uses no overlay slots" in ignored.skipped[0]

    cow = _ladder.plan_ladder("cow", _env(False, 0), cheap_clone=True)
    assert (cow.rung, cow.skipped) == ("C", ())
    copy = _ladder.plan_ladder("cow", _env(True, 0), cheap_clone=False)
    assert copy.rung == "D"
    assert "ignored" in copy.skipped[0]


def test_stepping_down_records_the_reason_and_picks_c_or_d() -> None:
    plan = _ladder.plan_ladder("tmpfs-worker", _env(False, 0), cheap_clone=True)
    assert plan.below("full", cheap_clone=True).rung == "C"
    lower = plan.below("full", cheap_clone=False)
    assert lower.rung == "D"
    assert lower.skipped == ("rung B (tmpfs-copy) unavailable: full",)


def test_the_key_follows_tree_identity_and_isolation_configuration() -> None:
    base = dict(
        commit="a" * 40,
        tree_sha256="b" * 64,
        project_prefix=".",
        link_paths=("x", "y"),
        snapshot_history="shallow",
        resolved_base=None,
    )
    key = _ladder.template_key(**base)
    assert key == _ladder.template_key(**{**base, "link_paths": ("y", "x")})
    assert len(key) == 64
    for change in (
        {"commit": "c" * 40},
        {"tree_sha256": "d" * 64},
        {"project_prefix": "sub"},
        {"link_paths": ()},
        {"snapshot_history": "full"},
        {"resolved_base": "e" * 40},
    ):
        assert _ladder.template_key(**{**base, **change}) != key


# -- publishing --------------------------------------------------------------

KEY = "k" * 64


def _build(tree: str) -> None:
    Path(tree, "f").write_text("content")


@pytest.mark.parametrize("backend", ["cowtree", "builtin"])
def test_a_template_is_built_once_per_key(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if backend == "builtin":
        monkeypatch.setattr(_ladder, "_load_cowtree", lambda: None)
    else:
        pytest.importorskip("cowtree.cache")
    first = _ladder.ensure_template(tmp_path, KEY, _build, require_tmpfs=False)
    assert (first.built, first.backend) == (True, backend)
    assert first.tree == tmp_path / KEY / "tree"
    assert (first.tree / "f").read_text() == "content"
    calls = []
    second = _ladder.ensure_template(
        tmp_path, KEY, lambda tree: calls.append(tree), require_tmpfs=False
    )
    assert (second.built, calls) == (False, [])
    # no seal: the published tree stays writable
    assert first.tree.stat().st_mode & 0o200
    assert (first.tree / "f").stat().st_mode & 0o200


def test_the_two_backends_publish_the_same_directory_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("cowtree.cache")
    layouts = []
    for backend in ("cowtree", "builtin"):
        root = tmp_path / backend
        root.mkdir()
        if backend == "builtin":
            monkeypatch.setattr(_ladder, "_load_cowtree", lambda: None)
        built = _ladder.ensure_template(root, KEY, _build, require_tmpfs=False)
        assert built.backend == backend
        layouts.append(
            sorted(p.relative_to(root / KEY).as_posix() for p in (root / KEY).rglob("*"))
        )
    assert layouts[0] == layouts[1]
    assert {"tree", "tree/f", "COMPLETE", ".lease", ".last-used"} <= set(layouts[0])


def test_cowtree_can_be_declined_and_its_failures_are_wrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declined = _ladder.ensure_template(
        tmp_path, KEY, _build, require_tmpfs=False, use_cowtree=False
    )
    assert declined.backend == "builtin"

    class Broken:
        class Cache:
            def __init__(self, *_a, **_k) -> None:
                raise RuntimeError("no way")

    monkeypatch.setattr(_ladder, "_load_cowtree", lambda: Broken)
    with pytest.raises(_ladder.TemplateError, match="cowtree could not ensure"):
        _ladder.ensure_template(tmp_path, "z" * 64, _build, require_tmpfs=False)


def test_loading_cowtree_tolerates_its_absence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ladder._load_cowtree.__name__ == "_load_cowtree"
    monkeypatch.setitem(sys.modules, "cowtree", None)
    monkeypatch.delitem(sys.modules, "cowtree.cache", raising=False)
    assert _ladder._load_cowtree() is None


def test_the_builtin_publisher_refuses_what_it_cannot_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_ladder, "_load_cowtree", lambda: None)
    with pytest.raises(_ladder.TemplateError, match="not a valid cache key"):
        _ladder.ensure_template(tmp_path, "short", _build, require_tmpfs=False)
    with pytest.raises(_ladder.TemplateError, match="not a directory"):
        _ladder.ensure_template(tmp_path / "nope", KEY, _build, require_tmpfs=False)
    monkeypatch.setattr(_tmpfs_worker, "_mount_type", lambda path, mounts: "ext4")
    with pytest.raises(_ladder.TemplateError, match="not a tmpfs"):
        _ladder.ensure_template(tmp_path, KEY, _build, require_tmpfs=True)
    monkeypatch.setattr(_tmpfs_worker, "_mount_type", lambda path, mounts: "tmpfs")
    assert _ladder.ensure_template(tmp_path, KEY, _build, require_tmpfs=True).built
    monkeypatch.setattr(_tmpfs_worker, "_mount_type", lambda path, mounts: None)
    with pytest.raises(_ladder.TemplateError, match="unknown, not a tmpfs"):
        _ladder.ensure_template(tmp_path, KEY, _build, require_tmpfs=True)


def test_a_published_key_with_another_identity_and_a_failed_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_ladder, "_load_cowtree", lambda: None)
    _ladder.ensure_template(tmp_path, KEY, _build, require_tmpfs=False)
    (tmp_path / KEY / "COMPLETE").write_text('{"identity": "someone-else"}')
    with pytest.raises(_ladder.TemplateError, match="published with identity"):
        _ladder.ensure_template(tmp_path, KEY, _build, require_tmpfs=False)

    # an unpublished leftover is replaced; a failing build leaves nothing behind
    leftover = tmp_path / ("l" * 64)
    leftover.mkdir()
    (leftover / "junk").write_text("x")

    def failing(tree: str) -> None:
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        _ladder.ensure_template(tmp_path, "l" * 64, failing, require_tmpfs=False)
    assert not leftover.exists()
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name]
    rebuilt = _ladder.ensure_template(tmp_path, "l" * 64, _build, require_tmpfs=False)
    assert rebuilt.built


@pytest.mark.parametrize(
    "rung,jobs,tree,fits,cheap,refused",
    [
        ("D", 1, 10**12, False, False, False),
        ("A", 4, 10**12, False, False, False),
        ("C", 4, 10**12, False, True, False),
        ("B", 4, 10**12, True, False, False),
        ("B", 4, 10**12, False, True, False),
        ("B", 4, 10**12, False, False, True),
        ("D", 4, 1024, False, False, False),
        ("D", 4, 10**12, False, False, True),
    ],
)
def test_parallel_refusal_follows_the_ladder_not_btrfs(
    rung: str, jobs: int, tree: int, fits: bool, cheap: bool, refused: bool
) -> None:
    plan = _ladder.LadderPlan(rung, "why", ("skipped",))
    message = _ladder.parallel_refusal(
        plan, jobs=jobs, tree_bytes=tree, tmpfs_fits_all=fits, cheap_clone=cheap
    )
    assert (message is not None) is refused
    if refused:
        assert "requires a cheap_clone snapshot workspace" in message
        assert "skipped" in message


def test_the_refusal_falls_back_to_the_reason_when_nothing_was_skipped() -> None:
    plan = _ladder.LadderPlan("D", "a plain build per candidate")
    message = _ladder.parallel_refusal(
        plan, jobs=2, tree_bytes=10**12, tmpfs_fits_all=False, cheap_clone=False
    )
    assert "a plain build per candidate" in message


def test_the_ladder_log_counts_and_reports() -> None:
    log = _ladder.LadderLog(_ladder.LadderPlan("B", "why"))
    log.count("B")
    log.count("A")
    log.count("B")
    log.degradations.append("step")
    assert log.as_record() == {
        "rung": "B",
        "name": "tmpfs-copy",
        "reason": "why",
        "skipped": [],
        "degradations": ["step"],
        "candidates": {"A": 1, "B": 2},
    }


# -- the repository: template ensure, rung A, repair, degrade, no prime ------


def _template_policy(shm: Path, **changes: object) -> IsolationConfig:
    return _policy(shm, materialize="template", **changes)


def _open_env(tmp_path, repo, commit, policy, environment, provider=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    provider = provider or _CheapCloneWorkspaceProvider(tmp_path / "run")
    return isolation.prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, snapshot_policy=policy),
        timeout=TIMEOUT,
        workspace=provider,
        environment=environment,
    )


def _make_template(tmp_path: Path, repo: Path, commit: str, shm: Path) -> Path:
    """Publish the template with a throwaway run; returns its `tree`."""
    root = tmp_path / "cache"
    root.mkdir(exist_ok=True)
    with _open_env(
        tmp_path / f"pub-{os.urandom(3).hex()}", repo, commit, _template_policy(shm), {}
    ) as prepared:
        return prepared.ensure_template(root, timeout=TIMEOUT, require_tmpfs=False).tree


def _slots(tmp_path: Path, template: Path, count: int) -> list[Path]:
    slots = []
    for index in range(count):
        slot = tmp_path / "slots" / f"w{index}-{os.urandom(3).hex()}"
        shutil.copytree(template, slot, symlinks=True)
        slots.append(slot)
    return slots


def _offer(template: Path, slots: list[Path]) -> dict[str, str]:
    return {
        _ladder.ENV_TEMPLATE_DIR: str(template),
        _ladder.ENV_WORKER_SLOTS: ":".join(str(s) for s in slots),
    }


def test_template_ensure_publishes_the_primed_unsealed_tree(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    assert template.parent.name == _make_key(tmp_path, repo, commit, shm)
    assert (template / ".git" / "HEAD").read_text() == commit + "\n"
    assert (template / "other.txt").read_bytes() == FILES["other.txt"]
    assert not (template / "other.txt").stat().st_mode & 0o200 == 0
    status = _git(template, "status", "--porcelain")
    assert status == ""
    again = _make_template(tmp_path, repo, commit, shm)
    assert again == template


def _make_key(tmp_path, repo, commit, shm) -> str:
    with _open_env(
        tmp_path / f"key-{os.urandom(3).hex()}", repo, commit, _template_policy(shm), {}
    ) as prepared:
        return prepared.template_key()


def test_rung_a_serves_workers_from_slots_without_priming(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    slots = _slots(tmp_path, template, 1)
    primed = []
    original = isolation.SnapshotRepository._prime_index

    def spy(self, root, deadline):
        primed.append(root)
        return original(self, root, deadline)

    monkeypatch.setattr(isolation.SnapshotRepository, "_prime_index", spy)
    with _open_env(
        tmp_path / "run", repo, commit, _template_policy(shm), _offer(template, slots)
    ) as prepared:
        assert prepared.ladder.plan.rung == "A"
        for data in (b"x\n", b"y\n"):
            with _candidate(prepared, "other.txt", data) as snapshot:
                assert snapshot.root == slots[0]
                assert (slots[0] / "other.txt").read_bytes() == data
        assert (slots[0] / "other.txt").read_bytes() == FILES["other.txt"]
        assert prepared.ladder.candidates == {"A": 2}
        assert prepared.ladder.degradations == []
    assert primed == []  # the template's primed index is trusted on a slot
    assert not list(shm.iterdir())  # nothing was copied into the tmpfs


def test_a_drifted_slot_is_repaired_from_the_template(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    slots = _slots(tmp_path, template, 1)
    with _open_env(
        tmp_path / "run", repo, commit, _template_policy(shm), _offer(template, slots)
    ) as prepared:
        with _candidate(prepared, "other.txt", b"x\n"):
            (slots[0] / "sub" / "deep.txt").write_bytes(b"DRIFT\n")
            (slots[0] / "dup1.txt").unlink()
            shutil.rmtree(slots[0] / "bin")
            (slots[0] / "stray.txt").write_text("residue")
        with _candidate(prepared, "other.txt", b"y\n") as snapshot:
            assert snapshot.root == slots[0]
            assert (slots[0] / "sub" / "deep.txt").read_bytes() == FILES["sub/deep.txt"]
            assert (slots[0] / "dup1.txt").read_bytes() == FILES["dup1.txt"]
            assert (slots[0] / "bin" / "tool.sh").stat().st_mode & 0o111
            assert not (slots[0] / "stray.txt").exists()
        assert prepared.ladder.candidates == {"A": 2}
        assert prepared.ladder.degradations == []
        # the template itself was only read
        assert (template / "sub" / "deep.txt").read_bytes() == FILES["sub/deep.txt"]


def test_an_unrepairable_slot_is_abandoned_and_the_worker_steps_down(
    tmp_path: Path, shm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    slots = _slots(tmp_path, template, 1)
    with _open_env(
        tmp_path / "run", repo, commit, _template_policy(shm), _offer(template, slots)
    ) as prepared:
        with _candidate(prepared, "other.txt", b"x\n"):
            (slots[0] / ".git" / "HEAD").write_bytes(b"x")
            (slots[0] / ".git" / "HEAD").chmod(0o000)
        with _candidate(prepared, "other.txt", b"y\n") as snapshot:
            assert snapshot.root != slots[0]
            assert snapshot.root.is_relative_to(shm)  # rung B took over
        assert prepared.ladder.candidates == {"A": 1, "B": 1}
        assert any("could not be repaired" in line for line in prepared.ladder.degradations)
    assert slots[0].exists()  # the caller's volume is never removed by assay
    assert "provisioning ladder: overlay slot" in capsys.readouterr().err


def test_degradations_are_explicit_never_silent(
    tmp_path: Path, shm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    cases = {
        "no-slot-left": (_offer(template, []), "no free overlay slot"),
        "slot-empty": (_offer(template, [tmp_path / "empty"]), "is not usable"),
        "unreadable-template": (
            _offer(tmp_path / "missing", _slots(tmp_path, template, 1)),
            "is not readable as a template",
        ),
    }
    (tmp_path / "empty").mkdir()
    for name, (environment, expected) in cases.items():
        environment.setdefault(_ladder.ENV_WORKER_SLOTS, "")
        if name == "no-slot-left":
            # a plan of rung A needs slots: offer one, take it with another thread
            environment[_ladder.ENV_WORKER_SLOTS] = str(_slots(tmp_path, template, 1)[0])
        with _open_env(
            tmp_path / name, repo, commit, _template_policy(shm), environment
        ) as prepared:
            if name == "no-slot-left":
                results = []

                def work() -> None:
                    with _candidate(prepared, "other.txt", b"x\n") as snapshot:
                        results.append(snapshot.root)

                first = threading.Thread(target=work)
                first.start()
                first.join()
                with _candidate(prepared, "other.txt", b"y\n") as snapshot:
                    results.append(snapshot.root)
                assert results[1].is_relative_to(shm)
            else:
                with _candidate(prepared, "other.txt", b"x\n") as snapshot:
                    assert snapshot.root.is_relative_to(shm)
            assert any(expected in line for line in prepared.ladder.degradations), name
    assert "provisioning ladder" in capsys.readouterr().err


def test_a_template_of_another_commit_or_key_is_refused(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    # same files, another judged commit
    (repo / "new.txt").write_text("n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    newer = _git(repo, "rev-parse", "HEAD")
    slots = _slots(tmp_path, template, 1)
    with _open_env(
        tmp_path / "run", repo, newer, _template_policy(shm), _offer(template, slots)
    ) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as snapshot:
            assert snapshot.root.is_relative_to(shm)
        assert any("not a template of the judged commit" in d for d in prepared.ladder.degradations)
    # right commit, a key directory that is not this run's key
    imposter = tmp_path / ("f" * 64) / "tree"
    shutil.copytree(template, imposter, symlinks=True)
    with _open_env(
        tmp_path / "run2", repo, commit, _template_policy(shm), _offer(imposter, slots)
    ) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as snapshot:
            assert snapshot.root.is_relative_to(shm)
        assert any("is keyed" in d for d in prepared.ladder.degradations)
    # a template outside the key layout is accepted on its HEAD alone
    loose = tmp_path / "loose-template"
    shutil.copytree(template, loose, symlinks=True)
    with _open_env(
        tmp_path / "run3", repo, commit, _template_policy(shm), _offer(loose, _slots(tmp_path / "s3", template, 1))
    ) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as snapshot:
            assert snapshot.root.parent.parent.parent != shm
        assert prepared.ladder.candidates == {"A": 1}


def test_without_an_offer_the_ladder_starts_at_b_and_says_so(
    tmp_path: Path, shm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, commit = _repo(tmp_path)
    with _open_env(tmp_path, repo, commit, _template_policy(shm), {}) as prepared:
        assert prepared.ladder.plan.rung == "B"
        with _candidate(prepared, "other.txt", b"x\n") as snapshot:
            assert snapshot.root.is_relative_to(shm)
    err = capsys.readouterr().err
    assert "neither ASSAY_TEMPLATE_DIR nor ASSAY_WORKER_SLOTS is set" in err


def test_a_shared_template_is_the_copy_source_for_rung_b(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    environment = {_ladder.ENV_TEMPLATE_DIR: str(template)}
    with _open_env(
        tmp_path / "run", repo, commit, _template_policy(shm), environment
    ) as prepared:
        with _candidate(prepared, "other.txt", b"x\n") as snapshot:
            assert snapshot.root.is_relative_to(shm)
        assert prepared._local_template is None
        assert prepared.ladder.candidates == {"B": 1}


def test_the_cow_default_ignores_the_offer_and_says_so(
    tmp_path: Path, shm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, commit = _repo(tmp_path)
    environment = {_ladder.ENV_TEMPLATE_DIR: "/nonexistent/tree"}
    with _open_env(
        tmp_path, repo, commit, _policy(shm, materialize="cow"), environment
    ) as prepared:
        assert prepared.ladder.plan.rung == "C"
        with _candidate(prepared, "other.txt", b"x\n"):
            pass
        assert prepared.ladder.candidates == {"C": 1}
    assert "ignored" in capsys.readouterr().err
    quiet = tmp_path / "quiet"
    with _open_env(quiet, repo, commit, _policy(shm, materialize="cow"), {}) as prepared:
        assert prepared.ladder.plan.skipped == ()
    assert capsys.readouterr().err == ""


def test_the_process_environment_is_the_default_offer(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit = _repo(tmp_path)
    monkeypatch.setenv(_ladder.ENV_TEMPLATE_DIR, "/some/tree")
    with _open(tmp_path, repo, commit, _template_policy(shm)) as prepared:
        assert prepared._offer.template_dir == Path("/some/tree")


# -- jobs > 1 without a btrfs workspace -------------------------------------


def test_parallel_workers_are_admitted_by_the_ladder_not_by_btrfs(
    tmp_path: Path, shm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_ladder, "PLAIN_COPY_PARALLEL_MAX_BYTES", 1024 * 1024)
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    no_cow = tmp_path / "nocow"
    # rung A with enough slots, no workspace at all
    slots = _slots(tmp_path, template, 3)
    with _open_env(
        no_cow / "a", repo, commit, _template_policy(shm), _offer(template, slots),
        provider=_NoWorkspace(),
    ) as prepared:
        assert not prepared.cheap_clone
        assert prepared.parallel_refusal(1) is None
        assert prepared.parallel_refusal(3) is None
    # too few slots: judged as rung B (the tmpfs holds 3 trees)
    with _open_env(
        no_cow / "b", repo, commit, _template_policy(shm), _offer(template, slots[:1]),
        provider=_NoWorkspace(),
    ) as prepared:
        assert prepared.parallel_refusal(3) is None
    # a template that is unusable also falls to B
    with _open_env(
        no_cow / "b2", repo, commit, _template_policy(shm),
        _offer(tmp_path / "gone", slots),
        provider=_NoWorkspace(),
    ) as prepared:
        assert prepared.parallel_refusal(2) is None
    # nothing cheap and a large tree: refused with the reasons
    big_repo, big_commit = _repo(tmp_path / "big", big=2 * 1024 * 1024)
    with _open_env(
        no_cow / "d", big_repo, big_commit, _policy(shm, materialize="cow"), {},
        provider=_NoWorkspace(),
    ) as prepared:
        message = prepared.parallel_refusal(2)
        assert "requires a cheap_clone snapshot workspace" in message
        assert prepared.parallel_refusal(1) is None
    # the same large tree is fine when the tmpfs can hold the workers
    with _open_env(
        no_cow / "e", big_repo, big_commit,
        _policy(shm, materialize="tmpfs-worker", tmpfs_max_mib=128), {},
        provider=_NoWorkspace(),
    ) as prepared:
        assert prepared.parallel_refusal(2) is None
        # ... and refused once the declared bound cannot hold them
    with _open_env(
        no_cow / "f", big_repo, big_commit,
        _policy(shm, materialize="tmpfs-worker", tmpfs_max_mib=1), {},
        provider=_NoWorkspace(),
    ) as prepared:
        assert prepared.parallel_refusal(2) is not None


class _NoWorkspace:
    """A workspace factory that yields no workspace (the plain path)."""

    max_live_clones = 4

    from contextlib import contextmanager as _cm

    @_cm
    def open(self, estimate, *, scratch_root):
        yield None


def test_the_mutation_gate_uses_the_ladder_when_the_repository_has_one() -> None:
    class Ladder:
        cheap_clone = False

        def parallel_refusal(self, jobs: int) -> str | None:
            return "ladder says no" if jobs > 2 else None

    assert mutation.parallel_source_refusal(Ladder(), 1, "old") is None
    assert mutation.parallel_source_refusal(Ladder(), 2, "old") is None
    assert mutation.parallel_source_refusal(Ladder(), 3, "old") == "ladder says no"

    class Legacy:
        cheap_clone = True

    assert mutation.parallel_source_refusal(Legacy(), 4, "old") is None
    assert mutation.parallel_source_refusal(object(), 4, "old") == "old"
    assert mutation.parallel_source_refusal(None, 4, "old") == "old"
    assert mutation.parallel_source_refusal(None, 1, "old") is None


# -- equivalence across cow / copy / overlay / tmpfs copy --------------------


def _snapshot_state(root: Path) -> dict[str, bytes]:
    state = {}
    for current, directories, files in os.walk(root):
        directories[:] = [d for d in directories if d != ".git"]
        for name in files:
            path = Path(current, name)
            relative = path.relative_to(root).as_posix()
            state[relative] = (
                os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            ) + b"|%o" % (path.lstat().st_mode & 0o7777)
    return state


def test_every_rung_materializes_the_same_candidate_tree(
    tmp_path: Path, shm: Path
) -> None:
    repo, commit = _repo(tmp_path)
    template = _make_template(tmp_path, repo, commit, shm)
    slots = _slots(tmp_path, template, 1)
    arms = {
        "C": (_policy(shm, materialize="cow"), {}, None),
        "D": (_policy(shm, materialize="cow"), {}, _NoWorkspace()),
        "B": (_policy(shm, materialize="tmpfs-worker"), {}, None),
        "A": (_template_policy(shm), _offer(template, slots), None),
    }
    seen = {}
    for rung, (policy, environment, provider) in arms.items():
        results = []
        with _open_env(
            tmp_path / f"arm-{rung}", repo, commit, policy, environment, provider=provider
        ) as prepared:
            for name, data in (("other.txt", b"one\n"), ("dup1.txt", b"two\n"), ("other.txt", b"three\n")):
                with _candidate(prepared, name, data) as snapshot:
                    results.append(
                        (snapshot.commit, _snapshot_state(snapshot.root),
                         _git(snapshot.root, "status", "--porcelain"))
                    )
            assert set(prepared.ladder.candidates) == {rung}
        seen[rung] = results
    assert seen["A"] == seen["B"] == seen["C"] == seen["D"]


# -- template ensure on the CLI ---------------------------------------------

LANE = """\
schema_version = 2

[lanes.pkg]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["{python}", "-c", "pass"]
env = {{ PYTHONDONTWRITEBYTECODE = "1" }}
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.pkg.isolation]
snapshot_selection = "repository"

[lanes.pkg.judge]
language = "python"
source_roots = ["src"]
mode = "whole_target"
targets = ["src/mod.py"]
fail_under = 0.0
allow_excluded = true
require_branch = false

[lanes.pkg.judge.coverage]
format = "coverage-py-json"
artifact = "coverage.json"
"""


def _lane_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lane-repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("x = 1\n")
    (repo / "assay.toml").write_text(LANE.format(python=sys.executable))
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_template_ensure_prints_key_and_path_and_reuses_it(tmp_path: Path) -> None:
    repo = _lane_repo(tmp_path)
    root = tmp_path / "tc"
    root.mkdir()
    argv = [
        "template", "ensure", "--lane", "pkg", "--root", str(root),
        "--file", str(repo / "assay.toml"),
    ]
    out = io.StringIO()
    assert cli.main(argv, stdout=out, stderr=io.StringIO()) == 0
    key, path = out.getvalue().split()
    assert Path(path) == root / key / "tree"
    assert (Path(path) / "src" / "mod.py").read_text() == "x = 1\n"
    out2 = io.StringIO()
    assert cli.main(argv, stdout=out2, stderr=io.StringIO()) == 0
    assert out2.getvalue() == out.getvalue()


def test_template_ensure_refuses_an_r0_lane_and_a_non_tmpfs_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _lane_repo(tmp_path)
    lane_file = cli._resolve_lane_file(repo / "assay.toml")
    lane = lane_file.lane("pkg")
    from dataclasses import replace

    r0 = replace(lane, rigor=("R0",), isolation=None, judge=None)
    with pytest.raises(LaneConfigError, match="R0-only"):
        runner.ensure_template(
            r0, project_root=lane_file.project_root,
            snapshot_limits=lane_file.snapshot_limits, request_base=None,
            root=tmp_path, require_tmpfs=False, timeout=60.0,
        )
    err = io.StringIO()
    code = cli.main(
        ["template", "ensure", "--lane", "pkg", "--root", str(tmp_path),
         "--require-tmpfs", "--file", str(repo / "assay.toml")],
        stdout=io.StringIO(), stderr=err,
    )
    assert code != 0
    assert "not a tmpfs" in err.getvalue() or "tmpfs" in err.getvalue()
