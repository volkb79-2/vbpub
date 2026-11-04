"""nyxloom — files-first control plane for role-separated AI development.

Frozen-core modules (written by the frontier session; implementation agents
MUST NOT modify them — file a BLOCKED report instead):
    types, paths, storage, config, leases
Package modules (one implementation handoff each, see ../../handoff/):
    frontmatter, lint, reconcile, adapters, wrapper, daemon, render,
    notify, decisions, doctor, cli
"""

# setuptools-scm stamps the version into the wheel METADATA at build time (git-tag
# derived — see pyproject [tool.setuptools_scm]); at runtime we read it back from the
# installed distribution's metadata. An un-installed source checkout (the daemon runs
# from mounted source via PYTHONPATH) has no metadata, so it reports a sentinel until
# it runs installed. Extracted to a helper so BOTH branches are unit-tested: nyxloom's
# changed-line coverage gate rejects no-cover exclusions on changed lines (GA2b), so
# there is deliberately no un-covered fallback here.
from importlib.metadata import PackageNotFoundError, distributions as _distributions, version as _dist_version
from pathlib import Path as _Path


def _unshadowed_version(search_path: list[str] | None, package_root: _Path) -> str | None:
    """NL-24: when this package is imported from a checkout's `src/` (the
    exec-nyxloom.py host fallback puts it first on sys.path), a leftover
    `src/nyxloom.egg-info` from an old editable install is found BEFORE the
    installed dist-info and reports a stale version. If such a build directory
    sits beside the imported package AND a distribution installed elsewhere
    exists, report the installed one; otherwise None (fall through to the plain
    lookup, so a legitimate egg-info-only dev install keeps working)."""
    if not any(package_root.glob("nyxloom*.egg-info")):
        return None  # cheap exit: the common case scans no sys.path metadata at all
    found = [
        d for d in _distributions(path=search_path)
        if (d.metadata["Name"] or "").lower().replace("_", "-") == "nyxloom"
    ]
    if not any(_Path(d.locate_file("")).resolve() == package_root for d in found):
        return None
    for d in found:
        if _Path(d.locate_file("")).resolve() != package_root:
            return d.version
    return None


def _resolve_version(search_path: list[str] | None = None, package_root: _Path | None = None) -> str:
    root = package_root if package_root is not None else _Path(__file__).resolve().parent.parent
    shadow_free = _unshadowed_version(search_path, root)
    if shadow_free is not None:
        return shadow_free
    try:
        return _dist_version("nyxloom")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()
