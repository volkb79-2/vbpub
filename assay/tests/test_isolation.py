"""P22 — committed-object snapshot substrate (ordinary production tests).

These are assay's own tests for :mod:`assay.isolation`. They deliberately do
NOT restate the carver's locked acceptance
(``nyxloom-trove/carve-assets/P22/test_acceptance.py``); they attack the axes
that suite leaves open, so that neither file can silently cover for the
other's deletion:

* the raw tree grammar as a pure function over hand-written object bytes,
  including the truncation cases a real repository cannot easily produce;
* symlink containment as a pure lexical decision, including a dangling but
  contained link and a contained cycle, which must be ACCEPTED;
* ``project_prefix = '.'`` (the locked suite only exercises a nested project);
* a zero-byte blob, which is its own branch of the ``cat-file --batch`` reader;
* the prepared-repository lifecycle (use after close, close with a live child);
* deterministic replacement identity across two INDEPENDENTLY prepared seeds,
  which the locked suite cannot show because it reuses one prepared object; and
* an injected budget expiry, proven without any wall-clock dependence.

Expected manifests are hand-authored under ``tests/fixtures/isolation/`` from
literal committed bytes and are never produced by calling the implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import stat
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from assay import git as git_module
from assay import isolation
from assay.errors import AssayError, Outcome, ReasonCode
from assay.isolation import (
    DEFAULT_SNAPSHOT_LIMITS,
    SnapshotSpec,
    prepare_snapshot,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "isolation"
ROOT_MANIFEST = json.loads((FIXTURES / "root_project_manifest.json").read_text(encoding="utf-8"))
TREE_GRAMMAR = json.loads((FIXTURES / "tree_grammar.json").read_text(encoding="utf-8"))

TIMEOUT = 600.0

#: The literal committed bytes ``root_project_manifest.json`` describes.
LITERALS: dict[str, bytes] = {
    "empty.txt": b"",
    "bin/tool.sh": b"#!/bin/sh\necho hi\n",
    "lib/data.bin": b"\x00\x01\x02binary\n",
    "n\N{LATIN SMALL LETTER A WITH ACUTE}me.txt": "unicode name\n".encode("utf-8"),
}

_FIXTURE_ENV = {
    "GIT_AUTHOR_NAME": "Assay Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@assay.invalid",
    "GIT_COMMITTER_NAME": "Assay Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@assay.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}


def _git(repo: Path, *args: str) -> str:
    """Drive the fixture repository with a real git, independently of assay."""
    env = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **_FIXTURE_ENV,
    }
    completed = subprocess.run(
        [shutil.which("git") or "git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout.decode("utf-8").strip()


def _root_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose PROJECT IS ITS ROOT, with a zero-byte blob, an
    executable, a UTF-8 name, and a symlink that descends."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, data in LITERALS.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (repo / "bin" / "tool.sh").chmod(0o755)
    os.symlink("lib/data.bin", repo / "link-to-data")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "root project base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _write_object(repo: Path, kind: str, payload: bytes) -> str:
    """Write a raw object with ``--literally`` so deliberately malformed
    shapes can be committed, independently of assay."""
    completed = subprocess.run(
        [
            shutil.which("git") or "git",
            "-C",
            str(repo),
            "hash-object",
            "--literally",
            "-w",
            "-t",
            kind,
            "--stdin",
        ],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": os.environ["PATH"], "LC_ALL": "C.UTF-8"},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout.decode().strip()


def _scratch(tmp_path: Path) -> Path:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return scratch


def _spec(repo: Path, scratch: Path, commit: str, **changes: object) -> SnapshotSpec:
    values: dict[str, object] = {
        "repo_top": repo.resolve(),
        "commit": commit,
        "project_prefix": PurePosixPath("."),
        "scratch_root": scratch.resolve(),
        "limits": DEFAULT_SNAPSHOT_LIMITS,
    }
    values.update(changes)
    return SnapshotSpec(**values)  # type: ignore[arg-type]


def _body(record: dict) -> bytes:
    return b"".join(bytes.fromhex(part) for part in record["body"])


# --------------------------------------------------------------------------
# Raw tree grammar — a pure function over hand-written object bytes
# --------------------------------------------------------------------------


def test_raw_tree_grammar_accepts_exact_records() -> None:
    """The parser yields the name as RAW BYTES on purpose.

    Decoding belongs to the walker, which owns the strict-UTF-8 refusal; a
    parser that decoded eagerly would turn an undecodable name into an
    exception with no path context, or worse, into replacement characters.
    """
    for case in TREE_GRAMMAR["accepted"]:
        parsed = isolation._parse_tree(_body(case), "t" * 40)
        rendered = [[mode, name.decode("utf-8"), oid] for mode, name, oid in parsed]
        assert rendered == case["expect"], case["id"]


@pytest.mark.parametrize("case", TREE_GRAMMAR["refused"], ids=lambda c: c["id"])
def test_raw_tree_grammar_refuses_malformed_records(case: dict) -> None:
    with pytest.raises(AssayError) as caught:
        isolation._parse_tree(_body(case), "t" * 40)
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_a_non_canonical_tree_mode_spelling_is_refused_end_to_end(
    tmp_path: Path,
) -> None:
    """``040000`` is the human spelling; git writes ``40000``.

    Git stores and returns the non-canonical spelling verbatim (verified
    against a real binary), so this is a shape a hostile or broken producer
    can genuinely put in a repository. Accepting both spellings would let the
    validator and the materializer disagree about which one they checked, so
    it is refused rather than normalized. The parser itself only reports the
    mode; the supported-mode rule is enforced where the tree is walked, and
    this asserts the behaviour through the real public entry point.
    """
    repo, base = _root_repo(tmp_path)
    subtree = _git(repo, "rev-parse", f"{base}^{{tree}}:lib")
    raw = b"040000 lib\x00" + bytes.fromhex(subtree)
    tree = _write_object(repo, "tree", raw)
    commit = _write_object(
        repo,
        "commit",
        (
            f"tree {tree}\n"
            "author Assay Fixture <fixture@assay.invalid> 946684800 +0000\n"
            "committer Assay Fixture <fixture@assay.invalid> 946684800 +0000\n\n"
            "non-canonical mode\n"
        ).encode("utf-8"),
    )
    scratch = _scratch(tmp_path)
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("a non-canonical tree mode must not be yielded")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert list(scratch.iterdir()) == []
    # the canonical spelling of the same directory entry is accepted
    assert isolation._parse_tree(
        b"40000 lib\x00" + bytes.fromhex(subtree), "t" * 40
    )[0][0] == "40000"


# --------------------------------------------------------------------------
# Symlink containment — lexical, never the filesystem
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,target",
    [
        ("links/in", "../shared/input.txt"),
        ("a/b/c", "../../a/b/c"),
        ("top", "sub/file"),
        ("cycle", "cycle"),
        ("dangling", "does/not/exist"),
        ("deep/x", "./../deep/y"),
    ],
)
def test_contained_symlink_targets_are_accepted(path: str, target: str) -> None:
    """A contained link may dangle or form a cycle: assay materializes the
    committed link, it does not promise the target resolves."""
    isolation._check_symlink_target(PurePosixPath(path), target)


@pytest.mark.parametrize(
    "path,target",
    [
        ("links/in", "/etc/passwd"),
        ("links/in", "../../outside"),
        ("top", ".."),
        ("a/b", "../../../etc/shadow"),
        ("x", ""),
    ],
)
def test_absolute_empty_or_escaping_symlink_targets_are_refused(
    path: str, target: str
) -> None:
    with pytest.raises(AssayError) as caught:
        isolation._check_symlink_target(PurePosixPath(path), target)
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


# --------------------------------------------------------------------------
# Repository-root project, zero-byte blob, exact bytes and modes
# --------------------------------------------------------------------------


def test_repository_root_project_materializes_every_literal_exactly(
    tmp_path: Path,
) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            # project_prefix '.' means the project root IS the snapshot root.
            assert snapshot.project_root == snapshot.root
            for name, expected in ROOT_MANIFEST["entries"].items():
                target = snapshot.root / name
                if expected["mode"] == "120000":
                    assert target.is_symlink()
                    raw = os.fsencode(os.readlink(target))
                    assert os.readlink(target) == expected["target"]
                else:
                    assert target.is_file() and not target.is_symlink()
                    raw = target.read_bytes()
                    assert stat.S_IMODE(target.stat().st_mode) == int(
                        expected["mode"][-3:], 8
                    )
                assert len(raw) == expected["size"]
                assert hashlib.sha256(raw).hexdigest() == expected["sha256"]
            # the zero-byte blob is a real, present, empty regular file
            assert (snapshot.root / "empty.txt").read_bytes() == b""
    assert list(scratch.iterdir()) == []


def test_read_regular_file_refuses_a_symlink_and_an_absent_path(
    tmp_path: Path,
) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        assert (
            prepared.read_regular_file(PurePosixPath("lib/data.bin"), timeout=TIMEOUT)
            == LITERALS["lib/data.bin"]
        )
        for path in (PurePosixPath("link-to-data"), PurePosixPath("nope.txt")):
            with pytest.raises(AssayError) as caught:
                prepared.read_regular_file(path, timeout=TIMEOUT)
            assert caught.value.reason_code is ReasonCode.GIT_FAILED


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def test_every_method_refuses_after_the_outer_context_closes(tmp_path: Path) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        pass
    with pytest.raises(RuntimeError, match="closed"):
        prepared.read_regular_file(PurePosixPath("empty.txt"), timeout=TIMEOUT)
    with pytest.raises(RuntimeError, match="closed"):
        with prepared.materialize(timeout=TIMEOUT):
            pytest.fail("a closed repository must not materialize")
    assert list(scratch.iterdir()) == []


def test_closing_the_outer_context_with_a_live_child_is_programmer_misuse(
    tmp_path: Path,
) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with pytest.raises(RuntimeError, match="live snapshot"):
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
            child = prepared.materialize(timeout=TIMEOUT)
            child.__enter__()  # deliberately never closed
    assert list(scratch.iterdir()) == []


def test_an_injected_budget_expiry_is_a_lane_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Budget expiry is proven by injection, never by racing a real clock.

    Patching the deadline type in the namespace that OWNS it keeps the test
    deterministic on any machine: no sleep, no elapsed-time assertion, and
    nothing that could flip verdict on a slow host (AUTHORING 3b.A).
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)

    class _Expired(git_module._P22Deadline):
        def remaining(self, what: str) -> float:
            raise git_module._p22_timeout(what)

    monkeypatch.setattr(git_module, "_P22Deadline", _Expired)
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("an expired budget must not yield a seed")
    assert caught.value.outcome is Outcome.BUDGET_EXCEEDED
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert list(scratch.iterdir()) == []


# --------------------------------------------------------------------------
# Replacement
# --------------------------------------------------------------------------


def test_replacement_identity_is_stable_across_independent_preparations(
    tmp_path: Path,
) -> None:
    """The same base/path/bytes must give the same child OID even when the
    two children come from two SEPARATELY prepared seeds.

    Reusing one prepared repository (as the locked suite does) cannot
    distinguish "deterministic" from "cached", and a cached child would let a
    later ambient-identity regression go unseen.
    """
    repo, commit = _root_repo(tmp_path)
    children = []
    for index in range(2):
        scratch = tmp_path / f"scratch-{index}"
        scratch.mkdir()
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
            with prepared.materialize_replacement(
                path=PurePosixPath("lib/data.bin"),
                expected=LITERALS["lib/data.bin"],
                replacement=b"replaced\n",
                timeout=TIMEOUT,
            ) as changed:
                children.append(changed.commit)
                assert (changed.root / "lib/data.bin").read_bytes() == b"replaced\n"
        assert list(scratch.iterdir()) == []
    assert children[0] == children[1]
    assert children[0] != commit


def test_replacing_an_executable_preserves_its_mode_in_the_child(
    tmp_path: Path,
) -> None:
    """``update-index --cacheinfo`` is given the entry's OWN mode.

    Hardcoding ``100644`` would silently drop the executable bit, which no
    byte-content assertion can see.
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize_replacement(
            path=PurePosixPath("bin/tool.sh"),
            expected=LITERALS["bin/tool.sh"],
            replacement=b"#!/bin/sh\necho replaced\n",
            timeout=TIMEOUT,
        ) as changed:
            target = changed.root / "bin" / "tool.sh"
            assert target.read_bytes() == b"#!/bin/sh\necho replaced\n"
            assert stat.S_IMODE(target.stat().st_mode) == 0o755
            listing = _git(changed.root, "ls-tree", changed.commit, "bin/tool.sh")
            assert listing.split()[0] == "100755"


def test_a_replacement_naming_a_symlink_is_a_stale_mutation_site(
    tmp_path: Path,
) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with pytest.raises(AssayError) as caught:
            with prepared.materialize_replacement(
                path=PurePosixPath("link-to-data"),
                expected=b"lib/data.bin",
                replacement=b"anything\n",
                timeout=TIMEOUT,
            ):
                pytest.fail("a symlink is not a regular mutation site")
        assert caught.value.outcome is Outcome.ERROR
        assert caught.value.reason_code is ReasonCode.MUTATION_DISCOVERY_FAILED
    assert list(scratch.iterdir()) == []


# --------------------------------------------------------------------------
# Constructor and call grammar
# --------------------------------------------------------------------------


def test_the_prepared_repository_exposes_its_own_spec(tmp_path: Path) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    spec = _spec(repo, scratch, commit)
    with prepare_snapshot(spec, timeout=TIMEOUT) as prepared:
        assert prepared.spec is spec
        assert prepared.spec.commit == commit


def test_limits_reject_incoherent_and_non_integer_bounds() -> None:
    from assay.isolation import SnapshotLimits as L

    base = {f: getattr(DEFAULT_SNAPSHOT_LIMITS, f) for f in DEFAULT_SNAPSHOT_LIMITS.__dataclass_fields__}
    for bad in (True, 0, -1, 1.5, "8"):
        with pytest.raises(ValueError, match="max_entries"):
            L(**{**base, "max_entries": bad})
    with pytest.raises(ValueError, match="max_blob_bytes cannot exceed"):
        L(**{**base, "max_blob_bytes": 2048, "max_total_object_bytes": 1024})


def test_spec_rejects_roots_and_prefixes_a_caller_would_otherwise_invent(
    tmp_path: Path,
) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with pytest.raises(ValueError, match="limits"):
        _spec(repo, scratch, commit, limits=object())
    with pytest.raises(ValueError, match="repo_top"):
        _spec(repo, scratch, commit, repo_top=Path("relative/path"))
    with pytest.raises(ValueError, match="scratch_root"):
        _spec(repo, scratch, commit, scratch_root=tmp_path / "absent")
    inside = repo / "inner-scratch"
    inside.mkdir()
    with pytest.raises(ValueError, match="outside repo_top"):
        _spec(repo, scratch, commit, scratch_root=inside)
    link = tmp_path / "scratch-link"
    link.symlink_to(scratch)
    with pytest.raises(ValueError, match="scratch_root"):
        _spec(repo, scratch, commit, scratch_root=link)
    with pytest.raises(ValueError, match="project_prefix"):
        _spec(repo, scratch, commit, project_prefix="apps/p")


def test_repo_top_must_be_the_resolved_repository_top(tmp_path: Path) -> None:
    """A subdirectory resolves to the same repository but is not its top.

    Accepting it would make every repo-top-relative manifest key silently
    mean something else.
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with pytest.raises(ValueError, match="resolved repository top"):
        with prepare_snapshot(
            _spec(repo, scratch, commit, repo_top=(repo / "lib").resolve()),
            timeout=TIMEOUT,
        ):
            pytest.fail("a subdirectory is not the repository top")


def test_replacement_arguments_must_be_whole_blob_bytes(tmp_path: Path) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with pytest.raises(ValueError, match="whole blob bytes"):
            prepared.materialize_replacement(
                path=PurePosixPath("lib/data.bin"),
                expected="not bytes",  # type: ignore[arg-type]
                replacement=b"x",
                timeout=TIMEOUT,
            )
        with pytest.raises(ValueError, match="normalized repo-relative"):
            prepared.read_regular_file(PurePosixPath("/abs/path"), timeout=TIMEOUT)


def test_an_absent_replacement_path_is_a_stale_mutation_site(tmp_path: Path) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with pytest.raises(AssayError) as caught:
            with prepared.materialize_replacement(
                path=PurePosixPath("never/existed.py"),
                expected=b"x",
                replacement=b"y",
                timeout=TIMEOUT,
            ):
                pytest.fail("an absent path must not yield")
        assert caught.value.reason_code is ReasonCode.MUTATION_DISCOVERY_FAILED


# --------------------------------------------------------------------------
# Bounds as a pure decision
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metadata,changes",
    [
        ({"a" * 40: ("blob", 10), "b" * 40: ("blob", 10)}, {"max_objects": 1}),
        ({"a" * 40: ("blob", 10)}, {"max_blob_bytes": 9}),
        (
            {"a" * 40: ("blob", 10), "b" * 40: ("blob", 10)},
            {"max_blob_bytes": 10, "max_total_object_bytes": 19},
        ),
    ],
    ids=["object-count", "per-blob", "total-object"],
)
def test_object_limits_refuse_the_first_overrun(
    metadata: dict, changes: dict
) -> None:
    limits_values = {
        f: getattr(DEFAULT_SNAPSHOT_LIMITS, f)
        for f in DEFAULT_SNAPSHOT_LIMITS.__dataclass_fields__
    }
    limits_values.update({k: max(v, 1) for k, v in changes.items()})
    from assay.isolation import SnapshotLimits as L

    with pytest.raises(AssayError) as caught:
        isolation._enforce_object_limits(metadata, L(**limits_values))
    assert caught.value.outcome is Outcome.BUDGET_EXCEEDED
    assert caught.value.reason_code is ReasonCode.SNAPSHOT_LIMIT_EXCEEDED


# --------------------------------------------------------------------------
# cat-file --batch grammar
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stream,expected",
    [
        (b"deadbeef missing\n", "missing or the batch grammar"),
        (b"%s blob notanumber\n" % (b"a" * 40), "non-integer size"),
        (b"%s blob 40\nshort" % (b"a" * 40), "ended mid-object"),
    ],
    ids=["missing-object", "non-integer-size", "truncated-content"],
)
def test_batch_reader_refuses_malformed_streams(stream: bytes, expected: str) -> None:
    reader = isolation._BatchReader(lambda _o, _k, _s: None)
    with pytest.raises(AssayError, match=expected):
        reader.feed(stream)
        reader.finish(1)


def test_batch_reader_refuses_a_short_object_count() -> None:
    reader = isolation._BatchReader(lambda _o, _k, _s: None)
    reader.feed(b"%s blob 0\n\n" % (b"a" * 40))
    with pytest.raises(AssayError, match="returned 1 objects, expected 2"):
        reader.finish(2)


def test_a_non_ascii_tree_mode_is_refused() -> None:
    body = b"\xff\xfe dir\x00" + bytes.fromhex(TREE_GRAMMAR["oid20"])
    with pytest.raises(AssayError, match="non-ASCII mode"):
        isolation._parse_tree(body, "t" * 40)


# --------------------------------------------------------------------------
# Crafted committed objects: names, collisions, and declared types
# --------------------------------------------------------------------------


def _commit_tree(repo: Path, tree: str, message: str) -> str:
    return _write_object(
        repo,
        "commit",
        (
            f"tree {tree}\n"
            "author Assay Fixture <fixture@assay.invalid> 946684800 +0000\n"
            "committer Assay Fixture <fixture@assay.invalid> 946684800 +0000\n\n"
            f"{message}\n"
        ).encode("utf-8"),
    )


def _blob(repo: Path, data: bytes) -> str:
    return _write_object(repo, "blob", data)


@pytest.mark.parametrize(
    "name,expected",
    [
        (b".git", "'.git' component"),
        (b"..", "the name '..'"),
        (b".", "the name '.'"),
        (b"\xff\xfe", "non-UTF-8 name"),
    ],
    ids=["dot-git", "dotdot", "dot", "non-utf8"],
)
def test_hostile_tree_entry_names_are_refused(
    tmp_path: Path, name: bytes, expected: str
) -> None:
    repo, _base = _root_repo(tmp_path)
    blob = _blob(repo, b"x\n")
    tree = _write_object(repo, "tree", b"100644 " + name + b"\x00" + bytes.fromhex(blob))
    commit = _commit_tree(repo, tree, "hostile name")
    scratch = _scratch(tmp_path)
    with pytest.raises(AssayError, match=expected) as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("a hostile entry name must not be yielded")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert list(scratch.iterdir()) == []


def test_a_duplicate_path_in_one_tree_is_refused(tmp_path: Path) -> None:
    repo, _base = _root_repo(tmp_path)
    blob = bytes.fromhex(_blob(repo, b"x\n"))
    tree = _write_object(
        repo, "tree", b"100644 dup\x00" + blob + b"100644 dup\x00" + blob
    )
    commit = _commit_tree(repo, tree, "duplicate path")
    scratch = _scratch(tmp_path)
    with pytest.raises(AssayError, match="duplicate or colliding path") as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("a duplicate path must not be yielded")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_a_tree_entry_whose_object_is_a_blob_is_refused(tmp_path: Path) -> None:
    """The declared type must agree with the inventory.

    A tree entry pointing at a blob would otherwise be traversed as a tree
    and parsed as tree bytes, turning arbitrary file content into a
    structure the materializer trusts.
    """
    repo, _base = _root_repo(tmp_path)
    blob = _blob(repo, b"not a tree\n")
    tree = _write_object(repo, "tree", b"40000 sub\x00" + bytes.fromhex(blob))
    commit = _commit_tree(repo, tree, "blob as tree")
    scratch = _scratch(tmp_path)
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("a blob declared as a tree must not be yielded")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_a_non_utf8_symlink_target_is_refused(tmp_path: Path) -> None:
    repo, _base = _root_repo(tmp_path)
    blob = _blob(repo, b"\xff\xfe")
    tree = _write_object(repo, "tree", b"120000 link\x00" + bytes.fromhex(blob))
    commit = _commit_tree(repo, tree, "non-utf8 link")
    scratch = _scratch(tmp_path)
    with pytest.raises(AssayError, match="non-UTF-8 target") as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("a non-UTF-8 symlink target must not be yielded")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_a_project_prefix_naming_no_tree_is_refused(tmp_path: Path) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with pytest.raises(AssayError, match="does not name a tree") as caught:
        with prepare_snapshot(
            _spec(repo, scratch, commit, project_prefix=PurePosixPath("nope/here")),
            timeout=TIMEOUT,
        ):
            pytest.fail("an absent project prefix must not be yielded")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_an_object_that_merely_peels_to_a_commit_is_not_a_snapshot_identity(
    tmp_path: Path,
) -> None:
    """``SnapshotSpec`` already forces 40 hex characters, so the remaining
    attack is a 40-hex object that RESOLVES to a different commit.

    An annotated tag is exactly that: ``<tag-oid>^{commit}`` peels happily to
    the commit, so without the literal round-trip check the recorded snapshot
    identity would silently differ from the one the caller named.
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    _git(repo, "tag", "-a", "-m", "annotated", "v1", commit)
    tag_oid = _git(repo, "rev-parse", "v1")
    assert tag_oid != commit
    with pytest.raises(AssayError, match="only a literal full commit") as caught:
        with prepare_snapshot(_spec(repo, scratch, tag_oid), timeout=TIMEOUT):
            pytest.fail("a tag OID is not a commit identity")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED

    tree = _git(repo, "rev-parse", f"{commit}^{{tree}}")
    with pytest.raises(AssayError) as peeled:
        with prepare_snapshot(_spec(repo, scratch, tree), timeout=TIMEOUT):
            pytest.fail("a tree OID is not a commit identity")
    assert peeled.value.reason_code is ReasonCode.GIT_FAILED
    assert list(scratch.iterdir()) == []


# --------------------------------------------------------------------------
# Source topology
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hazard",
    ["alternates-symlink", "alternates-fifo", "grafts", "sha256", "promisor"],
)
def test_further_external_or_incomplete_topologies_are_refused(
    tmp_path: Path, hazard: str
) -> None:
    """Axes the locked suite does not cover.

    The locked suite proves a regular non-empty alternates file, a
    partial-clone extension, and a shallow file. These are the remaining
    spellings of the same hazard, each of which a convenient
    ``if alternates.exists()`` or ``read_text()`` implementation would miss.
    """
    repo, commit = _root_repo(tmp_path)
    info = repo / ".git" / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    if hazard == "alternates-symlink":
        (tmp_path / "elsewhere").write_text("/objects\n", encoding="utf-8")
        (info / "alternates").symlink_to(tmp_path / "elsewhere")
    elif hazard == "alternates-fifo":
        os.mkfifo(info / "alternates")
    elif hazard == "grafts":
        (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (repo / ".git" / "info" / "grafts").write_text(f"{commit}\n", encoding="ascii")
    elif hazard == "sha256":
        repo = tmp_path / "sha256-repo"
        repo.mkdir()
        _git(repo, "init", "-q", "--object-format=sha256")
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "sha256 base")
        commit = "0" * 40  # topology is refused before any commit resolution
    else:
        _git(repo, "config", "remote.origin.promisor", "true")

    scratch = _scratch(tmp_path)
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("an unsafe source topology must not be yielded")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert list(scratch.iterdir()) == []


def test_an_empty_alternates_file_is_not_an_external_store(tmp_path: Path) -> None:
    """The refusal is about CONTENT, not existence: git itself writes an
    empty alternates file in ordinary repositories, and refusing that would
    make a normal consumer unusable."""
    repo, commit = _root_repo(tmp_path)
    info = repo / ".git" / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "alternates").write_text("\n", encoding="utf-8")
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            assert snapshot.commit == commit


# --------------------------------------------------------------------------
# The private verification that runs before a snapshot is ever yielded
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("head", "private HEAD is"),
        ("dirty", "is not clean"),
        ("alternates", "alternate object store"),
        ("hooks", "carries hooks"),
        ("config", "references the source repository"),
    ],
)
def test_the_pre_yield_verification_catches_a_damaged_snapshot(
    tmp_path: Path, damage: str, expected: str
) -> None:
    """These guards are defence in depth, so they are driven directly.

    Without this the guards would be lines no test could reach, which is
    exactly the "checked nothing" shape this project exists to remove.
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root, git_dir = snapshot.root, snapshot.root / ".git"
            if damage == "head":
                (git_dir / "HEAD").write_text("0" * 40 + "\n", encoding="ascii")
            elif damage == "dirty":
                (root / "lib" / "data.bin").write_bytes(b"tampered\n")
            elif damage == "alternates":
                (git_dir / "objects" / "info").mkdir(parents=True, exist_ok=True)
                (git_dir / "objects" / "info" / "alternates").write_text(
                    "/elsewhere\n", encoding="utf-8"
                )
            elif damage == "hooks":
                (git_dir / "hooks").mkdir(exist_ok=True)
                (git_dir / "hooks" / "post-checkout").write_text("#!/bin/sh\n", encoding="utf-8")
            else:
                with (git_dir / "config").open("a", encoding="utf-8") as handle:
                    handle.write(f"\n[assay]\n\tsource = {repo}\n")

            def run(*args: str, stdin_bytes: bytes = b"", identity: bool = False) -> bytes:
                return git_module._p22_git(
                    prepared._source.git_executable,
                    git_dir=git_dir,
                    work_tree=root,
                    args=args,
                    deadline=git_module._P22Deadline(TIMEOUT),
                    cwd=root,
                    stdin_bytes=stdin_bytes,
                    identity=identity,
                )

            with pytest.raises(AssayError, match=expected) as caught:
                prepared._verify(root, git_dir, commit, run)
            assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_a_bounded_stdout_ceiling_refuses_an_oversized_child(tmp_path: Path) -> None:
    """P20's fail-closed output bound still applies to P22's new seam."""
    repo, commit = _root_repo(tmp_path)
    source = git_module._p22_open_source(repo.resolve(), git_module._P22Deadline(TIMEOUT))
    with pytest.raises(AssayError, match="more than 4 bytes"):
        git_module._p22_git(
            source.git_executable,
            git_dir=source.git_dir,
            work_tree=source.repo_top,
            args=("rev-list", "--objects", "--no-object-names", commit),
            deadline=git_module._P22Deadline(TIMEOUT),
            cwd=source.repo_top,
            max_stdout=4,
        )


def test_a_failing_git_child_is_a_typed_terminal(tmp_path: Path) -> None:
    repo, _commit = _root_repo(tmp_path)
    source = git_module._p22_open_source(repo.resolve(), git_module._P22Deadline(TIMEOUT))
    with pytest.raises(AssayError) as caught:
        git_module._p22_git(
            source.git_executable,
            git_dir=source.git_dir,
            work_tree=source.repo_top,
            args=("rev-parse", "--verify", "--end-of-options", "refs/heads/absent"),
            deadline=git_module._P22Deadline(TIMEOUT),
            cwd=source.repo_top,
        )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


@pytest.mark.parametrize(
    "stdout,expected",
    [
        (b"not-an-object-name\n", "not a full object name"),
        (b"", "reachable closure of .* is empty"),
    ],
    ids=["non-oid-line", "empty-closure"],
)
def test_a_lying_rev_list_is_refused_rather_than_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: bytes, expected: str
) -> None:
    """Assay validates git's answer instead of assuming its grammar.

    Injected at the module boundary that OWNS the child process, so the real
    parsing path is exercised rather than a stubbed copy of it.
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    real = git_module._p22_git

    def fake(*args: object, **kwargs: object) -> bytes:
        if tuple(kwargs.get("args", ()))[:1] == ("rev-list",):
            return stdout
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_module, "_p22_git", fake)
    with pytest.raises(AssayError, match=expected) as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("an unusable object inventory must not yield a seed")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize(
    "stdout,expected",
    [
        (b"%s blob\n" % (b"a" * 40), "missing or corrupt in the source"),
        (b"%s blob 3\n" % (b"a" * 40), "were not reported by git"),
    ],
    ids=["short-record", "unreported-object"],
)
def test_a_lying_batch_check_is_refused_rather_than_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: bytes, expected: str
) -> None:
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    real = git_module._p22_git

    def fake(*args: object, **kwargs: object) -> bytes:
        call = tuple(kwargs.get("args", ()))
        if call[:1] == ("cat-file",) and call[1].startswith("--batch-check"):
            return stdout
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_module, "_p22_git", fake)
    with pytest.raises(AssayError, match=expected) as caught:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT):
            pytest.fail("an unusable object inventory must not yield a seed")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert list(scratch.iterdir()) == []


def test_ambient_git_environment_never_crosses_the_p22_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P22's new child-process seam must keep P20's closed environment.

    This exists because a controlled break proved the locked suite could NOT
    see it: merging ``os.environ`` under the fixed identity still produced
    the right child OID, since the fixed identity is applied last and covers
    all six author/committer variables. Identity is therefore not the
    property that closure protects -- object location, index location and
    replace-ref base are. A leaked ``GIT_DIR``/``GIT_INDEX_FILE``/
    ``GIT_ALTERNATE_OBJECT_DIRECTORIES`` would silently redirect exactly the
    plumbing P22 added, so it is asserted here directly.
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _git(decoy, "init", "-q")
    for name, value in {
        "GIT_DIR": str(decoy / ".git"),
        "GIT_WORK_TREE": str(decoy),
        "GIT_INDEX_FILE": str(decoy / "hostile.index"),
        "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(decoy / ".git" / "objects"),
        "GIT_REPLACE_REF_BASE": "refs/hostile",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(decoy),
    }.items():
        monkeypatch.setenv(name, value)

    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            assert snapshot.commit == commit
            assert (snapshot.root / "lib" / "data.bin").read_bytes() == LITERALS["lib/data.bin"]
            assert _git(snapshot.root, "rev-parse", "HEAD") == commit
            assert _git(snapshot.root, "status", "--porcelain=v1") == ""
            assert not (decoy / "hostile.index").exists()
    assert list(scratch.iterdir()) == []


def test_a_replacement_never_writes_to_the_prepared_seed(tmp_path: Path) -> None:
    """A later base snapshot must not contain an earlier mutant.

    "Update the seed, then copy it" is the cheapest wrong implementation and
    passes any test that only ever looks at the child.
    """
    repo, commit = _root_repo(tmp_path)
    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize_replacement(
            path=PurePosixPath("lib/data.bin"),
            expected=LITERALS["lib/data.bin"],
            replacement=b"mutant\n",
            timeout=TIMEOUT,
        ) as changed:
            assert (changed.root / "lib/data.bin").read_bytes() == b"mutant\n"
        with prepared.materialize(timeout=TIMEOUT) as after:
            assert (after.root / "lib/data.bin").read_bytes() == LITERALS["lib/data.bin"]
            assert after.commit == commit
        assert (
            prepared.read_regular_file(PurePosixPath("lib/data.bin"), timeout=TIMEOUT)
            == LITERALS["lib/data.bin"]
        )


# --------------------------------------------------------------------------
# Independent review (blind phase 1) -- combined-axis attacks
#
# Written against the handoff's own normative text before any implementation
# narrative was read. Every expected value below is authored from the literal
# committed bytes, never produced by calling :mod:`assay.isolation`.
#
# The locked matrix varies its axes one fixture at a time: a NORMAL repository
# for source-disconnect, a CLEAN repository for the linked worktree, a
# single-mode blob everywhere, and no two directories with identical content.
# A-134's standing consequence is that an oracle enumerating input shapes must
# also COMBINE them, so these do.
# --------------------------------------------------------------------------


#: The literal committed bytes of the combined-axis fixture, repo-top-relative.
COMBINED: dict[str, bytes] = {
    "apps/p/main.py": b"import shared\n",
    "apps/p/a/__init__.py": b"",
    "apps/p/b/__init__.py": b"",
    "shared/input.txt": b"tracked sibling\n",
    "tools/run.sh": b"#!/bin/sh\nexit 0\n",
    "docs/run.sh.txt": b"#!/bin/sh\nexit 0\n",
    "od\N{LATIN SMALL LETTER E WITH ACUTE}\nline\\slash.txt": b"odd path\n",
}
#: ``apps/p/a`` and ``apps/p/b`` hold identical content, so git stores ONE tree
#: object at two paths at the same depth. ``tools/run.sh`` and
#: ``docs/run.sh.txt`` are ONE blob at two paths with DIFFERENT modes.
COMBINED_MODES: dict[str, int] = {
    "apps/p/main.py": 0o644,
    "apps/p/a/__init__.py": 0o644,
    "apps/p/b/__init__.py": 0o644,
    "shared/input.txt": 0o644,
    "tools/run.sh": 0o755,
    "docs/run.sh.txt": 0o644,
    "od\N{LATIN SMALL LETTER E WITH ACUTE}\nline\\slash.txt": 0o644,
}
#: Every directory the commit contains, including the intermediate ones that
#: are the direct parent of no file.
COMBINED_DIRECTORIES: tuple[str, ...] = (
    "apps",
    "apps/p",
    "apps/p/a",
    "apps/p/b",
    "docs",
    "links",
    "shared",
    "tools",
)
FIXED_MTIME = 946684800


def _combined_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    """A repository combining every axis the locked fixtures vary separately.

    Returns ``(main_repository, linked_worktree, commit)``.
    """
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, data in COMBINED.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (repo / "tools" / "run.sh").chmod(0o755)
    (repo / "links").mkdir()
    os.symlink("../shared/input.txt", repo / "links" / "in")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "combined-axis base")
    commit = _git(repo, "rev-parse", "HEAD")

    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "--detach", str(linked), commit)
    assert (linked / ".git").is_file() and not (linked / ".git").is_symlink()
    return repo, linked, commit


def _assert_combined_worktree(root: Path) -> None:
    """Assert the exact committed content, from hand-authored literals."""
    for name, data in COMBINED.items():
        path = root / name
        assert path.is_file() and not path.is_symlink(), name
        assert path.read_bytes() == data, name
        assert stat.S_IMODE(path.stat().st_mode) == COMBINED_MODES[name], name
        assert int(path.stat().st_mtime) == FIXED_MTIME, name
    link = root / "links" / "in"
    assert link.is_symlink()
    assert os.readlink(link) == "../shared/input.txt"
    for name in COMBINED_DIRECTORIES:
        directory = root / name
        assert directory.is_dir() and not directory.is_symlink(), name
        assert stat.S_IMODE(directory.stat().st_mode) == 0o755, name
        assert int(directory.stat().st_mtime) == FIXED_MTIME, name


def test_reviewer_linked_worktree_survives_full_source_removal_with_hostile_tree(
    tmp_path: Path,
) -> None:
    """Five axes at once, none of which the locked matrix pairs.

    Source namespace (a linked-worktree gitfile, whose objects live in a
    common directory somewhere else) is combined with a source removed
    ENTIRELY after preparation -- not just the gitfile, the whole main
    repository, so the common object store is gone too -- and with a tree
    carrying identical sibling directories, one blob committed at two paths
    under two different modes, a UTF-8/newline/backslash path, a zero-byte
    blob, a contained symlink, and a nested project prefix. The replacement
    then names a tracked sibling OUTSIDE that prefix.

    Any one of these passes on its own against a convenient implementation.
    Together they are what a real monorepo actually looks like.
    """
    repo, linked, commit = _combined_repo(tmp_path)
    scratch = _scratch(tmp_path)
    spec = _spec(linked, scratch, commit, project_prefix=PurePosixPath("apps/p"))

    with prepare_snapshot(spec, timeout=TIMEOUT) as prepared:
        sibling = prepared.read_regular_file(
            PurePosixPath("shared/input.txt"), timeout=TIMEOUT
        )
        assert sibling == COMBINED["shared/input.txt"]

        # The ENTIRE source disappears: the linked worktree's gitfile target,
        # the common object store, and every source path are gone. Anything
        # the seed does not already own cannot be recovered from here.
        shutil.rmtree(repo)
        assert not repo.exists()

        with prepared.materialize(timeout=TIMEOUT) as base:
            assert base.commit == commit
            assert base.project_root == base.root / "apps" / "p"
            _assert_combined_worktree(base.root)
            assert _git(base.root, "rev-parse", "HEAD") == commit
            assert _git(base.root, "status", "--porcelain=v1") == ""

            with prepared.materialize_replacement(
                path=PurePosixPath("shared/input.txt"),
                expected=COMBINED["shared/input.txt"],
                replacement=b"mutated sibling\n",
                timeout=TIMEOUT,
            ) as child:
                assert child.commit != commit
                assert (
                    child.root / "shared/input.txt"
                ).read_bytes() == b"mutated sibling\n"
                assert _git(child.root, "status", "--porcelain=v1") == ""
                assert (
                    _git(child.root, "rev-list", "--parents", "-n", "1", child.commit)
                    == f"{child.commit} {commit}"
                )
                # the concurrently-held base snapshot is untouched
                assert (
                    base.root / "shared/input.txt"
                ).read_bytes() == COMBINED["shared/input.txt"]
                # and each path sharing one blob still carries its own mode
                assert (
                    stat.S_IMODE((child.root / "tools/run.sh").stat().st_mode) == 0o755
                )
                assert (
                    stat.S_IMODE((child.root / "docs/run.sh.txt").stat().st_mode)
                    == 0o644
                )
                deterministic = child.commit

        with prepared.materialize_replacement(
            path=PurePosixPath("shared/input.txt"),
            expected=COMBINED["shared/input.txt"],
            replacement=b"mutated sibling\n",
            timeout=TIMEOUT,
        ) as repeated:
            assert repeated.commit == deterministic

    assert list(scratch.iterdir()) == []


def test_reviewer_identical_sibling_directories_are_not_a_duplicate_path(
    tmp_path: Path,
) -> None:
    """Two directories with identical content are ONE tree object at two paths.

    ``cat-file --batch`` answers one record per requested name, so a walker
    that asks for the same tree twice in one level and accumulates the reply
    parses every record in it twice, then reports its own second copy as a
    "duplicate or colliding path" in the consumer's repository. Reproduced
    against the live vbpub tree, where ``ciu/nyxloom-trove/archive`` and
    ``shared-ramdisk-depot-manager/nyxloom-trove/archive`` are one object.
    """
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    for package in ("sub1", "sub2"):
        directory = repo / "src" / package
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_bytes(b"")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two identical packages")
    commit = _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", f"{commit}:src/sub1") == _git(
        repo, "rev-parse", f"{commit}:src/sub2"
    )

    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            for package in ("sub1", "sub2"):
                member = snapshot.root / "src" / package / "__init__.py"
                assert member.is_file()
                assert member.read_bytes() == b""
            assert _git(snapshot.root, "status", "--porcelain=v1") == ""


def test_reviewer_one_blob_at_two_paths_keeps_each_entrys_own_mode(
    tmp_path: Path,
) -> None:
    """Mode belongs to the tree ENTRY, not to the object it names.

    Deriving one mode per blob gives whichever entry is processed last the
    other's permission bits, which the pre-yield clean-status check then
    reports as a corrupt snapshot -- a refusal naming neither the file nor
    the real cause.
    """
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    body = b"#!/bin/sh\necho hi\n"
    (repo / "run.sh").write_bytes(body)
    (repo / "run.sh").chmod(0o755)
    (repo / "docs").mkdir()
    (repo / "docs" / "run.sh.txt").write_bytes(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "same blob, two modes")
    commit = _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", f"{commit}:run.sh") == _git(
        repo, "rev-parse", f"{commit}:docs/run.sh.txt"
    )

    scratch = _scratch(tmp_path)
    with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            assert (snapshot.root / "run.sh").read_bytes() == body
            assert (snapshot.root / "docs/run.sh.txt").read_bytes() == body
            assert stat.S_IMODE((snapshot.root / "run.sh").stat().st_mode) == 0o755
            assert (
                stat.S_IMODE((snapshot.root / "docs/run.sh.txt").stat().st_mode)
                == 0o644
            )
            assert _git(snapshot.root, "status", "--porcelain=v1") == ""


def test_reviewer_intermediate_directories_carry_no_ambient_umask_or_clock(
    tmp_path: Path,
) -> None:
    """Every materialized directory is fixed, not only a file's direct parent.

    A directory left at its creation defaults takes the ambient umask and the
    wall clock, so one commit materializes differently on two hosts -- the
    exact difference the fixed mtime exists to remove. The umask used here is
    an ordinary group-writable one, not a hostile value.
    """
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    deep = repo / "apps" / "p" / "pkg"
    deep.mkdir(parents=True)
    (deep / "main.py").write_bytes(b"x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "nested project")
    commit = _git(repo, "rev-parse", "HEAD")

    scratch = _scratch(tmp_path)
    previous = os.umask(0o002)
    try:
        spec = _spec(repo, scratch, commit, project_prefix=PurePosixPath("apps/p"))
        with prepare_snapshot(spec, timeout=TIMEOUT) as prepared:
            with prepared.materialize(timeout=TIMEOUT) as snapshot:
                for name in ("apps", "apps/p", "apps/p/pkg"):
                    directory = snapshot.root / name
                    assert stat.S_IMODE(directory.stat().st_mode) == 0o755, name
                    assert int(directory.stat().st_mtime) == FIXED_MTIME, name
                member = snapshot.root / "apps/p/pkg/main.py"
                assert stat.S_IMODE(member.stat().st_mode) == 0o644
                assert int(member.stat().st_mtime) == FIXED_MTIME
    finally:
        os.umask(previous)


def test_reviewer_materialization_holds_a_bounded_descriptor_count(
    tmp_path: Path,
) -> None:
    """Open descriptors are a resource no declared limit bounds.

    Opening one file per tracked path and holding them all for the whole
    batch makes the peak descriptor count the repository's file count, so an
    ordinary ``RLIMIT_NOFILE`` of 1024 turns a legitimate commit into a bare
    ``OSError`` that escapes the terminal table entirely. The ceiling is
    derived from this process's own current usage rather than hardcoded, so
    the test states the property instead of a machine's configuration.
    """
    if not Path("/proc/self/fd").is_dir():  # pragma: no cover - Linux gate only
        pytest.skip("descriptor accounting requires /proc/self/fd")
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = len(os.listdir("/proc/self/fd")) + 128
    if hard != resource.RLIM_INFINITY and hard < target:  # pragma: no cover
        pytest.skip("no headroom to constrain RLIMIT_NOFILE")
    file_count = target + 256

    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q")
    many = repo / "many"
    many.mkdir()
    for index in range(file_count):
        # Distinct bytes per file: identical content would collapse to one
        # object and hide the very fan-out being bounded.
        (many / f"f{index:05d}.txt").write_bytes(b"content-%d\n" % index)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "many tracked files")
    commit = _git(repo, "rev-parse", "HEAD")
    scratch = _scratch(tmp_path)

    resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    try:
        with prepare_snapshot(_spec(repo, scratch, commit), timeout=TIMEOUT) as prepared:
            with prepared.materialize(timeout=TIMEOUT) as snapshot:
                materialized = sorted((snapshot.root / "many").iterdir())
                assert len(materialized) == file_count
                assert materialized[0].read_bytes() == b"content-0\n"
                assert materialized[-1].read_bytes() == (
                    b"content-%d\n" % (file_count - 1)
                )
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
