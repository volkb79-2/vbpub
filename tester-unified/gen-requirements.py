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
``libraries/*`` and project sources with ``--no-index --no-deps``.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Iterator

#: Normalised names of every distribution this estate builds itself.
ESTATE_INTERNAL = frozenset({
    "cli-extended", "worktree", "cmru", "assay", "ciu", "nyxloom", "topos",
    "cgroup-profiler", "run-gate", "pwmcp", "srdm",
})

#: (pyproject path relative to --root, extras whose requirements the gate needs)
PROJECTS = (
    ("ciu/pyproject.toml", ("ssh", "test")),
    ("cmru/pyproject.toml", ("test",)),
    ("assay/pyproject.toml", ("test",)),
    ("topos/pyproject.toml", ("dev",)),
    ("nyxloom/pyproject.toml", ("test",)),
    ("cgroup-profiler/pyproject.toml", ("test",)),
)

#: Source-backed run-gate lanes install the selected assay tree into the tester
#: venv with --no-build-isolation, so its pinned build backend must be there.
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


def refuse_internal(requirement: str, source: str) -> str:
    """Return ``requirement`` unchanged, or refuse it.

    Refuses estate-internal names and direct-URL (``name @ url``) requirements,
    which bypass the index policy entirely."""
    name = requirement_name(requirement)
    if name in ESTATE_INTERNAL:
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
    for relative, extras in PROJECTS:
        pyproject = root / relative
        with pyproject.open("rb") as handle:
            document = tomllib.load(handle)
        project = document["project"]
        source = f"{relative} [project]"
        for requirement in project.get("dependencies", ()):
            yield refuse_internal(requirement, source)
        for requirement in own_extra_requirements(project, extras):
            yield refuse_internal(requirement, f"{relative} extras {list(extras)}")
        # Source-backed lanes install the selected tree with --no-build-isolation,
        # so its pinned build backend must be in the image.  (Build backends are
        # third-party; the refusal applies to them as well.)
        if normalized(project["name"]) in BUILD_REQUIRES_FOR:
            for requirement in document.get("build-system", {}).get("requires", ()):
                yield refuse_internal(requirement, f"{relative} [build-system]")
    yield "build"


def build_requirements(root: Path, pyprojects: list[str]) -> Iterator[str]:
    """Pinned build backends of the estate-internal projects the Dockerfile
    builds offline (``--no-index --no-deps --no-build-isolation``) in a
    throwaway venv, so they never clash with the tester venv's own pins."""
    for relative in pyprojects:
        with (root / relative).open("rb") as handle:
            document = tomllib.load(handle)
        for requirement in document.get("build-system", {}).get("requires", ()):
            yield refuse_internal(requirement, f"{relative} [build-system]")


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
