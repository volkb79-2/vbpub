"""Small stdlib-only copy-on-write workspace adapter."""
from .detect import ENV_MODE, ENV_ROOT, Area, Capabilities, detect
from .errors import (
    BackendFailed,
    CowError,
    LeaseHeld,
    PathRefused,
    RootInvalid,
    SpaceRefused,
)
from .run import Run, open_run
from .space import SpaceEstimate

API_VERSION = 1

__all__ = [
    "API_VERSION",
    "ENV_MODE",
    "ENV_ROOT",
    "Area",
    "BackendFailed",
    "Capabilities",
    "CowError",
    "LeaseHeld",
    "PathRefused",
    "RootInvalid",
    "Run",
    "SpaceEstimate",
    "SpaceRefused",
    "detect",
    "open_run",
]
