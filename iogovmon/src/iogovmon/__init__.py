"""iogovmon: a RECOMMEND-ONLY recorder of long-term shifts in virtual-disk capability (v1).

It measures with tiny bounded probes, journals what it saw, and recognises slow regime changes.
It never changes a cap, a model, a queue setting or any cgroup file. See docs/DESIGN-GUIDE.md.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

# The release version comes from the git tag `iogovmon-vX.Y.Z` (setuptools_scm) and is read from the installed wheel's metadata.
# A source-tree run (PYTHONPATH=src, the systemd units of `install.sh`) has no metadata: it reports "0+source", never a made-up release.
try:
    __version__ = _dist_version("iogovmon")
except PackageNotFoundError:  # pragma: no cover - depends on how the test process was started
    __version__ = "0+source"
SCHEMA_VERSION = 1
