#!/usr/bin/env python3
"""Build Assay's release artifacts from a committed source OID.

Standalone and stdlib-only, in the same register as ``release_wheel.py``: cmru
invokes this as a project ``build`` step, and it must run before Assay itself is
installed anywhere.

**Why this exists rather than cmru's built-in ``wheel-build``** (A-247/B002):
cmru's batteries-included handler is ``python -m build --wheel --outdir dist``
with no ``--require-hashes``, no ``--no-build-isolation``, no committed
wheelhouse and no private clone. Taking it would silently replace A-198's
five-wheel hash-bound closure and A-199's clean-source guarantee with an
unpinned ambient build. assay therefore adopts cmru's *orchestration* (tag,
Release, per-product prefix, ``latest.json``, isolated release worktree) and
declines its *build*.

**Why this is not folded into ``tools/tester-unified-gate.sh``**: that script is
the registered merge gate for every future package, its markers are under test,
and it builds into a ``mktemp -d`` it discards -- it is a gate, not a release
build. Adding a release mode to it would put the merge gate on the release
path. The two builders share the *inputs* instead (``build-requirements.txt``
and ``build-wheelhouse/``), and each derives the pin table its own way -- this
one reads the requirements file, the gate transcribes it independently -- so the
two are cross-witnesses rather than one file trusting itself
(``tests/test_distribution_build_release.py`` pins that agreement).

Five things here are load-bearing and each has a test that fails without it:

1. **The zipapp is built from the WHEEL, never from ``src/``.** A zipapp built
   straight from the source tree carries no ``.dist-info``, so
   ``importlib.metadata`` raises and ``assay.__version__`` becomes the
   ``0+unknown`` source-tree fallback -- and every verdict such a zipapp emits
   records that as ``assay_version``, which ``assay verify`` then accepts. An
   artifact that cannot be attributed to a release, accepted silently. Building
   from the wheel also makes the zipapp a strict derivative of the audited
   wheel: one build closure, two shapes.
2. **``__main__.py`` is written here, never synthesised by ``zipapp -m``.**
   ``zipapp``'s generated entry point is ``import assay.cli`` /
   ``assay.cli.main()`` -- it DISCARDS the return value, so the process always
   exits 0. That would turn every FAIL, ERROR and NO_MEASUREMENT verdict into a
   silent success at the one boundary a consumer reads. Verified by reading
   ``zipapp``'s own output, not assumed.
3. **Every staged mtime is normalised before archiving.** ``zipapp`` walks
   ``sorted(source.rglob('*'))`` (deterministic order) but takes each entry's
   timestamp from the filesystem, so two builds one second apart differ. cmru's
   S9.4 requires byte-identical artifacts from the same commit.
4. **A manifest is emitted only for a TAGGED release.** A-200's own text says
   SCM development builds are not release manifests, but ``release_wheel.py``'s
   grammar accepts ``.devN+g<sha>`` (it validates the SPELLING, and pre/post/dev
   suffixes are legal PEP 440). Nothing mechanically enforced the policy. This
   asks git -- ``describe --exact-match`` against the project's own
   ``assay-v*`` tag glob -- rather than pattern-matching the version, because
   "did this come from a release tag" is a fact about the repository, not about
   a string.
5. **``bin/`` and ``__pycache__`` never enter the archive.** ``pip install
   --target`` leaves a console-script shim whose shebang points at the building
   interpreter; inside a zipapp it is dead weight that would also make the
   archive depend on the builder's own paths.

Phase markers go to stdout, one per line, the same idiom the registered gate
uses -- a caller can assert the pipeline really ran rather than trusting exit 0.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import zipapp
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: The fixed timestamp every staged entry is stamped with before archiving.
#: 1980-01-01 is the ZIP format's own epoch -- the earliest value a DOS
#: timestamp can express, so it round-trips exactly and cannot be shifted by a
#: reader's timezone. Chosen over ``SOURCE_DATE_EPOCH`` because the input here
#: is already content-addressed by the wheel's sha256: a per-build timestamp
#: would make two builds of the SAME wheel differ, which is the property this
#: constant exists to remove.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: Never a real identity, tagged or not: `0.0.0` is what an ambient build with
#: no `setuptools_scm` produces and `0+unknown` is the source-tree fallback.
PLACEHOLDER_VERSIONS = frozenset({"0.0.0", "0+unknown"})

#: `pyproject.toml`'s `fallback_version`. Refused only on an UNTAGGED build:
#: string-matching it unconditionally would refuse a legitimate future
#: `assay-v0.1.0` tag, which is indistinguishable from the fallback by spelling
#: alone. The tag is the discriminator, not the string.
FALLBACK_VERSION = "0.1.0"

#: The project's own tag line. Must equal ``pyproject.toml``'s
#: ``[tool.setuptools_scm] git_describe_command`` match glob and cmru's
#: ``[project.assay] prefix``; ``test_distribution_build_release.py`` pins all
#: three together, because a divergence would version a release off some other
#: product's tag.
TAG_GLOB = "assay-v*"

WHEEL_NAME_RE = re.compile(r"\Aassay-(?P<version>.+)-py3-none-any\.whl\Z")
REQUIREMENT_RE = re.compile(r"\A(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s]+)")


class ReleaseBuildError(RuntimeError):
    """The release build cannot proceed truthfully."""


def _run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        argv, cwd=None if cwd is None else str(cwd),
        env=None if env is None else {**os.environ, **env},
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise ReleaseBuildError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n"
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _phase(name: str) -> None:
    print(f"ASSAY_RELEASE_PHASE={name}", flush=True)


@dataclass(frozen=True, kw_only=True)
class ReleaseArtifacts:
    version: str
    wheel: Path
    zipapp: Path
    manifest: Path | None
    tagged: bool


def locked_pins(requirements: Path) -> dict[str, str]:
    """The exact ``name == version`` closure declared in *requirements*.

    Derived from the file the install actually consumes, so this builder cannot
    drift from its own ``--require-hashes`` input. The registered gate keeps an
    independent hand transcription of the same five; the two are compared by
    test.
    """
    pins: dict[str, str] = {}
    for line in requirements.read_text(encoding="utf-8").splitlines():
        match = REQUIREMENT_RE.match(line.strip())
        if match is not None:
            pins[match.group("name").replace("_", "-").lower()] = match.group("version")
    if not pins:
        raise ReleaseBuildError(f"{requirements} declares no pinned requirement")
    return pins


def make_exact_oid_clone(repo: Path, scratch: Path) -> str:
    """A private, no-local, sparse clone of *repo* at its exact HEAD OID.

    ``--no-local`` so the clone owns its own objects rather than hardlinking the
    caller's, and no working-tree copy anywhere: A-199's first real probe
    committed ignored ``__pycache__``/egg-info residue into a copied tree and
    produced a wheel over twice the correct size, *reproducibly*. Reproducible
    contamination is still contamination.
    """
    oid = _run(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", oid):
        raise ReleaseBuildError(f"could not resolve a full HEAD OID for {repo}")
    clone = scratch / "clone"
    _run(["git", "clone", "--no-local", "--no-checkout", "--quiet", str(repo), str(clone)])
    _run(["git", "-C", str(clone), "sparse-checkout", "init", "--cone"])
    _run(["git", "-C", str(clone), "sparse-checkout", "set", "assay"])
    _run(["git", "-C", str(clone), "checkout", "--quiet", "--detach", oid])
    clone_head = _run(["git", "-C", str(clone), "rev-parse", "HEAD"]).strip()
    if clone_head != oid:
        raise ReleaseBuildError(
            f"private clone HEAD ({clone_head}) does not match source OID ({oid})"
        )
    return oid


def head_release_tag(repo: Path) -> str | None:
    """The ``assay-v*`` tag on HEAD itself, or ``None``.

    ``--exact-match`` deliberately: a tag merely *reachable* from HEAD makes
    every later commit look like that release.
    """
    completed = subprocess.run(
        ["git", "-C", str(repo), "describe", "--tags", "--exact-match",
         "--match", TAG_GLOB, "HEAD"],
        capture_output=True, text=True, check=False,
    )
    tag = completed.stdout.strip()
    return tag if completed.returncode == 0 and tag else None


def build_closure_venv(scratch: Path, distribution: Path, *, base_python: str) -> Path:
    """A build venv holding exactly the locked five-wheel offline closure."""
    build_venv = scratch / "build-venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(build_venv)
    python = build_venv / "bin" / "python"
    _run([
        str(python), "-m", "pip", "install", "--quiet", "--no-index",
        "--find-links", str(distribution / "build-wheelhouse"),
        "--require-hashes", "-r", str(distribution / "build-requirements.txt"),
    ])
    expected = locked_pins(distribution / "build-requirements.txt")
    observed = _run([
        str(python), "-c",
        "import json;from importlib.metadata import version;"
        "import sys;print(json.dumps({n:version(n) for n in sys.argv[1:]}))",
        *expected,
    ])
    import json as _json

    got = _json.loads(observed)
    wrong = {name: (want, got.get(name)) for name, want in expected.items()
             if got.get(name) != want}
    if wrong:
        raise ReleaseBuildError(
            f"installed build closure does not match the locked pins: {wrong}"
        )
    del base_python  # resolved by EnvBuilder; kept for call-site symmetry
    return build_venv


def commit_epoch(repo: Path) -> str:
    """HEAD's own commit timestamp, as ``SOURCE_DATE_EPOCH``.

    MEASURED, not assumed: without this the wheel is NOT reproducible. Two
    builds of the same OID produced two different wheels
    (``93a562cb…`` vs ``5f35bb65…``) and therefore two different zipapps,
    because a fresh clone's checkout stamps every file with the checkout time
    and ``wheel`` reads those mtimes into the archive. Normalising the ZIPAPP's
    own staging tree does not help -- the non-determinism is upstream, inside
    the wheel the zipapp is built from.

    The commit's timestamp is used rather than the clock so the artifact is a
    function of the COMMIT alone, which is what cmru's S9.4 ("the same source
    commit and toolchain pin") actually asks for. ``pyproject.toml``'s own
    build-system comment already names this mechanism -- "same toolchain +
    SOURCE_DATE_EPOCH -> identical sha256" -- so this closes a gap between that
    stated intent and anything that enforced it.
    """
    stamp = _run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "HEAD"]).strip()
    if not stamp.isdigit():
        raise ReleaseBuildError(f"could not read HEAD's commit timestamp in {repo}")
    return stamp


def build_wheel(
    build_venv: Path, clone: Path, outdir: Path, *,
    source_date_epoch: str, tag: str | None,
) -> tuple[Path, str]:
    """One wheel, built offline with no build isolation, version verified.

    *tag* is HEAD's own ``assay-v*`` tag or ``None``, and it decides which
    version checks apply -- see :data:`FALLBACK_VERSION` and the tag-agreement
    check below.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    _run([
        str(build_venv / "bin" / "python"), "-m", "pip", "wheel",
        "--no-index", "--no-build-isolation", "--no-deps",
        "--wheel-dir", str(outdir), str(clone / "assay"),
    ], env={"SOURCE_DATE_EPOCH": source_date_epoch})
    wheels = sorted(outdir.glob("assay-*.whl"))
    if len(wheels) != 1:
        raise ReleaseBuildError(f"expected exactly one Assay wheel, found {len(wheels)}")
    wheel = wheels[0]
    match = WHEEL_NAME_RE.match(wheel.name)
    if match is None:
        raise ReleaseBuildError(f"unexpected wheel filename: {wheel.name}")
    filename_version = match.group("version")
    metadata_version = _wheel_metadata_version(wheel)
    if filename_version != metadata_version:
        raise ReleaseBuildError(
            f"wheel filename version {filename_version!r} != METADATA version "
            f"{metadata_version!r}"
        )
    if metadata_version in PLACEHOLDER_VERSIONS:
        raise ReleaseBuildError(
            f"wheel version is the placeholder {metadata_version!r}: a release "
            f"artifact must carry a real SCM identity"
        )
    if tag is None:
        if metadata_version == FALLBACK_VERSION:
            raise ReleaseBuildError(
                f"wheel version is pyproject's fallback_version "
                f"{metadata_version!r} on an untagged build: setuptools_scm did "
                f"not resolve the repository, so this artifact records no real "
                f"source identity"
            )
    else:
        expected = tag[len("assay-v"):]
        if metadata_version != expected:
            # FAIL-CLOSED, and deliberately NOT reachable through the normal
            # path -- stated plainly rather than justified with a scenario that
            # does not hold. The obvious motivation (a dirty tree at a tagged
            # commit builds `<tag>.dev0+...dYYYYMMDD`) is WRONG here: A-199's
            # private clone checks out the committed OID, so working-tree dirt
            # in the caller's repository never reaches setuptools_scm at all.
            # Two `assay-v*` tags on one commit does not reach it either --
            # measured, `describe --exact-match` and setuptools_scm's own
            # `describe --long` pick the same tag.
            #
            # It stays because the alternative to an unreachable assertion here
            # is a SILENT mislabelling: the tag names the Release the artifact
            # is published under, and nothing downstream re-derives the version
            # from the tag. Same reasoning as `verify.py`'s
            # `_BASELINE_NEVER_READ` -- if the proof stops holding, this is a
            # loud refusal rather than a wrong publication. Driven directly by
            # unit test rather than left unexercised.
            raise ReleaseBuildError(
                f"HEAD carries tag {tag!r} but the wheel built as "
                f"{metadata_version!r}, not {expected!r}: the working tree is "
                f"dirty or the tag does not describe this source"
            )
    return wheel, metadata_version


def _wheel_metadata_version(wheel: Path) -> str:
    import email

    with zipfile.ZipFile(wheel) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        document = email.message_from_bytes(archive.read(name))
    version = document["Version"]
    if not version:
        raise ReleaseBuildError(f"{wheel.name} has an empty METADATA version")
    return version


ZIPAPP_MAIN = '''\
"""assay, as a zipapp. Generated by gate/distribution/build_release.py."""
import sys

from assay.cli import main

# `sys.exit(main(...))` is the whole point of not letting `zipapp -m` write
# this file: its generated entry point calls main() and drops the result, so a
# FAIL, ERROR or NO_MEASUREMENT verdict would exit 0.
sys.exit(main(sys.argv[1:]))
'''


def build_zipapp(build_venv: Path, wheel: Path, version: str, outdir: Path) -> Path:
    """A reproducible zipapp, installed FROM *wheel* rather than from source."""
    staging = outdir.parent / "zipapp-staging"
    if staging.exists():
        shutil.rmtree(staging)
    _run([
        str(build_venv / "bin" / "python"), "-m", "pip", "install", "--quiet",
        "--no-index", "--no-deps", "--target", str(staging), str(wheel),
    ])
    dist_infos = list(staging.glob("assay-*.dist-info"))
    if len(dist_infos) != 1:
        raise ReleaseBuildError(
            f"expected exactly one assay .dist-info in the zipapp staging tree, "
            f"found {len(dist_infos)} -- without it importlib.metadata cannot "
            f"resolve a version and the zipapp would report 0+unknown"
        )
    # `bin/` is a shebang shim pointing at the BUILDING interpreter, and
    # `__pycache__` is builder-specific bytecode: both would make the archive
    # depend on where it was built.
    for junk in (staging / "bin", *staging.rglob("__pycache__")):
        if junk.is_dir():
            shutil.rmtree(junk)
    _strip_installation_metadata(dist_infos[0])
    (staging / "__main__.py").write_text(ZIPAPP_MAIN, encoding="utf-8")
    _normalise_mtimes(staging)

    target = outdir / f"assay-{version}.pyz"
    zipapp.create_archive(
        staging, target=target, interpreter="/usr/bin/env python3", main=None
    )
    target.chmod(0o755)
    return target



def _strip_installation_metadata(dist_info: Path) -> None:
    """Remove metadata about *this* installation, keeping RECORD consistent.

    MEASURED, not anticipated. With ``SOURCE_DATE_EPOCH`` fixed the WHEEL became
    byte-identical across builds but the zipapp still did not, and diffing the
    two archives named exactly two members:

    * ``direct_url.json`` -- PEP 610, and it records the wheel's absolute
      ``file://`` URL, so the archive embedded the builder's own output
      directory;
    * ``RECORD`` -- whose first line is ``../../bin/assay``, the console-script
      shim, whose sha256 differs per build because the shim's shebang embeds the
      BUILDING interpreter's path.

    Both describe how the wheel was installed on one machine, not what the
    distribution is, so neither belongs in a redistributable archive. ``RECORD``
    is rewritten to only the entries that actually exist in the staged tree --
    which drops the deleted ``bin/`` shim and ``direct_url.json`` together and
    leaves it self-consistent with the archive, strictly more honest than a
    RECORD naming files that are not there.

    ``importlib.metadata.version()`` reads METADATA, never RECORD, so the
    version the zipapp reports is untouched -- asserted by test rather than
    assumed.
    """
    direct_url = dist_info / "direct_url.json"
    if direct_url.exists():
        direct_url.unlink()
    record = dist_info / "RECORD"
    if not record.exists():
        return
    kept = [
        line for line in record.read_text(encoding="utf-8").splitlines()
        if line and (dist_info.parent / line.split(",", 1)[0]).exists()
    ]
    record.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


def _normalise_mtimes(root: Path) -> None:
    """Stamp every entry with :data:`ZIP_EPOCH` so the archive is byte-stable."""
    import calendar

    stamp = calendar.timegm((*ZIP_EPOCH, 0, 0, 0))
    for path in sorted(root.rglob("*"), reverse=True):
        os.utime(path, (stamp, stamp))
    os.utime(root, (stamp, stamp))


def write_sha256_sidecar(artifact: Path) -> Path:
    """cmru S1.3's sidecar: one line in ``sha256sum -c`` format."""
    digest = hashlib.sha256()
    with artifact.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    sidecar = artifact.with_name(artifact.name + ".sha256")
    sidecar.write_text(f"{digest.hexdigest()}  {artifact.name}\n", encoding="utf-8")
    return sidecar


def build(repo: Path, outdir: Path, *, base_python: str | None = None) -> ReleaseArtifacts:
    distribution = repo / "assay" / "gate" / "distribution"
    if not (distribution / "build-requirements.txt").is_file():
        raise ReleaseBuildError(f"{distribution} is not Assay's distribution directory")
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in (*outdir.glob("assay-*.whl"), *outdir.glob("assay-*.pyz"),
                  *outdir.glob("*.sha256"), *outdir.glob("release-manifest.json")):
        stale.unlink()

    tag = head_release_tag(repo)
    with tempfile.TemporaryDirectory(prefix="assay-release-") as raw_scratch:
        scratch = Path(raw_scratch)
        make_exact_oid_clone(repo, scratch)
        _phase("clone")
        build_venv = build_closure_venv(
            scratch, distribution, base_python=base_python or sys.executable
        )
        _phase("closure")
        wheel, version = build_wheel(
            build_venv, scratch / "clone", outdir,
            source_date_epoch=commit_epoch(repo), tag=tag,
        )
        _phase("wheel")
        pyz = build_zipapp(build_venv, wheel, version, outdir)
        _phase("zipapp")

    write_sha256_sidecar(wheel)
    write_sha256_sidecar(pyz)
    _phase("sidecars")

    manifest: Path | None = None
    if tag is None:
        # A-200's policy, enforced mechanically for the first time: a
        # development identity gets a wheel and a zipapp, but no manifest.
        # `release_wheel.py` would have accepted `.devN+g<sha>` -- it validates
        # the SPELLING, and this is the fact its grammar cannot see.
        print(
            f"ASSAY_RELEASE_MANIFEST=skipped version={version} reason=no-{TAG_GLOB}-tag-on-HEAD",
            flush=True,
        )
    else:
        sys.path.insert(0, str(distribution))
        import release_wheel

        manifest = outdir / "release-manifest.json"
        release_wheel.create_release_manifest(wheel, version, manifest)
        write_sha256_sidecar(manifest)
        print(f"ASSAY_RELEASE_MANIFEST=created tag={tag}", flush=True)
    _phase("manifest")

    print(f"ASSAY_RELEASE_COMPLETE={version}", flush=True)
    return ReleaseArtifacts(
        version=version, wheel=wheel, zipapp=pyz, manifest=manifest, tagged=tag is not None
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="build_release.py")
    parser.add_argument(
        "--repo", type=Path, required=True,
        help="the monorepo top holding assay/ (cmru passes the release worktree)",
    )
    parser.add_argument(
        "--outdir", type=Path, required=True, help="where the artifacts are written",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build(args.repo.resolve(), args.outdir)
    except (ReleaseBuildError, OSError, zipfile.BadZipFile) as exc:
        print(f"build-release: REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
