"""Report whether TrueNAS has a KVM or QEMU TCG VM backend available."""

from __future__ import annotations

import platform
import shutil

from truenas_pylibvirt.utils import kvm_supported


def virtualization_backend() -> str | None:
    """Return the backend selected by TrueNAS, or ``None`` if none is usable.

    TrueNAS's VM XML generator uses KVM when ``/dev/kvm`` exists and QEMU TCG
    otherwise. Check the architecture-specific QEMU binary before advertising
    the TCG path through middleware.
    """
    if kvm_supported():
        return "KVM"

    if shutil.which(f"qemu-system-{platform.machine()}"):
        return "TCG"

    return None
