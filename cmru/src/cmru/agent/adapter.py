"""ProjectAdapter ABC + load_adapter() — Seam 2 (spec §3.1 / §5).

The adapter is shipped INSIDE the verified dstdns release and loaded from
the installed release root via its entrypoint.  cmru NEVER imports a
project module directly — it invokes the entrypoint as a subprocess or loads
it from the trusted, signature-verified release root.

The ABC here is the contract that all adapters must satisfy.  Downstream
projects (e.g. dstdns SPEC F) implement concrete adapters conforming to this.
"""
from __future__ import annotations

import abc
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    success: bool
    exit_code: int
    message: str = ""   # human-readable; callers MUST redact before logging


@dataclass
class HealthResult:
    status: str         # "healthy" | "degraded" | "failed"
    message: str = ""   # human-readable; callers MUST redact before logging


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class ProjectAdapter(abc.ABC):
    """Generic adapter interface.  No dstdns topology knowledge belongs here.

    The adapter receives only the data it needs:
    - desired state as passed from the reconciler
    - the installed release root (where the project files live)
    - the previous release root (for rollback)

    IMPORTANT: apply_step dispatches only to ENUMERATED adapter actions.
    There is no shell-command or arbitrary-argv field anywhere in the protocol.
    """

    @abc.abstractmethod
    def validate(self, desired: Any, installed_release: Path) -> None:
        """Validate that the desired state is applicable given the installed release.
        Raise ValueError if not.
        """
        ...

    @abc.abstractmethod
    def prepare(self, desired: Any, release_root: Path) -> None:
        """Prepare the host for the new release.
        This includes any pre-network transport-join required for first bring-up.
        """
        ...

    @abc.abstractmethod
    def apply_step(self, step: Any) -> StepResult:
        """Execute one plan step (e.g. ciu up --profile ...).
        The step object is opaque data from the desired state — the adapter owns
        the interpretation but MUST NOT execute arbitrary commands from it.
        """
        ...

    @abc.abstractmethod
    def health(self, step: Any) -> HealthResult:
        """Check health of the service after a step; returns HealthResult."""
        ...

    @abc.abstractmethod
    def rollback(self, previous: Any) -> None:
        """Roll back to the previous release."""
        ...


# ---------------------------------------------------------------------------
# Adapter loader
# ---------------------------------------------------------------------------

_ADAPTER_MODULE_NAME = "cmru_adapter"
_ADAPTER_CLASS_NAME = "Adapter"
_ADAPTER_ENTRYPOINT = "scripts/adapter.py"


def load_adapter(release_root: Path) -> ProjectAdapter:
    """Load the ProjectAdapter from the VERIFIED release root.

    The release root must already be signature-verified before calling this
    function (SPEC A installer verifies sha256 + minisign before install).

    Looks for an `Adapter` class in:
      1. <release_root>/scripts/adapter.py
      2. <release_root>/adapter.py
    The class must subclass ProjectAdapter.

    Raises RuntimeError if the adapter cannot be loaded.
    """
    candidates = [
        release_root / "scripts" / "adapter.py",
        release_root / "adapter.py",
    ]
    adapter_path: Optional[Path] = None
    for candidate in candidates:
        if candidate.exists():
            adapter_path = candidate
            break

    if adapter_path is None:
        raise RuntimeError(
            f"No adapter found in release root {release_root}. "
            f"Expected one of: {[str(c) for c in candidates]}"
        )

    spec = importlib.util.spec_from_file_location(_ADAPTER_MODULE_NAME, adapter_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load adapter from {adapter_path}")

    module = importlib.util.module_from_spec(spec)
    # Do NOT pollute sys.modules with the adapter
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        raise RuntimeError(f"Failed to execute adapter {adapter_path}: {exc}") from exc

    cls = getattr(module, _ADAPTER_CLASS_NAME, None)
    if cls is None:
        raise RuntimeError(
            f"Adapter module {adapter_path} does not define class '{_ADAPTER_CLASS_NAME}'"
        )
    if not (isinstance(cls, type) and issubclass(cls, ProjectAdapter)):
        raise RuntimeError(
            f"'{_ADAPTER_CLASS_NAME}' in {adapter_path} does not subclass ProjectAdapter"
        )

    try:
        instance = cls()
    except Exception as exc:
        raise RuntimeError(f"Failed to instantiate adapter class: {exc}") from exc

    return instance
