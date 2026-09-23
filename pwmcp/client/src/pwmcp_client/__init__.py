"""Consumer API for the pwmcp Playwright service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pwmcp-client")
except PackageNotFoundError:
    # The source tree's declared project version is authoritative before
    # installation; installed wheels use their package metadata above.
    __version__ = "0.1.0"

from .contract import PwmcpContract, load_contract
from .session import BrowserLease, PwmcpError, PwmcpUnavailable, VersionMismatch

__all__ = [
    "BrowserLease",
    "PwmcpContract",
    "PwmcpError",
    "PwmcpUnavailable",
    "VersionMismatch",
    "load_contract",
    "__version__",
]
