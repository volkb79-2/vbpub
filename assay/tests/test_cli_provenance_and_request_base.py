"""B018/A-327 and B019/A-328 at the CLI boundary, through a REAL git
repository and :func:`assay.cli.main` -- the same discipline
``test_cli_run.py`` holds itself to.

Both features are about a REFUSAL more than a value, so the refusals are what
this module mostly asserts:

* B018 -- an invocation that cannot identify the build it is running records
  no identity at all (never a partial one), says so, and refuses outright
  when the caller demanded the binding. The POSITIVE half -- a real digest
  from a real installed wheel -- cannot be proven from a source checkout at
  all, and is proven where it can be: ``test_standalone.py`` against a built
  wheel, ``test_distribution_build_release.py`` against a built zipapp, and
  the registered gate against its own run-venv wheel.
* B019 -- a lane that delegated its comparison base and got none refuses; a
  lane that did not delegate and got one refuses too; and where the request
  DOES own it, the resolved commit reaches ``judgment.resolved.base`` exactly
  once, through the same merge-base contract ``judge.base`` always used.
"""

from __future__ import annotations

import io
import json
import types
from pathlib import Path

import pytest
from conftest import R0_LANE, GitRepo, set_key

from assay import provenance
from assay.cli import main
from assay.errors import LaneConfigError, Outcome, ReasonCode
from assay.verdict import JudgeProvenance


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _write_and_commit_lane(repo: GitRepo, text: str) -> Path:
    path = repo.write("assay.toml", text)
    repo.commit_all("add assay.toml")
    return path


# ===========================================================================
# B018 -- the judge identity
# ===========================================================================


class _FakeDistribution:
    """The narrow surface :func:`provenance.identify_judge` reads.

    Hand-written rather than mocked so every branch below is driven by data
    with an obvious shape. The three forms this stands in for were each
    MEASURED against a real interpreter before this module existed (A-327);
    these stand-ins exist to reach the branches cheaply, not to establish that
    the branches are the right ones.
    """

    def __init__(self, *, root: Path, metadata: dict, direct_url: str | None):
        self._root = root
        self.metadata = metadata
        self._direct_url = direct_url

    def locate_file(self, name: str) -> Path:
        return self._root / name

    def read_text(self, name: str) -> str | None:
        return self._direct_url if name == "direct_url.json" else None


def _module_at(path: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(__file__=str(path), __loader__=None)


def _wheel_dist(root: Path, digest: str, url: str = "file:///tmp/assay-1.0.whl"):
    return _FakeDistribution(
        root=root,
        metadata={"Name": "assay", "Version": "1.0.0"},
        direct_url=json.dumps({"url": url, "archive_info": {"hashes": {"sha256": digest}}}),
    )


A_DIGEST = "5e" * 32


def test_a_wheel_install_reports_the_installers_recorded_sha256(tmp_path: Path):
    identity, reason = provenance.identify_judge(
        _module_at(tmp_path / "assay" / "__init__.py"), _wheel_dist(tmp_path, A_DIGEST)
    )
    assert reason is None
    assert identity == JudgeProvenance(
        name="assay",
        version="1.0.0",
        artifact="wheel",
        digest_algorithm="sha256",
        digest=A_DIGEST,
    )


def test_pep_610s_superseded_single_hash_spelling_is_still_read(tmp_path: Path):
    """`archive_info.hash` predates `archive_info.hashes` and pip still writes
    both. Read as a fallback, not required -- an installer that wrote only the
    old spelling produced a real installation and should not be unidentifiable
    for a metadata-vocabulary reason."""
    dist = _FakeDistribution(
        root=tmp_path,
        metadata={"Name": "assay", "Version": "1.0.0"},
        direct_url=json.dumps(
            {
                "url": "file:///tmp/assay-1.0.whl",
                "archive_info": {"hash": f"sha256={A_DIGEST}"},
            }
        ),
    )
    identity, reason = provenance.identify_judge(
        _module_at(tmp_path / "assay" / "__init__.py"), dist
    )
    assert reason is None and identity is not None
    assert identity.digest == A_DIGEST


@pytest.mark.parametrize(
    ("direct_url", "why"),
    [
        (None, "no direct_url.json at all -- an index install"),
        ('{"url": "file:///src", "dir_info": {"editable": true}}', "an editable install"),
        ('{"url": "file:///src", "dir_info": {}}', "a directory install"),
        (
            '{"url": "https://example/assay.git", "vcs_info": {"vcs": "git"}}',
            "a VCS install: a repository, not a build",
        ),
        (
            '{"url": "file:///tmp/assay-1.0.tar.gz", "archive_info": {"hashes":'
            ' {"sha256": "' + A_DIGEST + '"}}}',
            "an sdist: an archive, but not the artifact assay ships",
        ),
        (
            '{"url": "file:///tmp/assay-1.0.whl", "archive_info": {}}',
            "a wheel the installer recorded no digest for",
        ),
        ("not json at all", "unparseable installer metadata"),
    ],
)
def test_an_unidentifiable_installation_yields_a_reason_and_no_identity(
    tmp_path: Path, direct_url, why
):
    dist = _FakeDistribution(
        root=tmp_path, metadata={"Name": "assay", "Version": "1.0.0"}, direct_url=direct_url
    )
    identity, reason = provenance.identify_judge(
        _module_at(tmp_path / "assay" / "__init__.py"), dist
    )
    assert identity is None, why
    assert reason and "direct_url.json" in reason, why


def test_an_uppercase_recorded_digest_is_normalized_rather_than_refused(tmp_path: Path):
    """Hex case is not semantic, and an installer that wrote uppercase
    produced a real installation. It is normalized to the ONE spelling the
    artifact contract allows -- lowercase -- rather than refused, because a
    consumer comparing this against its own resolved digest compares
    strings."""
    identity, reason = provenance.identify_judge(
        _module_at(tmp_path / "assay" / "__init__.py"),
        _wheel_dist(tmp_path, A_DIGEST.upper()),
    )
    assert reason is None and identity is not None
    assert identity.digest == A_DIGEST


@pytest.mark.parametrize("digest", ["", A_DIGEST[:10], "z" * 64, A_DIGEST + "0"])
def test_a_malformed_recorded_digest_is_an_unidentifiable_installation(
    tmp_path: Path, digest
):
    """The installer's record is metadata on disk, not a computed value, so it
    is CHECKED rather than trusted. A malformed one must not become a
    `JudgeProvenance` whose digest is garbage -- and must not raise out of a
    function whose contract is never to."""
    identity, reason = provenance.identify_judge(
        _module_at(tmp_path / "assay" / "__init__.py"), _wheel_dist(tmp_path, digest)
    )
    assert identity is None and reason


def test_a_source_import_shadowing_an_installed_distribution_is_refused(tmp_path: Path):
    """**The falsification this guard exists for.**
    `sys.path.insert(0, ".../assay/src")` beside an installed assay imports
    the SOURCE while `importlib.metadata` still answers with the INSTALLED
    `.dist-info`. Reporting the installed wheel's digest there would name a
    build that never contained the running code: not an absent record but a
    false one, which is strictly worse than the absence this module otherwise
    prefers. `gate/python/qualify_dstdns_sql.py` invokes the CLI in exactly
    this shape, so it is a live configuration, not a hypothetical."""
    installed = tmp_path / "site-packages"
    elsewhere = tmp_path / "checkout" / "src"
    identity, reason = provenance.identify_judge(
        _module_at(elsewhere / "assay" / "__init__.py"),
        _wheel_dist(installed, A_DIGEST),
    )
    assert identity is None
    assert reason and "outside the installed distribution" in reason


def test_metadata_without_a_name_or_version_names_nothing(tmp_path: Path):
    for metadata in ({"Name": None, "Version": "1.0.0"}, {"Name": "assay", "Version": None}):
        dist = _FakeDistribution(root=tmp_path, metadata=metadata, direct_url=None)
        identity, reason = provenance.identify_judge(
            _module_at(tmp_path / "assay" / "__init__.py"), dist
        )
        assert identity is None
        assert reason and "cannot name" in reason


def test_the_live_source_checkout_this_suite_runs_from_identifies_nothing():
    """Not a stand-in: the real interpreter running this test. If this ever
    starts returning an identity, every source-tree assertion in the suite is
    resting on a false premise and should fail here first."""
    identity, reason = provenance.identify_judge()
    assert identity is None
    assert reason


# --- ...and what the CLI does with it ---------------------------------------


def test_an_unidentifiable_run_records_no_identity_and_says_so(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 0
    document = json.loads(out)
    # Absent, never partial and never null (A-051).
    assert "judge_provenance" not in document
    assert err.startswith("assay: no judge_provenance recorded -- ")
    assert "--require-judge-provenance" in err


def test_require_judge_provenance_refuses_an_unidentifiable_run(git_repo: GitRepo):
    """A gate that binds evidence to a verified judge passes this flag, and
    gets a refusal rather than evidence it cannot attribute."""
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(
        ["run", "package", "--file", str(path), "--require-judge-provenance"]
    )

    assert code == 2
    assert "--require-judge-provenance" in err
    assert "cannot identify the build artifact it is running from" in err


def test_the_refusal_happens_before_the_lane_command_ever_runs(
    git_repo: GitRepo, tmp_path: Path
):
    """"Before any work" is the claim, so it is measured: the lane's own argv
    would create a file, and the refusal means that file never appears."""
    witness = tmp_path / "the-lane-ran"
    lane = set_key(R0_LANE, "argv", f'["/bin/sh", "-c", "touch {witness}"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, _out, _err = run(
        ["run", "package", "--file", str(path), "--require-judge-provenance"]
    )

    assert code == 2
    assert not witness.exists(), "the lane command ran despite the refusal"


# ===========================================================================
# B019 -- the gate-request-supplied comparison base
# ===========================================================================


def _r2_lane(*, base_line: str) -> str:
    """A real, complete R0+R2 lane whose ONLY variable is who owns the base.

    R2 rather than R1 for two reasons: it needs no coverage artifact to reach
    a judgment (so nothing but the base is under test here), and it is the
    tier CIU V8's own proposal is built around. `test_cli_run.py` proves this
    same lane shape kills a real mutant end to end; this module reuses the
    shape and varies only `judge`'s base ownership.
    """
    return (
        "schema_version = 2\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R2"]\n'
        'enforcement = "gate"\n'
        "argv = [\"/bin/sh\", \"-c\", \"grep -q 'x > 0' src/mod.py\"]\n"
        "env = {}\n"
        'env_passthrough = ["PATH"]\n'
        'budget = "1m"\n'
        "allow_argv_append = false\n\n"
        "[lanes.package.isolation]\n"
        'snapshot_selection = "repository"\n\n'
        "[lanes.package.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        f"{base_line}\n\n"
        "[lanes.package.judge.mutation]\n"
        "jobs = 1\n"
        "max_mutants = 50\n"
        'operators = ["python:compare-swap"]\n'
    )


def _repo_with_two_commits(git_repo: GitRepo) -> str:
    """A base commit and a head commit whose diff introduces exactly one
    changed-line mutation site -- so an R2 lane scoped against that base has
    real work to do, and one scoped against nothing would be visibly
    different."""
    (git_repo.path / "src").mkdir(exist_ok=True)
    git_repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base = git_repo.commit_all("base commit")
    git_repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.commit_all("introduce a compare-swap site")
    return base


def test_a_delegating_lane_with_no_request_base_refuses_loudly(git_repo: GitRepo):
    """**B019's central refusal.** No fallback to HEAD, to a default branch,
    or to any other invented value: a changed-line judgment whose base was
    guessed is not a changed-line judgment."""
    _repo_with_two_commits(git_repo)
    path = _write_and_commit_lane(git_repo, _r2_lane(base_line='base_source = "request"'))

    code, _out, err = run(["run", "package", "--file", str(path)])

    assert code == 2
    assert "judge.base_source = 'request'" in err
    assert "--request-base" in err
    assert "does not fall back to HEAD" in err


def test_a_declaring_lane_handed_a_request_base_refuses_rather_than_choosing(
    git_repo: GitRepo,
):
    """Neither side wins. Whichever lost would be configuration nothing reads
    -- A-062 -- so the operator is told which one line to delete."""
    base = _repo_with_two_commits(git_repo)
    path = _write_and_commit_lane(git_repo, _r2_lane(base_line=f'base = "{base}"'))

    code, _out, err = run(
        ["run", "package", "--file", str(path), "--request-base", base]
    )

    assert code == 2
    assert "declares its own judge.base" in err
    assert "base_source = 'request'" in err


def test_a_lane_reading_no_base_at_all_refuses_a_request_base(git_repo: GitRepo):
    """An R0-only lane resolves nothing against a comparison commit, and this
    refusal is reached ABOVE `run_lane`'s own R0/higher-rigor dispatch so the
    R0 path cannot silently ignore the argument."""
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, _out, err = run(
        ["run", "package", "--file", str(path), "--request-base", "HEAD~1"]
    )

    assert code == 2
    assert "reads no comparison base at all" in err


def test_a_delegating_lane_records_the_request_resolved_base_exactly_once(
    git_repo: GitRepo,
):
    """The positive half: the request's ref goes through the SAME merge-base
    resolution `judge.base` does, and lands in `judgment.resolved.base` once,
    beside the `base_resolution` classification that only a resolved base
    gets."""
    base = _repo_with_two_commits(git_repo)
    path = _write_and_commit_lane(git_repo, _r2_lane(base_line='base_source = "request"'))

    code, out, _err = run(
        [
            "run", "package", "--file", str(path),
            "--request-base", base,
            "--verdict-json", "-",
        ]
    )

    document = json.loads(out)
    resolved = document["judgment"]["resolved"]
    assert resolved["base"] == base
    assert resolved["base_resolution"] in {"merge-base", "first-parent"}
    assert code == document["exit_code"]
    # B035/A-329, on the same artifact: this is an `R0,R2` document, the shape
    # whose base rule was unenforceable at v7. It now says which scope it
    # judged under, so the base it just recorded is checkable -- and a
    # request-supplied base changes nothing about that, which is the point.
    assert document["judgment"]["r2"]["mode"] == "changed_lines"
    assert "targets" not in document["judgment"]["r2"]


def test_a_request_supplied_symbolic_ref_resolves_the_same_way_a_declared_one_does(
    git_repo: GitRepo,
):
    """"A ref OR an already-resolved commit", from B019's own contract. The
    branch name below resolves through `git.resolve_base` to the identical
    OID a declared `judge.base` naming it would have produced."""
    base = _repo_with_two_commits(git_repo)
    git_repo.git("branch", "the-gates-base", base)
    path = _write_and_commit_lane(git_repo, _r2_lane(base_line='base_source = "request"'))

    _code, out, _err = run(
        [
            "run", "package", "--file", str(path),
            "--request-base", "the-gates-base",
            "--verdict-json", "-",
        ]
    )
    by_ref = json.loads(out)["judgment"]["resolved"]["base"]

    declared = _write_and_commit_lane(git_repo, _r2_lane(base_line=f'base = "{base}"'))
    _code, out, _err = run(
        ["run", "package", "--file", str(declared), "--verdict-json", "-"]
    )
    by_declaration = json.loads(out)["judgment"]["resolved"]["base"]

    assert by_ref == by_declaration == base


def test_plan_refuses_a_delegating_lane_with_no_request_base(git_repo: GitRepo):
    """`plan` predicts a run, so it must refuse on identical terms -- a plan
    scoped against a base the run would not use is worse than no plan."""
    _repo_with_two_commits(git_repo)
    path = _write_and_commit_lane(git_repo, _r2_lane(base_line='base_source = "request"'))

    code, _out, err = run(["plan", "package", "--file", str(path)])

    assert code == 2
    assert "--request-base" in err


def test_the_resolver_is_one_function_both_verbs_call():
    """A-328's own structural claim, asserted rather than described: `run` and
    `plan` do not each carry a copy of the precedence rule."""
    from assay import runner

    lane = types.SimpleNamespace(
        name="package",
        judge=types.SimpleNamespace(base=None, base_source="request"),
    )
    with pytest.raises(LaneConfigError) as excinfo:
        runner.resolve_base_declaration(lane, None)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert runner.resolve_base_declaration(lane, "origin/main") == "origin/main"
