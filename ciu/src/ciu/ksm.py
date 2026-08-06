"""S15.17 — build and cache the KSM opt-in shim (CIU-17).

WHY THIS EXISTS
---------------
S15.11's ``governance.ksm_optin`` takes a path to a shim that the CONSUMER must
produce. Every consuming repo therefore carries its own copy of the same tiny
ELF object, built by hand from a README recipe, and CIU-14 is what happens when
one of them is missing: Docker silently bind-mounts an empty directory in its
place and KSM opt-in contributes nothing, with no error outside container-
internal ``ld.so`` stderr.

CIU ships the SOURCE and builds it on demand instead. The artifact is a build
product, so it belongs in ``.ciu/`` — the machine-owned artifact dir (S1.6),
already gitignored, already excluded from the tree CIU treats as authored.

WHY NOT A HOST-LEVEL CACHE (``$XDG_CACHE_HOME``)
-----------------------------------------------
Tempting — one build per host, shared by every worktree — and wrong here. The
shim's path becomes a Docker BIND SOURCE, so it is translated by
``to_physical_path`` (S1.3/S1.4), which maps paths under the repo root into the
daemon's namespace and passes everything else through UNCHANGED. A cache under
``~/.cache`` inside a devcontainer would pass through untranslated and address a
path that does not exist on the host — Docker would then create it as an empty
directory, which is CIU-14 again, from a different direction. Under the repo
root the translation is correct by construction.

The cost is one build per checkout. It is ~1s of gcc in a container that is
almost always already pulled, and it is paid once per worktree, not per run.

VERIFICATION IS PART OF THE BUILD
---------------------------------
The shim must be DEPENDENCY-FREE (``-nostdlib``, zero ``DT_NEEDED``). One ``.so``
has to preload under BOTH libcs, and a libc-linked shim is FATAL under the other
libc's loader — glibc's ``ld.so`` exits 127 on a musl-linked object (measured,
see the consumer's KSM measurement doc). So a build whose output still declares
a dynamic dependency is not a degraded shim, it is a container that will not
start. It must never reach the cache.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
from pathlib import Path

from .config_constants import MACHINE_DIR

KSM_SUBDIR = "ksm"
BUILDER_IMAGE = "gcc:13-bookworm"
BUILD_TIMEOUT_SECONDS = 300

# The shim source ships with CIU (package data). Keeping it here rather than in
# each consumer repo is the whole point: one source of truth for an artifact
# whose correctness is subtle (see the module docstring on -nostdlib).
SHIM_SOURCE_NAME = "ksm-optin.c"


class KsmBuildError(RuntimeError):
    """A configuration/environment error while building the shim (exit 2/3)."""


def shim_source_path() -> Path:
    """Absolute path to the shipped shim source."""
    return Path(__file__).resolve().parent / "data" / SHIM_SOURCE_NAME


def _source_digest(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()[:16]


def cache_path(repo_root: Path, source: Path | None = None) -> Path:
    """Where the built shim lives for this checkout.

    Keyed by ``<machine>-<source-digest>``:

    - **machine** (``uname -m``) because silently reusing an x86-64 object on
      aarch64 is the same class of silent-wrong-answer this module exists to
      remove.
    - **source digest** so a CIU upgrade that changes the shim invalidates the
      cache without anyone having to remember to clear it. A stale artifact that
      still *loads* is the worst outcome: it works, and it is not what the
      current source says.
    """
    src = source or shim_source_path()
    key = f"{platform.machine()}-{_source_digest(src)}"
    return repo_root / MACHINE_DIR / KSM_SUBDIR / f"ksm-optin-{key}.so"


def _dt_needed_count(so_path: Path) -> int | None:
    """Number of ``DT_NEEDED`` entries in *so_path*, or None if unknowable.

    Reads the dynamic section directly rather than shelling out to ``readelf``,
    which is not present in every environment CIU runs in. Returning None means
    "could not verify" — the caller treats that as a refusal, never as a pass:
    an unverifiable artifact and a verified-good one must not be conflated.
    """
    try:
        blob = so_path.read_bytes()
    except OSError:
        return None
    if len(blob) < 64 or blob[:4] != b"\x7fELF":
        return None
    is_64 = blob[4] == 2
    little = blob[5] == 1
    if not is_64:
        return None  # 32-bit unsupported; refuse rather than guess
    endian = "little" if little else "big"

    def u(off: int, size: int) -> int:
        return int.from_bytes(blob[off:off + size], endian)  # type: ignore[arg-type]

    e_phoff, e_phentsize, e_phnum = u(32, 8), u(54, 2), u(56, 2)
    PT_DYNAMIC, DT_NEEDED, DT_NULL = 2, 1, 0
    for i in range(e_phnum):
        ph = e_phoff + i * e_phentsize
        if ph + e_phentsize > len(blob):
            return None
        if u(ph, 4) != PT_DYNAMIC:
            continue
        off, size = u(ph + 8, 8), u(ph + 32, 8)
        count = 0
        for j in range(0, size, 16):
            pos = off + j
            if pos + 16 > len(blob):
                return None
            tag = u(pos, 8)
            if tag == DT_NULL:
                return count
            if tag == DT_NEEDED:
                count += 1
        return count
    return 0  # no PT_DYNAMIC at all -> no dynamic dependencies


def _verify(so_path: Path) -> None:
    """Refuse anything that is not a non-empty, dependency-free ELF object."""
    if not so_path.is_file():
        raise KsmBuildError(f"[S15.17] build produced no file at {so_path}")
    if so_path.stat().st_size == 0:
        raise KsmBuildError(f"[S15.17] build produced an EMPTY file at {so_path}")
    needed = _dt_needed_count(so_path)
    if needed is None:
        raise KsmBuildError(
            f"[S15.17] cannot verify {so_path} is dependency-free (unreadable or "
            "not a 64-bit ELF object). Refusing to cache an unverifiable shim: a "
            "libc-linked shim is FATAL under the other libc's loader, so an "
            "unchecked artifact is a container that will not start, not a "
            "degraded one."
        )
    if needed:
        raise KsmBuildError(
            f"[S15.17] built shim declares {needed} DT_NEEDED entr"
            f"{'y' if needed == 1 else 'ies'}; it MUST be dependency-free "
            "(-nostdlib). A libc-linked shim is FATAL under the other libc's "
            "loader (glibc ld.so exits 127 on a musl-linked object)."
        )


def build(
    repo_root: Path,
    physical_root: Path | str,
    *,
    force: bool = False,
) -> Path:
    """Build the shim into this checkout's ``.ciu/ksm/`` and return its path.

    *physical_root* is the repo root as the DOCKER DAEMON sees it (S1.3/S1.4) —
    required, not optional, and not defaulted. The build's ``-v`` source must be
    a daemon-visible path; passing the container-local one would make docker
    create a phantom empty directory and mount that, so the build would write
    its output somewhere the caller cannot see and "succeed" with nothing. That
    is CIU-14's failure inverted, and it is why this parameter has no fallback.

    Returns the cached artifact untouched when one already exists for this
    (machine, source) pair, unless *force*. Raises `KsmBuildError` on every
    failure — it never returns a path that has not passed `_verify`.
    """
    source = shim_source_path()
    if not source.is_file():
        raise KsmBuildError(
            f"[S15.17] CIU's shim source is missing at {source} — the installed "
            "package is incomplete (package data not installed?)."
        )

    target = cache_path(repo_root, source)
    if target.is_file() and not force:
        _verify(target)  # a cached artifact still has to be good
        return target

    if shutil.which("docker") is None:
        raise KsmBuildError(
            "[S15.17] docker is required to build the KSM shim (the build runs "
            f"in {BUILDER_IMAGE}, so the host itself needs no toolchain). "
            'Install docker, or set governance.ksm_optin = "" to disable KSM '
            "opt-in."
        )

    cache_dir = target.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, cache_dir / SHIM_SOURCE_NAME)

    # The daemon-visible path of the cache dir. Derived from physical_root by
    # the same relative offset the logical path has from repo_root — the cache
    # lives UNDER the repo root precisely so this translation is well-defined
    # (see the module docstring on why a ~/.cache location would not be).
    physical_cache = Path(physical_root) / cache_dir.resolve().relative_to(
        Path(repo_root).resolve()
    )

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{physical_cache}:/src",
        "-w", "/src",
        BUILDER_IMAGE,
        "gcc", "-shared", "-fPIC", "-nostdlib", "-O2",
        "-o", target.name, SHIM_SOURCE_NAME,
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise KsmBuildError(
            f"[S15.17] shim build timed out after {BUILD_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise KsmBuildError(f"[S15.17] could not run docker: {exc}") from exc

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise KsmBuildError(
            f"[S15.17] shim build failed (docker exit {res.returncode}). "
            f"Image {BUILDER_IMAGE} may be unavailable (offline?). Output:\n"
            f"{detail}"
        )

    # Verify BEFORE the artifact is considered usable. A build that produced a
    # libc-linked object must not be cached: it would load fine under one libc
    # and kill the container under the other.
    try:
        _verify(target)
    except KsmBuildError:
        target.unlink(missing_ok=True)  # never leave a bad artifact cached
        raise
    finally:
        (cache_dir / SHIM_SOURCE_NAME).unlink(missing_ok=True)
    return target
