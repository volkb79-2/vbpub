"""iogovmon: a RECOMMEND-ONLY recorder of long-term shifts in virtual-disk capability (v1).

It measures with tiny bounded probes, journals what it saw, and recognises slow regime changes.
It never changes a cap, a model, a queue setting or any cgroup file. See docs/DESIGN-GUIDE.md.
"""

from importlib import metadata as _metadata

# Prefer the installed wheel's version; source installs write the version resolved by install.sh into this root-owned package copy.
try:
    __version__ = _metadata.version("iogovmon")
except _metadata.PackageNotFoundError:
    try:
        from ._version_installed import __version__
    except ModuleNotFoundError as exc:
        if exc.name != f"{__name__}._version_installed":
            raise
        __version__ = "0+source"
SCHEMA_VERSION = 1
