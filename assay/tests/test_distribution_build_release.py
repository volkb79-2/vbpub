"""P-ship (A-247/A-249): `gate/distribution/build_release.py`, the release
builder cmru invokes as assay's `build` step.

Every check here exists because the property it pins was either found broken by
measurement during this work, or is a fail-closed guard the integration path
cannot reach:

* **The zipapp must be built from the WHEEL.** From `src/` it carries no
  `.dist-info`, reports `0+unknown`, and emits verdicts recording that as
  `assay_version` -- which `assay verify` then accepts. Silent unattributable
  output.
* **`__main__.py` must call `sys.exit(main(...))`.** `zipapp -m` generates
  `assay.cli.main()` and drops the return value, so every FAIL/ERROR verdict
  would exit 0 at the one boundary a consumer reads.
* **Reproducibility took two fixes, both found by running two builds.**
  `SOURCE_DATE_EPOCH` from HEAD's commit (the wheel differed:
  `93a562cb...` vs `5f35bb65...`), then stripping `direct_url.json` and the
  `bin/` shim's `RECORD` line (the zipapp still differed after the wheel was
  fixed, because both embed the builder's own paths).
* **A manifest is emitted only for a TAGGED build.** `release_wheel.py`'s
  grammar accepts `.devN+g<sha>` -- it validates the spelling, and pre/post/dev
  suffixes are legal PEP 440 -- so A-200's "SCM development builds are not
  release manifests" had nothing enforcing it until here.

The end-to-end tests really build, twice, offline from the committed wheelhouse.
That costs ~20s and is the point: the previous two reproducibility claims in
this session were both false, and only running it twice showed that.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
DISTRIBUTION = PROJECT_ROOT / "gate" / "distribution"
BUILDER = DISTRIBUTION / "build_release.py"
GATE_SCRIPT = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"

sys.path.insert(0, str(DISTRIBUTION))
import build_release  # noqa: E402


# ---------------------------------------------------------------------------
# Structure and the cross-witness on the pinned closure
# ---------------------------------------------------------------------------


def test_the_builder_is_a_standalone_stdlib_only_module():
    """A consumer runs the release path before assay is installed anywhere, so
    the builder may not import assay -- the same constraint `release_wheel.py`
    is already held to."""
    assert BUILDER.is_file()
    source = BUILDER.read_text(encoding="utf-8")
    # AST, not a text match: the builder EMBEDS the generated `__main__.py`,
    # which necessarily contains `from assay.cli import main` as a string
    # literal. A regex over the file reads that as an import and reddens on the
    # one line that is supposed to be there -- `test_dependency_purity.py`
    # already uses AST for exactly this reason.
    import ast

    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert "assay" not in imported, (
        f"the release builder must not import the package it builds; got {sorted(imported)}"
    )
    # It DOES import its sibling helper, deliberately, rather than restating
    # A-200's manifest grammar.
    assert "release_wheel" in imported


def test_locked_pins_agree_with_the_gate_scripts_independent_transcription():
    """Two builders, two derivations, one closure.

    `build_release.py` reads `build-requirements.txt`; the registered gate
    hand-transcribes the same five in its own heredoc. Neither is allowed to
    drift, and comparing them is what makes the duplication a cross-witness
    rather than a second place to be wrong.
    """
    pins = build_release.locked_pins(DISTRIBUTION / "build-requirements.txt")
    gate_source = GATE_SCRIPT.read_text(encoding="utf-8")
    transcribed = dict(
        re.findall(r'^\s*"([A-Za-z0-9._-]+)":\s*"([^"]+)",\s*$', gate_source, re.M)
    )
    assert transcribed, "the gate script no longer carries its pin transcription"
    assert pins == {k.replace("_", "-").lower(): v for k, v in transcribed.items()}


def test_the_tag_glob_is_the_same_one_pyproject_and_cmru_use():
    """A release versioned off another product's tag is the failure this pins.

    The monorepo carries `ciu-v*`, `topos-v*`, `cmru-v*` and more on one tag
    line; three independent files have to agree on which of them is assay's.
    """
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    describe = pyproject["tool"]["setuptools_scm"]["git_describe_command"]
    assert build_release.TAG_GLOB in describe, describe
    assert pyproject["tool"]["setuptools_scm"]["tag_regex"].startswith(
        "^" + build_release.TAG_GLOB.rstrip("*")
    )
    cmru = tomllib.loads((REPO_ROOT / "cmru.toml").read_text(encoding="utf-8"))
    assert cmru["project"]["assay"]["prefix"] == build_release.TAG_GLOB.rstrip("*")


def test_the_generated_zipapp_entry_point_propagates_the_exit_code():
    """The source-level half of the `zipapp -m` trap: the generated entry point
    must pass `main`'s return value to `sys.exit`, not discard it."""
    assert "sys.exit(main(sys.argv[1:]))" in build_release.ZIPAPP_MAIN


def test_zipapps_own_generated_main_really_does_drop_the_return_value():
    """The premise behind writing `__main__.py` by hand, proved rather than
    asserted: if a future Python ever makes `zipapp -m` propagate the exit
    code, this reddens and the hand-written file can be reconsidered."""
    import zipapp as zipapp_module

    generated = zipapp_module.MAIN_TEMPLATE.format(module="assay.cli", fn="main")
    assert "sys.exit" not in generated, generated


# ---------------------------------------------------------------------------
# `head_release_tag` -- the discriminator every version rule leans on
# ---------------------------------------------------------------------------


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for argv in (
        ["git", "init", "-q", "."],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(argv, cwd=path, check=True, capture_output=True)
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True, capture_output=True)
    return path


def _commit(path: Path, name: str) -> None:
    (path / name).write_text(name, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", name], cwd=path, check=True, capture_output=True)


def test_head_release_tag_finds_a_matching_tag_on_head(tmp_path: Path):
    repo = _repo(tmp_path / "r")
    subprocess.run(["git", "tag", "-a", "assay-v1.2.3", "-m", "r"], cwd=repo,
                   check=True, capture_output=True)
    assert build_release.head_release_tag(repo) == "assay-v1.2.3"


def test_head_release_tag_is_none_with_no_tag_at_all(tmp_path: Path):
    assert build_release.head_release_tag(_repo(tmp_path / "r")) is None


def test_head_release_tag_ignores_another_products_tag_on_head(tmp_path: Path):
    """The whole monorepo shares one tag line. A `ciu-v*` tag on assay's HEAD
    must not make this look like an assay release."""
    repo = _repo(tmp_path / "r")
    subprocess.run(["git", "tag", "-a", "ciu-v4.0.0", "-m", "r"], cwd=repo,
                   check=True, capture_output=True)
    assert build_release.head_release_tag(repo) is None


def test_head_release_tag_refuses_a_tag_that_is_merely_REACHABLE(tmp_path: Path):
    """`--exact-match`, not plain `describe`: without it every commit after a
    release would report that release's tag and publish as it."""
    repo = _repo(tmp_path / "r")
    subprocess.run(["git", "tag", "-a", "assay-v1.2.3", "-m", "r"], cwd=repo,
                   check=True, capture_output=True)
    _commit(repo, "later.txt")
    assert build_release.head_release_tag(repo) is None


# ---------------------------------------------------------------------------
# The version guards, driven directly
# ---------------------------------------------------------------------------


def _fake_wheel(directory: Path, version: str, *, metadata_version: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / f"assay-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"assay-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: assay\nVersion: "
            f"{metadata_version or version}\n\n",
        )
    return wheel


class _StubVenv:
    """Stands in for the build venv on the guard paths, which never reach pip.

    `build_wheel` runs pip FIRST, so these tests monkeypatch `_run` away and
    then drive the checks that follow it. That is the only way to reach the
    guards: a real build cannot produce a placeholder version, which is the
    point of the guards.
    """


@pytest.fixture()
def no_pip(monkeypatch):
    monkeypatch.setattr(build_release, "_run", lambda *a, **k: "")


@pytest.mark.parametrize("placeholder", sorted(build_release.PLACEHOLDER_VERSIONS))
def test_a_placeholder_version_is_refused_tagged_or_not(tmp_path, no_pip, placeholder):
    _fake_wheel(tmp_path / "out", placeholder)
    for tag in (None, f"assay-v{placeholder}"):
        with pytest.raises(build_release.ReleaseBuildError, match="placeholder"):
            build_release.build_wheel(
                tmp_path / "venv", tmp_path / "clone", tmp_path / "out",
                source_date_epoch="0", tag=tag,
            )


def test_the_fallback_version_is_refused_on_an_untagged_build(tmp_path, no_pip):
    _fake_wheel(tmp_path / "out", build_release.FALLBACK_VERSION)
    with pytest.raises(build_release.ReleaseBuildError, match="fallback_version"):
        build_release.build_wheel(
            tmp_path / "venv", tmp_path / "clone", tmp_path / "out",
            source_date_epoch="0", tag=None,
        )


def test_the_same_version_is_ACCEPTED_when_a_tag_actually_names_it(tmp_path, no_pip):
    """The differential half, and the reason the fallback is not simply
    blacklisted: `0.1.0` is both pyproject's `fallback_version` and a perfectly
    legitimate future `assay-v0.1.0` release. The tag is the discriminator, not
    the spelling."""
    version = build_release.FALLBACK_VERSION
    _fake_wheel(tmp_path / "out", version)
    wheel, resolved = build_release.build_wheel(
        tmp_path / "venv", tmp_path / "clone", tmp_path / "out",
        source_date_epoch="0", tag=f"assay-v{version}",
    )
    assert resolved == version
    assert wheel.name == f"assay-{version}-py3-none-any.whl"


def test_a_wheel_whose_version_disagrees_with_its_tag_is_refused(tmp_path, no_pip):
    """The fail-closed guard the integration path cannot reach (the private
    clone hides working-tree dirt, and two tags on one commit do not disagree).
    Driven here so it is exercised rather than merely present."""
    _fake_wheel(tmp_path / "out", "9.9.9")
    with pytest.raises(build_release.ReleaseBuildError, match="not '1.0.0'"):
        build_release.build_wheel(
            tmp_path / "venv", tmp_path / "clone", tmp_path / "out",
            source_date_epoch="0", tag="assay-v1.0.0",
        )


def test_a_wheel_whose_filename_and_metadata_disagree_is_refused(tmp_path, no_pip):
    _fake_wheel(tmp_path / "out", "1.2.3", metadata_version="4.5.6")
    with pytest.raises(build_release.ReleaseBuildError, match="METADATA version"):
        build_release.build_wheel(
            tmp_path / "venv", tmp_path / "clone", tmp_path / "out",
            source_date_epoch="0", tag=None,
        )


def test_more_than_one_wheel_in_the_output_is_refused(tmp_path, no_pip):
    _fake_wheel(tmp_path / "out", "1.2.3")
    _fake_wheel(tmp_path / "out", "1.2.4")
    with pytest.raises(build_release.ReleaseBuildError, match="exactly one"):
        build_release.build_wheel(
            tmp_path / "venv", tmp_path / "clone", tmp_path / "out",
            source_date_epoch="0", tag=None,
        )


def test_commit_epoch_is_heads_own_timestamp_not_the_clock(tmp_path):
    repo = _repo(tmp_path / "r")
    expected = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "HEAD"], cwd=repo,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert build_release.commit_epoch(repo) == expected


# ---------------------------------------------------------------------------
# `_strip_installation_metadata` -- the second reproducibility fix
# ---------------------------------------------------------------------------


def test_stripping_removes_direct_url_and_prunes_record_to_what_exists(tmp_path: Path):
    staging = tmp_path / "staging"
    dist_info = staging / "assay-1.2.3.dist-info"
    dist_info.mkdir(parents=True)
    (staging / "assay").mkdir()
    (staging / "assay" / "__init__.py").write_text("", encoding="utf-8")
    (dist_info / "METADATA").write_text("Name: assay\n", encoding="utf-8")
    (dist_info / "direct_url.json").write_text('{"url": "file:///tmp/whatever"}', encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "../../bin/assay,sha256=aaa,189\n"
        "assay/__init__.py,sha256=bbb,0\n"
        "assay-1.2.3.dist-info/METADATA,sha256=ccc,13\n"
        "assay-1.2.3.dist-info/direct_url.json,sha256=ddd,30\n",
        encoding="utf-8",
    )

    build_release._strip_installation_metadata(dist_info)

    assert not (dist_info / "direct_url.json").exists()
    kept = (dist_info / "RECORD").read_text(encoding="utf-8").splitlines()
    assert kept == [
        "assay/__init__.py,sha256=bbb,0",
        "assay-1.2.3.dist-info/METADATA,sha256=ccc,13",
    ], kept


def test_stripping_keeps_metadata_because_that_is_what_reports_the_version(tmp_path: Path):
    """`importlib.metadata.version()` reads METADATA, never RECORD -- so the
    pruning above cannot be what makes a zipapp report `0+unknown`."""
    dist_info = tmp_path / "assay-1.2.3.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Name: assay\nVersion: 1.2.3\n", encoding="utf-8")
    build_release._strip_installation_metadata(dist_info)
    assert (dist_info / "METADATA").is_file()


def test_the_sha256_sidecar_is_in_sha256sum_c_format(tmp_path: Path):
    artifact = tmp_path / "assay-1.2.3.pyz"
    artifact.write_bytes(b"payload")
    sidecar = build_release.write_sha256_sidecar(artifact)
    assert sidecar.name == "assay-1.2.3.pyz.sha256"
    line = sidecar.read_text(encoding="utf-8")
    assert re.fullmatch(r"[0-9a-f]{64}  assay-1\.2\.3\.pyz\n", line), line
    check = subprocess.run(
        ["sha256sum", "-c", sidecar.name], cwd=tmp_path, capture_output=True, text=True
    )
    assert check.returncode == 0, check.stdout + check.stderr


# ---------------------------------------------------------------------------
# End to end, against the real repository, offline
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    """One real release build of the current HEAD, plus a second into a
    DIFFERENT directory so reproducibility is a real claim rather than
    same-path luck."""
    first = tmp_path_factory.mktemp("release-a")
    second = tmp_path_factory.mktemp("release-b")
    artifacts = build_release.build(REPO_ROOT, first)
    again = build_release.build(REPO_ROOT, second)
    return {"first": artifacts, "second": again}


def test_the_real_build_produces_a_wheel_a_zipapp_and_both_sidecars(built):
    artifacts = built["first"]
    assert artifacts.wheel.is_file()
    assert artifacts.zipapp.is_file()
    assert artifacts.wheel.with_name(artifacts.wheel.name + ".sha256").is_file()
    assert artifacts.zipapp.with_name(artifacts.zipapp.name + ".sha256").is_file()


def test_two_builds_of_one_commit_are_byte_identical(built):
    """cmru S9.4. Both halves matter: the wheel needed `SOURCE_DATE_EPOCH`, and
    the zipapp additionally needed the installation metadata stripped."""
    first, second = built["first"], built["second"]
    assert first.wheel.read_bytes() == second.wheel.read_bytes(), "wheel is not reproducible"
    assert first.zipapp.read_bytes() == second.zipapp.read_bytes(), "zipapp is not reproducible"


def test_an_untagged_build_emits_no_release_manifest(built):
    """A-200's policy, mechanically enforced for the first time. The repository
    carries no `assay-v*` tag, so this is the live path today."""
    artifacts = built["first"]
    assert artifacts.tagged is False
    assert artifacts.manifest is None
    assert not (artifacts.wheel.parent / "release-manifest.json").exists()


def test_the_untagged_build_still_carries_a_real_scm_identity(built):
    version = built["first"].version
    assert version not in build_release.PLACEHOLDER_VERSIONS
    assert version != build_release.FALLBACK_VERSION
    assert re.match(r"\A[0-9]+\.[0-9]+\.[0-9]+", version), version


def test_the_zipapp_reports_the_wheels_version_and_never_the_source_fallback(built):
    """The finding this builder exists around: from `src/` this prints
    `assay 0+unknown`."""
    artifacts = built["first"]
    out = subprocess.run(
        [sys.executable, str(artifacts.zipapp), "--version"],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"assay {artifacts.version}", out.stdout


def test_the_zipapp_reads_its_packaged_schema_from_inside_the_archive(built):
    """A-029's contract has to survive the packaging change:
    `importlib.resources.files()` must resolve inside a zip."""
    artifacts = built["first"]
    probe = (
        "from assay.verdict import VERDICT_SCHEMA_VERSION, load_schema, schema_text;"
        "import assay;"
        "print(VERDICT_SCHEMA_VERSION, load_schema()['$id'], len(schema_text()),"
        " assay.__file__)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        env={"PYTHONPATH": str(artifacts.zipapp), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    version, schema_id, length, origin = out.stdout.split()
    assert schema_id == f"urn:assay:schema:verdict:{version}"
    assert int(length) > 1000
    assert str(artifacts.zipapp) in origin, origin


def test_the_zipapp_verifies_a_real_artifact_and_refuses_a_foreign_version(built):
    artifacts = built["first"]

    good = subprocess.run(
        [sys.executable, str(artifacts.zipapp), "verify",
         str(PROJECT_ROOT / "tests" / "fixtures" / "verdicts" / "r2_pass_with_judgment.json")],
        capture_output=True, text=True, timeout=120,
    )
    assert good.returncode == 0, good.stderr

    v4 = PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P21" / "expected" / "combined-pass-v4.json"
    foreign = subprocess.run(
        [sys.executable, str(artifacts.zipapp), "verify", str(v4)],
        capture_output=True, text=True, timeout=120,
    )
    assert foreign.returncode == 1, foreign.stdout
    assert "is not this verifier's version" in foreign.stderr


def test_the_zipapp_propagates_a_nonzero_exit_from_a_failing_lane(built, tmp_path):
    """The `zipapp -m` trap, end to end through the real artifact. With the
    generated entry point this exits 0 and a FAIL reads as success."""
    artifacts = built["first"]
    repo = _repo(tmp_path / "consumer")
    (repo / ".gitignore").write_text("verdict.json\n", encoding="utf-8")
    (repo / "assay.toml").write_text(
        'schema_version = 1\n'
        "[lanes.failing]\n"
        'scope = "S1"\nrigor = ["R0"]\nenforcement = "gate"\n'
        'argv = ["/bin/false"]\nenv = {}\nenv_passthrough = ["PATH"]\n'
        'budget = "1m"\nallow_argv_append = false\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "lane"], cwd=repo, check=True, capture_output=True)

    verdict = repo / "verdict.json"
    out = subprocess.run(
        [sys.executable, str(artifacts.zipapp), "run", "failing",
         "--verdict-json", str(verdict)],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert out.returncode != 0, "a FAIL verdict exited 0 through the zipapp"
    document = json.loads(verdict.read_text(encoding="utf-8"))
    assert document["outcome"] == "FAIL"
    assert document["exit_code"] == out.returncode
    assert document["assay_version"] == artifacts.version, (
        "the zipapp recorded a version that is not the wheel's"
    )


def test_the_archive_carries_no_builder_specific_paths(built):
    """Both reproducibility culprits, asserted as absences so a future change
    that reintroduces either is caught by name rather than by a hash diff."""
    artifacts = built["first"]
    with zipfile.ZipFile(artifacts.zipapp) as archive:
        names = archive.namelist()
        assert not any(n.endswith("direct_url.json") for n in names), names
        assert not any(n.startswith("bin/") for n in names), names
        assert not any("__pycache__" in n for n in names), names
        record = next(n for n in names if n.endswith(".dist-info/RECORD"))
        assert b"../../bin/assay" not in archive.read(record)
        assert any(n.endswith(".dist-info/METADATA") for n in names), names
        assert "__main__.py" in names
