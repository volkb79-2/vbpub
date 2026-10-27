#!/usr/bin/env python3
"""Derive tester-unified's third-party dependency closure from the COPYed projects.

Run by ``tester-unified/Dockerfile`` (``--root /src``); prints one requirement
per line.  The projects' own ``pyproject.toml`` files are the single source of
truth, never a hand-maintained requirements file.

Estate-internal distributions (``cli-extended``, ``worktree``, ``cmru``,
``assay``, ``ciu`` ...) must NEVER reach a PyPI-default ``pip``: a name nobody
has claimed (``cli-extended``) or an unrelated project that has (``worktree``)
would be installed into an image that ``tester-unified/run`` gives the host
Docker socket (dependency confusion, BG-05).  So the generator REFUSES (exit 1)
any requirement naming one; the Dockerfile installs those only from COPYed
project sources with ``--no-index --no-deps``.  The one exception is
``cli-extended`` (D8): cmru declares it as a real dependency, so its line is
SKIPPED (never printed, never refused). The Dockerfile installs a
sha256-verified published wheel by default; the explicit zero-release mode
supplies a local source-built wheel through the same install boundary.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Iterator

#: FLOOR of normalised names of distributions this estate builds itself. The
#: refusal set is this floor UNIONED with every project name found under the
#: copied tree (:func:`derive_internal`), so a new ``libraries/foo`` is covered
#: without editing this list.
#: Operator decision 2026-10-27: these first-party names have NO age window (always the latest
#: published release; ``--uploaded-prior-to`` is for third-party dependencies only), and the way
#: this image honours that is that none of them ever reaches an index-bound pip at all.
ESTATE_INTERNAL = frozenset({
    "cli-extended", "worktree", "cmru", "assay", "ciu", "nyxloom", "topos",
    "cgroup-profiler", "run-gate", "pwmcp", "pwmcp-client", "srdm", "iobench", "vmscreen",
})

#: Estate-internal names that are SKIPPED rather than refused: since cmru
#: declares ``cli-extended>=0.3.0`` (KI-51 / CX-D1) its requirement line reaches
#: this generator, but the Dockerfile installs a verified released wheel or
#: explicit local bootstrap wheel before cmru. No ``cli-extended`` line may
#: ever reach a PyPI-default pip.
INSTALLED_BY_DOCKERFILE = frozenset({"cli-extended"})

#: (pyproject path relative to --root, extras whose requirements the gate needs)
PROJECTS = (
    ("ciu/pyproject.toml", ("ssh", "test")),
    ("cmru/pyproject.toml", ("test",)),
    ("assay/pyproject.toml", ("test",)),
    ("topos/pyproject.toml", ("dev",)),
    ("nyxloom/pyproject.toml", ("test",)),
    ("cgroup-profiler/pyproject.toml", ("test",)),
    ("pwmcp/client/pyproject.toml", ("test",)),
)

#: Projects whose ``[project].dependencies`` are NOT part of the tester closure: the gate needs only
#: their declared test extra. pwmcp-client's runtime dependency ``playwright==1.64.0`` (exact pin,
#: ~150 MB with its bundled driver) is for driving a real browser; its tests stub the browser, so the
#: image must not carry it. ``cli-extended`` is installed by the Dockerfile in every case.
TEST_EXTRAS_ONLY = frozenset({"pwmcp-client"})

#: Source-backed Assay lanes install the selected assay tree into the tester
#: venv; the Run-Gate wheel is built offline from the copied source tree. Their
#: pinned build backends are installed in the isolated build venv.
BUILD_REQUIRES_FOR = frozenset({"assay"})

_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class InternalRequirementError(SystemExit):
    """An estate-internal distribution appeared in a PyPI-bound requirement."""


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    match = _NAME.match(requirement)
    if match is None:
        raise ValueError(f"cannot parse a distribution name from {requirement!r}")
    return normalized(match.group(1))


def derive_internal(root: Path) -> frozenset[str]:
    """The floor plus ``[project].name`` of every ``<root>/*/pyproject.toml`` and
    ``<root>/libraries/*/pyproject.toml`` (normalised)."""
    names = set(ESTATE_INTERNAL)
    for pattern in ("*/pyproject.toml", "libraries/*/pyproject.toml"):
        for pyproject in sorted(root.glob(pattern)):
            with pyproject.open("rb") as handle:
                name = tomllib.load(handle).get("project", {}).get("name")
            if name:
                names.add(normalized(name))
    return frozenset(names)


def refuse_internal(
    requirement: str, source: str, internal: frozenset[str] = ESTATE_INTERNAL,
) -> str:
    """Return ``requirement`` unchanged, or refuse it.

    Refuses estate-internal names and direct-URL (``name @ url``) requirements,
    which bypass the index policy entirely."""
    name = requirement_name(requirement)
    if name in internal:
        raise InternalRequirementError(
            f"gen-requirements: {source} requires estate-internal distribution "
            f"{name!r} ({requirement!r}). Refusing to hand it to a PyPI-default pip; "
            "install it in the Dockerfile from COPYed sources with --no-index --no-deps."
        )
    if "@" in requirement.split(";", 1)[0]:
        raise InternalRequirementError(
            f"gen-requirements: {source} has a direct-URL requirement {requirement!r}; "
            "refusing (it bypasses the index policy)."
        )
    return requirement


def admit(
    requirement: str, source: str, internal: frozenset[str] = ESTATE_INTERNAL,
) -> str | None:
    """The requirement to hand to pip, or ``None`` for a line the Dockerfile
    installs itself (D8: ``cli-extended`` is supplied by the Dockerfile as a
    sha256-verified release or explicit local bootstrap wheel, never an index
    query). Any other estate-internal name is still refused."""
    if requirement_name(requirement) in INSTALLED_BY_DOCKERFILE:
        return None
    return refuse_internal(requirement, source, internal)


def own_extra_requirements(project: dict, extras) -> Iterator[str]:
    """Expand only this project's composite extras.

    ``topos[dev]`` and ``cgroup-profiler[test]`` deliberately reuse their own
    ``zstandard``/``report`` extras.  Handing those self-references to pip would
    make it look for an unrelated package index project instead of reading the
    copied pyproject that is the closure's source of truth.
    """
    pending = list(extras)
    seen: set[str] = set()
    while pending:
        extra = pending.pop()
        if extra in seen:
            continue
        seen.add(extra)
        for requirement in project.get("optional-dependencies", {})[extra]:
            match = re.match(r"^([A-Za-z0-9_.-]+)\[([^]]+)\]$", requirement)
            if match and normalized(match.group(1)) == normalized(project["name"]):
                pending.extend(item.strip() for item in match.group(2).split(","))
            else:
                yield requirement


def requirements(root: Path) -> Iterator[str]:
    internal = derive_internal(root)
    for relative, extras in PROJECTS:
        pyproject = root / relative
        with pyproject.open("rb") as handle:
            document = tomllib.load(handle)
        project = document["project"]
        source = f"{relative} [project]"
        runtime = () if normalized(project["name"]) in TEST_EXTRAS_ONLY else project.get("dependencies", ())
        for requirement in runtime:
            admitted = admit(requirement, source, internal)
            if admitted is not None:
                yield admitted
        for requirement in own_extra_requirements(project, extras):
            admitted = admit(requirement, f"{relative} extras {list(extras)}", internal)
            if admitted is not None:
                yield admitted
        # Source-backed lanes install the selected tree with --no-build-isolation,
        # so its pinned build backend must be in the image.  (Build backends are
        # third-party; the refusal applies to them as well.)
        if normalized(project["name"]) in BUILD_REQUIRES_FOR:
            for requirement in document.get("build-system", {}).get("requires", ()):
                admitted = admit(requirement, f"{relative} [build-system]", internal)
                if admitted is not None:
                    yield admitted
    yield "build"


def build_requirements(root: Path, pyprojects: list[str]) -> Iterator[str]:
    """Pinned build backends of the estate-internal projects the Dockerfile
    builds offline (``--no-index --no-deps --no-build-isolation``) in a
    throwaway venv, so they never clash with the tester venv's own pins."""
    internal = derive_internal(root)
    for relative in pyprojects:
        with (root / relative).open("rb") as handle:
            document = tomllib.load(handle)
        for requirement in document.get("build-system", {}).get("requires", ()):
            admitted = admit(requirement, f"{relative} [build-system]", internal)
            if admitted is not None:
                yield admitted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True,
                        help="directory holding the COPYed project trees (e.g. /src)")
    parser.add_argument("--build-requires", nargs="+", metavar="PYPROJECT", default=None,
                        help="print only the build-system requirements of these "
                             "pyprojects (relative to --root) instead of the test closure")
    args = parser.parse_args(argv)
    produced = (
        build_requirements(args.root, args.build_requires)
        if args.build_requires else requirements(args.root)
    )
    seen: dict[str, None] = {}
    for requirement in produced:
        seen.setdefault(requirement, None)
    sys.stdout.write("".join(f"{requirement}\n" for requirement in seen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
