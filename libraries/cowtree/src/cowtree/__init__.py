"""Small stdlib-only copy-on-write workspace adapter."""
from .cache import (
    Cache,
    CacheGcItem,
    Lease,
    Published,
    validate_key,
)
from .detect import ENV_MODE, ENV_ROOT, Area, Capabilities, detect
from .errors import (
    BackendFailed,
    CacheBusy,
    KeyMissing,
    TreeInvalid,
    Unrepairable,
    CowError,
    LeaseHeld,
    PathRefused,
    RootInvalid,
    SpaceRefused,
)
from .overlay import (
    OverlayVolume,
    WorkerDirs,
    create_worker,
    overlay_volume,
    remove_token,
    remove_worker,
    repair_entry,
    reset_worker,
    reuse_worker,
    upper_full,
    worker_volume,
)
from .provision import Decision, LevelUnavailable, Probes, Provisioner, Settings, WorkerSlot, decide
from .run import Run, open_run
from .space import SpaceEstimate

API_VERSION = 1

__all__ = [
    "Decision",
    "LevelUnavailable",
    "Probes",
    "Provisioner",
    "Settings",
    "WorkerSlot",
    "decide",
    "Cache",
    "CacheBusy",
    "CacheGcItem",
    "KeyMissing",
    "Lease",
    "OverlayVolume",
    "Published",
    "TreeInvalid",
    "Unrepairable",
    "WorkerDirs",
    "create_worker",
    "overlay_volume",
    "remove_token",
    "remove_worker",
    "repair_entry",
    "reset_worker",
    "reuse_worker",
    "upper_full",
    "validate_key",
    "worker_volume",
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
