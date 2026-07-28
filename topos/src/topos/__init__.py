"""topos collector core."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("topos")
except PackageNotFoundError:  # source checkout before its first wheel build
    __version__ = "0.1.0"
