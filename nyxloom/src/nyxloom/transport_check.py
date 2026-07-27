"""Probe the docker transport for the truncation-and-forged-exit failure mode.

WHY THIS EXISTS (canonical `reference/LESSONS.md` L18 defeat 4). A gate process
can be entirely correct -- right image, right commit, real test run -- while the
*channel* that carries its stdout and exit code back to the caller silently
truncates the output and hands back the wrong exit code. The observed instance:
a devcontainer reaches dockerd through a `socat` relay started without `-t`, so
socat's 0.5s default half-close timeout tears the stream down mid-run. An
attached `docker run` then returns only the first fraction of a second of output
and may report exit 0 for a container that exited non-zero.

Nothing *inside* the container can see this -- it is a property of the plumbing
between the daemon and the client -- so no amount of hardening in the gate helps.
The only defense is to probe the transport itself and refuse to trust a gate
verdict carried over a lying one.

The measured asymmetry decides how the probe judges: the TRUNCATION is
deterministic, the exit-code corruption is NOT (a poisoned relay truncated a
sentinel yet still returned the right code). So the probe judges on OUTPUT --
does a line emitted AFTER a real delay make it back? -- never on the exit code.

The probe deliberately uses the ATTACHED `docker run` form, because that is the
form a naive gate uses and the form a lying relay truncates. A robust harness
(``docker run -d`` -> ``docker wait`` -> ``docker logs``) is immune and would
hide the very fault we are trying to surface.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Literal

from .log import get_logger

log = get_logger("transport")

# The default probe image. Any image exercises the SAME transport, so a tiny
# one is ideal. Overridable per host for air-gapped/registry-mirror setups via
# the env var, so a host that cannot reach docker.io can still run the check
# against an image it already has.
DEFAULT_PROBE_IMAGE = "busybox"
PROBE_IMAGE_ENV_VAR = "NYXLOOM_TRANSPORT_PROBE_IMAGE"

# The two sentinel markers. The first is emitted immediately; the second only
# after a real delay, so a transport that drops the stream mid-run loses the
# second while keeping the first -- exactly the truncation signature.
_MARK_EARLY = "NYXLOOM_TRANSPORT_EARLY"
_MARK_LATE = "NYXLOOM_TRANSPORT_LATE"

# The delay must comfortably exceed socat's 0.5s default half-close timeout;
# 3s leaves ample margin without making the probe feel slow.
_DEFAULT_DELAY_S = 3
_DEFAULT_TIMEOUT_S = 60

TransportStatus = Literal["healthy", "lying", "unavailable"]


@dataclass(frozen=True)
class TransportProbe:
    """Result of a single transport probe.

    ``status``:
      * ``"healthy"``     -- both markers returned across the delay; the
                             attached stream relays full output. Trustworthy.
      * ``"lying"``       -- the early marker returned but the late one did NOT.
                             The transport truncates mid-stream; any gate
                             verdict carried over it is worthless. THE dangerous
                             case, and the only one that should block.
      * ``"unavailable"`` -- the probe could not run at all (no docker CLI,
                             daemon unreachable, image missing/unpullable, or a
                             timeout). NOT a transport fault -- callers should
                             SKIP rather than block, so an offline host or a
                             missing probe image never manufactures a failure.
    """

    status: TransportStatus
    detail: str

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"

    @property
    def lying(self) -> bool:
        return self.status == "lying"


def probe_docker_transport(
    image: str,
    *,
    delay_s: int = _DEFAULT_DELAY_S,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    docker_bin: str = "docker",
) -> TransportProbe:
    """Run one attached-stream sentinel and classify the transport.

    *image* must be a locally-available (or pullable) image with a POSIX ``sh``;
    any image exercises the SAME transport, so a tiny one (e.g. ``busybox``) is
    the usual choice. A missing/unpullable image yields ``"unavailable"``, never
    a false ``"lying"``.
    """
    script = f"echo {_MARK_EARLY}; sleep {delay_s}; echo {_MARK_LATE}"
    argv = [docker_bin, "run", "--rm", image, "sh", "-c", script]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s
        )
    except FileNotFoundError:
        return TransportProbe("unavailable", f"{docker_bin!r} not found on PATH")
    except subprocess.TimeoutExpired:
        # A timeout is ambiguous (could be a hung transport) but is NOT the
        # deterministic truncation signature, so we do not block on it.
        return TransportProbe(
            "unavailable",
            f"transport probe timed out after {timeout_s}s (docker slow or hung)",
        )

    out = (proc.stdout or "") + (proc.stderr or "")
    has_early = _MARK_EARLY in out
    has_late = _MARK_LATE in out

    if has_early and has_late:
        return TransportProbe(
            "healthy", "attached stream relayed full output across a delay"
        )
    if has_early and not has_late:
        # The signature. Judge on OUTPUT, never the exit code (which the same
        # fault corrupts non-deterministically).
        return TransportProbe(
            "lying",
            "docker attached-stream TRUNCATION: the early marker returned but "
            f"the marker emitted after a {delay_s}s delay did NOT -- the docker "
            "transport (e.g. a socat relay started without -t) drops output "
            "mid-stream and can forge exit codes. Every containerised gate "
            "verdict from here is untrustworthy until the transport is fixed "
            "(see reference/LESSONS.md L18 defeat 4).",
        )
    # Neither marker: the probe never really ran (image error, daemon down).
    stderr = (proc.stderr or "").strip().splitlines()
    tail = stderr[-1] if stderr else f"exit {proc.returncode}, no output"
    return TransportProbe(
        "unavailable",
        f"transport probe produced no sentinel output ({tail}) -- docker "
        f"unreachable or image {image!r} unavailable; skipping the check",
    )


def probe_default(**kwargs) -> TransportProbe:
    """Probe using the default (or env-overridden) image. The single entry
    point callers should use, so the image choice stays consistent across the
    gate-verify preflight and ``nyxloom doctor``."""
    image = os.environ.get(PROBE_IMAGE_ENV_VAR, DEFAULT_PROBE_IMAGE)
    return probe_docker_transport(image, **kwargs)
