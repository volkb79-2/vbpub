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
from importlib.metadata import PackageNotFoundError, version as _dist_version


def _resolve_version() -> str:
    try:
        return _dist_version("nyxloom")
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()
