"""Consumer API for the pwmcp Playwright service."""

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


def __getattr__(name: str) -> str:
    # ``__version__`` keeps working for library callers but has no literal fallback:
    # it is the CLI identity's version, resolved from installed metadata and the
    # packaged VERSION file, which must agree (a VersionLookupError otherwise).
    if name == "__version__":
        from .cli import identity

        return identity().version
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
