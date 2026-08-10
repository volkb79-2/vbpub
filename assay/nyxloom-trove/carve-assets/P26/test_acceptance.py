"""Independent locked acceptance for P26's attestation/deadline contract.

The suite owns expected evidence objects and complete v4 templates.  It uses
real Git for path semantics and synchronized child processes for deadline
semantics; no expected verdict is produced by Assay's serializer.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import select
import subprocess
import sys
import threading
import tomllib
from datetime import datetime
from pathlib import Path

import pytest

from assay import (
    __version__,
    attestation,
    cli,
    config,
    git,
    measurability,
    runner,
    safeio,
)
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode
from assay.verdict import Claim, EvidenceDeclaration
from assay.verify import verify_document

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("ASSAY_P26_PROJECT_ROOT", HERE.parents[2])).resolve()
CONTRACT = json.loads((HERE / "interface-contract.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((HERE / "fixture-manifest.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "P26 acceptance",
        "GIT_AUTHOR_EMAIL": "p26@assay.invalid",
        "GIT_COMMITTER_NAME": "P26 acceptance",
        "GIT_COMMITTER_EMAIL": "p26@assay.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }


def _git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _record(path: Path, *, commit: str, reviewed: list[str], extra: dict | None = None) -> None:
    payload = {
        "producer": "human:alice",
        "attested_commit": commit,
        "reviewed_paths": reviewed,
    }
    if extra:
        payload.update(extra)
    _write(path, json.dumps(payload, sort_keys=True))


def _remaining() -> float:
    return 60.0


def _fixture_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / ".gitignore", ".assay/\nverdict.json\n")
    _write(repo / "src/reviewed/child.py", "old\n")
    _write(repo / "src/unrelated.py", "same\n")
    _write(repo / "literal/*?[x]\n.py", "old\n")
    base = _commit(repo, "base")
    _write(repo / "src/reviewed/child.py", "new\n")
    _write(repo / "literal/*?[x]\n.py", "new\n")
    head = _commit(repo, "head")
    return repo, base, head


def _load(repo: Path, head: str, *keys: str):
    return attestation.load_attested_evidence(
        repo,
        head=head,
        declared=tuple(
            EvidenceDeclaration(source="attested", key=key) for key in keys
        ),
        project_root=repo,
        attestation_dir=".assay/attestations",
        remaining=_remaining,
    )


def test_locked_asset_hashes_and_input_revision() -> None:
    assert CONTRACT["input_revision"] == "233926cedd26a6e34512806e267b7141377913b2"
    assert MANIFEST["schema_version"] == 1
    for relative, digest in MANIFEST["files"].items():
        assert _sha256(HERE / relative) == digest, relative


def test_public_contract_symbols_and_bounds_are_exact() -> None:
    assert hasattr(config, "EvidenceConfig")
    assert hasattr(safeio, "read_bounded_input")
    for name in (
        "verify_exact_commit",
        "is_ancestor",
        "tree_entry_kind",
        "path_is_current",
    ):
        assert hasattr(git, name), name
        assert "remaining" in inspect.signature(getattr(git, name)).parameters
    assert "remaining" in inspect.signature(git.run).parameters
    for name, value in CONTRACT["constants"].items():
        assert getattr(attestation, name) == value


def test_runner_binds_evidence_batch_to_lane_source_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "assay.toml"
    path.write_text(
        _lane_document(
            '''[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [{source="attested",key="review"}]'''
        ),
        encoding="utf-8",
    )
    lane = config.load_lane_file(path).lane("attested")

    def forbidden(*args, **kwargs):
        raise AssertionError("runner started work before binding declared evidence")

    for name in ("head_rev", "repo_top", "dirty_paths"):
        monkeypatch.setattr(git, name, forbidden)

    plan = runner.resolve_command_plan(lane)
    result = runner.CommandResult(
        plan=plan,
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
        returncode=None,
        started="2000-01-01T00:00:00+00:00",
        ended="2000-01-01T00:00:00+00:00",
    )
    claims = (
        Claim(
            rigor="R0",
            source="computed",
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True,
            reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
        ),
    )

    calls = (
        lambda: runner.run_lane(
            lane,
            commit="a" * 40,
            repo=tmp_path,
            project_root=tmp_path,
            adapter=None,
            assay_version="locked",
            evidence=(),
            declared_evidence=(),
        ),
        lambda: runner.refuse_lane(
            lane,
            commit="a" * 40,
            status=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
            assay_version="locked",
            evidence=(),
            declared_evidence=(),
        ),
        lambda: runner.assemble_verdict(
            lane=lane,
            commit="a" * 40,
            result=result,
            claims=claims,
            assay_version="locked",
            evidence=(),
            declared_evidence=(),
        ),
    )
    for call in calls:
        with pytest.raises(AssayError) as raised:
            call()
        assert (raised.value.outcome, raised.value.reason_code) == (
            Outcome.ERROR,
            ReasonCode.BAD_LANE_CONFIG,
        )


def _lane_document(judge: str) -> str:
    return f'''schema_version = 1

[lanes.attested]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/true"]
env = {{}}
env_passthrough = []
budget = "1m"
allow_argv_append = false

{judge}
'''


def test_r0_attestation_config_round_trips_without_inventing_a_judge(tmp_path: Path) -> None:
    text = _lane_document(
        '''[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [
  {source = "attested", key = "security-review"},
  {source = "attested", key = "api-review.v2"},
]'''
    )
    path = tmp_path / "assay.toml"
    path.write_text(text, encoding="utf-8")
    loaded = config.load_lane_file(path).lane("attested")
    assert loaded.as_declared() == tomllib.loads(text)["lanes"]["attested"]
    assert loaded.judge.attestation_dir == ".assay/attestations"
    assert tuple((item.source, item.key) for item in loaded.judge.evidence) == (
        ("attested", "security-review"),
        ("attested", "api-review.v2"),
    )


@pytest.mark.parametrize(
    "judge",
    [
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"''',
        '''[lanes.attested.judge]\nevidence = [{source="attested",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = []''',
        '''[lanes.attested.judge]\nattestation_dir = "../outside"\nevidence = [{source="attested",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="adjudicated",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="attested",key="../review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="attested",key="review"},{source="attested",key="review"}]''',
        '''[lanes.attested.judge]\nattestation_dir = ".assay/attestations"\nevidence = [{source="attested",key="review",extra=true}]''',
    ],
)
def test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape(
    tmp_path: Path, judge: str
) -> None:
    path = tmp_path / "assay.toml"
    path.write_text(_lane_document(judge), encoding="utf-8")
    with pytest.raises(LaneConfigError):
        config.load_lane_file(path)


@pytest.mark.parametrize(
    "mutation",
    [
        {"extra": 1},
        {"attested_commit": "A" * 40},
        {"attested_commit": "a" * 39},
        {"attested_commit": "HEAD"},
        {"producer": "x" * 257},
        {"producer": "\ud800"},
        {"reviewed_paths": ["../escape.py"]},
        {"reviewed_paths": ["src//double.py"]},
        {"reviewed_paths": ["/absolute.py"]},
        {"reviewed_paths": ["src/nul\u0000.py"]},
    ],
)
def test_record_grammar_is_closed_and_bounded_before_git(mutation: dict) -> None:
    payload = {
        "producer": "human:alice",
        "attested_commit": "a" * 40,
        "reviewed_paths": ["src/reviewed.py"],
    }
    payload.update(mutation)
    with pytest.raises(AssayError) as raised:
        attestation.parse_attestation(json.dumps(payload), source_name="locked")
    assert (raised.value.outcome, raised.value.reason_code) == (
        Outcome.ERROR,
        ReasonCode.UNREADABLE_ARTIFACT,
    )


def test_record_grammar_rejects_duplicate_json_members_before_git() -> None:
    duplicate = (
        '{"producer":"human:alice","producer":"human:bob",'
        '"attested_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"reviewed_paths":["src/reviewed.py"]}'
    )
    with pytest.raises(AssayError) as raised:
        attestation.parse_attestation(duplicate, source_name="locked")
    assert (raised.value.outcome, raised.value.reason_code) == (
        Outcome.ERROR,
        ReasonCode.UNREADABLE_ARTIFACT,
    )


def test_real_git_directory_literal_and_newline_currentness(tmp_path: Path) -> None:
    repo, base, head = _fixture_repo(tmp_path)
    root = repo / ".assay/attestations"
    _record(root / "directory.json", commit=base, reviewed=["src/reviewed"])
    _record(root / "unrelated.json", commit=base, reviewed=["src/unrelated.py"])
    _record(root / "hostile.json", commit=base, reviewed=["literal/*?[x]\n.py"])
    actual = tuple(item.to_dict() for item in _load(repo, head, "directory", "unrelated", "hostile"))
    assert actual == (
        {
            "source": "attested",
            "key": "directory",
            "status": "NO_MEASUREMENT",
            "verified_by_assay": False,
            "reason_code": "STALE_ATTESTATION",
            "producer": "human:alice",
            "attested_commit": base,
            "reviewed_paths": ["src/reviewed"],
        },
        {
            "source": "attested",
            "key": "unrelated",
            "status": "PASS",
            "verified_by_assay": False,
            "producer": "human:alice",
            "attested_commit": base,
            "reviewed_paths": ["src/unrelated.py"],
        },
        {
            "source": "attested",
            "key": "hostile",
            "status": "NO_MEASUREMENT",
            "verified_by_assay": False,
            "reason_code": "STALE_ATTESTATION",
            "producer": "human:alice",
            "attested_commit": base,
            "reviewed_paths": ["literal/*?[x]\n.py"],
        },
    )


def test_git_helpers_treat_metacharacters_as_identity_not_pathspec(tmp_path: Path) -> None:
    repo = tmp_path / "literal"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    exact = "literal/current*?[x]\n.py"
    decoy = "literal/current-abx\n.py"
    _write(repo / exact, "same\n")
    _write(repo / decoy, "old\n")
    base = _commit(repo, "base")
    _write(repo / decoy, "new\n")
    head = _commit(repo, "decoy-only")
    assert git.tree_entry_kind(repo, base, exact, remaining=_remaining) == "blob"
    assert git.path_is_current(repo, base, head, exact, remaining=_remaining)


def test_annotated_tag_object_cannot_peel_into_an_attested_commit(tmp_path: Path) -> None:
    repo, _, head = _fixture_repo(tmp_path)
    _git(repo, "tag", "-a", "review-tag", "-m", "not a commit object")
    tag_oid = _git(repo, "rev-parse", "review-tag")
    assert tag_oid != head
    root = repo / ".assay/attestations"
    _record(root / "tag-object.json", commit=tag_oid, reviewed=["src/unrelated.py"])
    actual = _load(repo, head, "tag-object")
    assert (actual[0].status, actual[0].reason_code) == (
        Outcome.ERROR,
        ReasonCode.UNREADABLE_ARTIFACT,
    )


def test_missing_later_path_outranks_an_earlier_stale_directory(tmp_path: Path) -> None:
    repo, base, head = _fixture_repo(tmp_path)
    root = repo / ".assay/attestations"
    _record(
        root / "stale-then-missing.json",
        commit=base,
        reviewed=["src/reviewed", "src/does-not-exist.py"],
    )
    actual = _load(repo, head, "stale-then-missing")
    assert (actual[0].status, actual[0].reason_code) == (
        Outcome.ERROR,
        ReasonCode.UNREADABLE_ARTIFACT,
    )


def test_missing_parent_symlink_malformed_and_later_good_are_independent(
    tmp_path: Path,
) -> None:
    repo, _, head = _fixture_repo(tmp_path)
    root = repo / ".assay/attestations"
    root.mkdir(parents=True)
    _write(root / "broken.json", "{not json")
    outside = tmp_path / "outside.json"
    _record(outside, commit=head, reviewed=["src/unrelated.py"])
    os.symlink(outside, root / "linked.json")
    _record(root / "good.json", commit=head, reviewed=["src/unrelated.py"])
    actual = _load(repo, head, "absent", "broken", "linked", "good")
    assert [(item.status, item.reason_code) for item in actual] == [
        (Outcome.NO_MEASUREMENT, ReasonCode.MISSING_ATTESTATION),
        (Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT),
        (Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT),
        (Outcome.PASS, None),
    ]


def test_all_missing_batch_still_observes_expiry_inside_atomic_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, head = _fixture_repo(tmp_path)
    expired = threading.Event()
    sentinel = AssayError(
        "locked missing-only deadline",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )

    def missing(*args, **kwargs):
        expired.set()

    def remaining() -> float:
        if expired.is_set():
            raise sentinel
        return 60.0

    def forbidden(*args, **kwargs):
        raise AssertionError("missing-only batch launched Git")

    monkeypatch.setattr(safeio, "read_bounded_input", missing, raising=False)
    for name in (
        "verify_exact_commit",
        "is_ancestor",
        "tree_entry_kind",
        "path_is_current",
    ):
        monkeypatch.setattr(git, name, forbidden, raising=False)
    with pytest.raises(AssayError) as raised:
        attestation.load_attested_evidence(
            repo,
            head=head,
            declared=(EvidenceDeclaration(source="attested", key="absent"),),
            project_root=repo,
            attestation_dir=".assay/attestations",
            remaining=remaining,
        )
    assert raised.value is sentinel


def test_loader_rejects_parent_key_or_directory_before_seeded_outside_read(
    tmp_path: Path,
) -> None:
    repo, _, head = _fixture_repo(tmp_path)
    _record(tmp_path / "outside.json", commit=head, reviewed=["src/unrelated.py"])
    calls = (
        lambda: _load(repo, head, "../outside"),
        lambda: attestation.load_attested_evidence(
            repo,
            head=head,
            declared=(EvidenceDeclaration(source="attested", key="outside"),),
            project_root=repo,
            attestation_dir="..",
            remaining=_remaining,
        ),
    )
    for call in calls:
        with pytest.raises(AssayError) as raised:
            call()
        assert raised.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_all_structural_and_aggregate_bounds_precede_every_git_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, head = _fixture_repo(tmp_path)
    root = repo / ".assay/attestations"
    root.mkdir(parents=True)
    (root / "oversize.json").write_bytes(b"x" * (1024 * 1024 + 1))
    for index in range(3):
        _record(
            root / f"many-{index}.json",
            commit=head,
            reviewed=[f"batch-{index}/path-{item}.py" for item in range(700)],
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("Git launched before the structural/aggregate preflight")

    for name in (
        "verify_exact_commit",
        "is_ancestor",
        "tree_entry_kind",
        "path_is_current",
    ):
        monkeypatch.setattr(git, name, forbidden, raising=False)
    oversize = _load(repo, head, "oversize")
    assert (oversize[0].status, oversize[0].reason_code) == (
        Outcome.ERROR,
        ReasonCode.UNREADABLE_ARTIFACT,
    )
    aggregate = _load(repo, head, "many-0", "many-1", "many-2")
    assert all(
        (item.status, item.reason_code)
        == (Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT)
        for item in aggregate
    )


def _substitute_template(path: Path, values: dict[str, str]) -> dict:
    def replace(value):
        if isinstance(value, str):
            return values.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    return replace(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("changed", "template", "expected_exit"),
    [
        (False, "current-v4-template.json", 0),
        (True, "stale-directory-v4-template.json", 3),
    ],
)
def test_cli_emits_the_complete_hand_authored_v4_artifact(
    tmp_path: Path, changed: bool, template: str, expected_exit: int
) -> None:
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    lane_text = _lane_document(
        '''[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [{source="attested",key="security-review"}]'''
    )
    _write(repo / "assay.toml", lane_text)
    _write(repo / ".gitignore", ".assay/\nverdict.json\n")
    _write(repo / "src/reviewed/child.py", "old\n")
    base = _commit(repo, "base")
    if changed:
        _write(repo / "src/reviewed/child.py", "new\n")
        head = _commit(repo, "head")
    else:
        head = base
    _record(
        repo / ".assay/attestations/security-review.json",
        commit=base,
        reviewed=["src/reviewed"],
    )
    destination = repo / "verdict.json"
    exit_code = cli.main(
        [
            "run",
            "attested",
            "--file",
            str(repo / "assay.toml"),
            "--verdict-json",
            str(destination),
        ]
    )
    assert exit_code == expected_exit
    actual = json.loads(destination.read_text(encoding="utf-8"))
    started = datetime.fromisoformat(actual["started"])
    ended = datetime.fromisoformat(actual["ended"])
    assert ended >= started
    expected = _substitute_template(
        HERE / "expected" / template,
        {
            "@ASSAY_VERSION@": __version__,
            "@HEAD_OID@": head,
            "@ATTESTED_OID@": base,
            "@STARTED@": actual["started"],
            "@ENDED@": actual["ended"],
        },
    )
    assert verify_document(expected) == []
    assert actual == expected


def test_cli_preserves_independent_malformed_missing_and_current_evidence(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    lane_text = _lane_document(
        '''[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [
  {source="attested",key="broken"},
  {source="attested",key="absent"},
  {source="attested",key="good"},
]'''
    )
    _write(repo / "assay.toml", lane_text)
    _write(repo / ".gitignore", ".assay/\nverdict.json\n")
    _write(repo / "src/reviewed/child.py", "old\n")
    head = _commit(repo, "fixture")
    root = repo / ".assay/attestations"
    _write(root / "broken.json", "{not json")
    _record(root / "good.json", commit=head, reviewed=["src/reviewed"])
    destination = repo / "verdict.json"
    exit_code = cli.main(
        [
            "run",
            "attested",
            "--file",
            str(repo / "assay.toml"),
            "--verdict-json",
            str(destination),
        ]
    )
    assert exit_code == 2
    actual = json.loads(destination.read_text(encoding="utf-8"))
    expected = _substitute_template(
        HERE / "expected/independent-errors-v4-template.json",
        {
            "@ASSAY_VERSION@": __version__,
            "@HEAD_OID@": head,
            "@STARTED@": actual["started"],
            "@ENDED@": actual["ended"],
        },
    )
    assert verify_document(expected) == []
    assert actual == expected


def test_attestation_timeout_is_atomic_and_does_not_run_a_failing_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    lane_text = _lane_document(
        '''[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [
  {source="attested",key="slow"},
  {source="attested",key="later"},
]'''
    ).replace('argv = ["/bin/true"]', 'argv = ["/bin/false"]')
    _write(repo / "assay.toml", lane_text)
    _write(repo / ".gitignore", ".assay/\nverdict.json\n")
    _write(repo / "src/reviewed/child.py", "old\n")
    head = _commit(repo, "fixture")
    root = repo / ".assay/attestations"
    _record(root / "slow.json", commit=head, reviewed=["src/reviewed"])
    _record(root / "later.json", commit=head, reviewed=["src/reviewed"])
    sentinel = AssayError(
        "locked attestation timeout",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )

    def expire(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(git, "verify_exact_commit", expire, raising=False)
    destination = repo / "verdict.json"
    exit_code = cli.main(
        [
            "run",
            "attested",
            "--file",
            str(repo / "assay.toml"),
            "--verdict-json",
            str(destination),
        ]
    )
    assert exit_code == 4
    actual = json.loads(destination.read_text(encoding="utf-8"))
    expected = _substitute_template(
        HERE / "expected/attestation-timeout-v4-template.json",
        {
            "@ASSAY_VERSION@": __version__,
            "@HEAD_OID@": head,
            "@STARTED@": actual["started"],
            "@ENDED@": actual["ended"],
        },
    )
    assert verify_document(expected) == []
    assert actual == expected


def test_generic_git_expiry_kills_the_complete_group_and_preserves_the_terminal(
    tmp_path: Path,
) -> None:
    if "remaining" not in inspect.signature(git._run_bounded).parameters:
        pytest.fail("generic Git boundary has no deadline seam")
    fifo = tmp_path / "ready.fifo"
    release = tmp_path / "release.fifo"
    os.mkfifo(fifo)
    os.mkfifo(release)
    expired = threading.Event()
    sentinel = AssayError(
        "locked deadline",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )
    helper = r'''
import json, os, sys, time
child = os.fork()
if child == 0:
    time.sleep(60)
    raise SystemExit(0)
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    stream.write(json.dumps({"descendant": child, "pgid": os.getpgrp()}))
with open(sys.argv[2], "r", encoding="ascii") as stream:
    stream.read()
os.write(1, b"ready")
raise SystemExit(0)
'''

    def remaining() -> float:
        if expired.is_set():
            raise sentinel
        return 60.0

    observed: list[BaseException | object] = []

    def invoke() -> None:
        try:
            git._run_bounded(
                [sys.executable, "-c", helper, str(fifo), str(release)],
                remaining=remaining,
            )
        except AssayError as exc:
            observed.append(exc)
        else:
            observed.append(object())

    worker = threading.Thread(target=invoke)
    worker.start()
    with fifo.open("r", encoding="utf-8") as stream:
        identity = json.loads(stream.read())
    pidfd = os.pidfd_open(identity["descendant"])
    expired.set()
    with release.open("w", encoding="ascii") as stream:
        stream.write("go")
    worker.join(timeout=60)
    assert not worker.is_alive(), "Git boundary did not observe the synchronized expiry"
    assert observed == [sentinel]
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    assert poller.poll(60_000), "forked descendant survived process-group expiry"
    os.close(pidfd)


def test_generic_git_overflow_also_kills_its_complete_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if "remaining" not in inspect.signature(git._run_bounded).parameters:
        pytest.fail("generic Git boundary has no process-group/deadline seam")
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", 4)
    fifo = tmp_path / "overflow.fifo"
    release = tmp_path / "overflow-release.fifo"
    os.mkfifo(fifo)
    os.mkfifo(release)
    helper = r'''
import json, os, sys, time
child = os.fork()
if child == 0:
    time.sleep(60)
    raise SystemExit(0)
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    stream.write(json.dumps({"descendant": child}))
with open(sys.argv[2], "r", encoding="ascii") as stream:
    stream.read()
os.write(1, b"12345")
raise SystemExit(0)
'''
    observed: list[AssayError | object] = []

    def invoke() -> None:
        try:
            git._run_bounded(
                [sys.executable, "-c", helper, str(fifo), str(release)],
                remaining=_remaining,
            )
        except AssayError as exc:
            observed.append(exc)
        else:
            observed.append(object())

    worker = threading.Thread(target=invoke)
    worker.start()
    with fifo.open("r", encoding="utf-8") as stream:
        identity = json.loads(stream.read())
    pidfd = os.pidfd_open(identity["descendant"])
    with release.open("w", encoding="ascii") as stream:
        stream.write("go")
    worker.join(timeout=60)
    assert not worker.is_alive(), "overflow killed only the direct child or hung"
    assert len(observed) == 1 and isinstance(observed[0], AssayError)
    assert (observed[0].outcome, observed[0].reason_code) == (
        Outcome.ERROR,
        ReasonCode.GIT_FAILED,
    )
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    assert poller.poll(60_000), "overflow left a forked descendant running"
    os.close(pidfd)


def test_bootstrap_timeout_launches_no_substantive_git_command(tmp_path: Path) -> None:
    if "remaining" not in inspect.signature(git.head_rev).parameters:
        pytest.fail("public Git wrapper has no deadline seam")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.jsonl"
    fifo = tmp_path / "bootstrap.fifo"
    release = tmp_path / "bootstrap-release.fifo"
    os.mkfifo(fifo)
    os.mkfifo(release)
    fake = bin_dir / "git"
    fake.write_text(
        f'''#!/usr/bin/python3
import json, os, sys, time
with open({str(log)!r}, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if "--absolute-git-dir" in sys.argv:
    with open({str(fifo)!r}, "w", encoding="ascii") as stream:
        stream.write("ready")
    with open({str(release)!r}, "r", encoding="ascii") as stream:
        stream.read()
    os.write(1, b"partial")
    time.sleep(60)
raise SystemExit(97)
''',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    expired = threading.Event()
    sentinel = AssayError(
        "bootstrap expired",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )

    def remaining() -> float:
        if expired.is_set():
            raise sentinel
        return 60.0

    outcome: list[BaseException | object] = []
    old_path = os.environ.get("PATH")
    os.environ["PATH"] = str(bin_dir)
    try:
        worker = threading.Thread(
            target=lambda: _capture_call(outcome, git.head_rev, repo, remaining=remaining)
        )
        worker.start()
        with fifo.open("r", encoding="ascii") as stream:
            assert stream.read() == "ready"
        expired.set()
        with release.open("w", encoding="ascii") as stream:
            stream.write("go")
        worker.join(timeout=60)
    finally:
        if old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old_path
    assert not worker.is_alive()
    assert outcome == [sentinel]
    launched = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(launched) == 1
    assert "--absolute-git-dir" in launched[0]
    assert "rev-parse" not in launched[0][launched[0].index("--absolute-git-dir") + 1 :]


def _capture_call(outcome: list, function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except AssayError as exc:
        outcome.append(exc)
    else:
        outcome.append(object())


def test_measurability_forwards_one_remaining_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def remaining() -> float:
        return 60.0

    observed = []

    def repo_top(repo, *, remaining=None):
        observed.append(remaining)
        return tmp_path

    def dirty_paths(repo, *, remaining=None):
        observed.append(remaining)
        return ()

    def head_rev(repo, *, remaining=None):
        observed.append(remaining)
        return "b" * 40

    def resolve_base(repo, base, *, remaining=None):
        observed.append(remaining)
        return "a" * 40

    monkeypatch.setattr(git, "repo_top", repo_top)
    monkeypatch.setattr(git, "dirty_paths", dirty_paths)
    monkeypatch.setattr(git, "head_rev", head_rev)
    monkeypatch.setattr(git, "resolve_base", resolve_base)
    measurability.check_dirty_tree(tmp_path, (tmp_path,), remaining=remaining)
    measurability.check_base_is_head(tmp_path, "main", remaining=remaining)
    assert observed and all(item == remaining for item in observed)


def test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "assay.toml"
    path.write_text(_lane_document(""), encoding="utf-8")
    lane = config.load_lane_file(path).lane("attested")
    head = "b" * 40
    deadline = runner.LaneDeadline(expires_at=100.0, monotonic=lambda: 83.0)
    forwarded = []

    def repo_top(repo, *, remaining=None):
        forwarded.append(remaining)
        return tmp_path

    def dirty_paths(repo, *, remaining=None):
        forwarded.append(remaining)
        return ()

    def head_rev(repo, *, remaining=None):
        forwarded.append(remaining)
        return head

    monkeypatch.setattr(git, "repo_top", repo_top)
    monkeypatch.setattr(git, "dirty_paths", dirty_paths)
    monkeypatch.setattr(git, "head_rev", head_rev)
    timeouts = []

    def process_runner(argv, *, env, cwd, timeout):
        timeouts.append(timeout)
        return subprocess.CompletedProcess(argv, 0, "", "")

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=tmp_path,
        project_root=tmp_path,
        adapter=None,
        assay_version="locked",
        process_runner=process_runner,
        deadline=deadline,
    )
    assert verdict.outcome is Outcome.PASS
    assert timeouts == [17.0]
    assert forwarded
    assert all(getattr(item, "__self__", None) is deadline for item in forwarded)


def test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it() -> None:
    source = (PROJECT_ROOT / "tools/tester-unified-gate.sh").read_text(encoding="utf-8")
    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]
    exact_installed_wheel_gate = '''PYTHONPATH= ASSAY_P26_PROJECT_ROOT="$worktree/assay" \\
    "$scratch/run-venv/bin/python" -m pytest \\
      "$worktree/assay/nyxloom-trove/carve-assets/P26/test_acceptance.py" \\
      -q -p no:randomly --override-ini=pythonpath=
  echo 'ASSAY_GATE_PHASE=attestation-hardened' '''.rstrip()
    assert exact_installed_wheel_gate in inner
    assert inner.index("ASSAY_GATE_PHASE=wheel-installed") < inner.index(
        "ASSAY_GATE_PHASE=attestation-hardened"
    )
    assert inner.index("ASSAY_GATE_PHASE=attestation-hardened") < inner.index(
        "run_self_hosted_lane"
    )
