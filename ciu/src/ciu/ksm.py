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
import json
import os
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

# S15.20 — the exec-wrapper: a small program that calls
# prctl(PR_SET_MEMORY_MERGE) and then execve()s the real entrypoint. Unlike the
# LD_PRELOAD shim it needs no dynamic loader in the target, so it reaches
# statically-linked binaries the shim cannot. Measured: the flag SURVIVES
# execve, which is what makes the whole approach work.
#
# Built with the SAME build/verify/cache machinery as the shim, with one
# difference: this is an ordinary executable, so it may link libc freely. The
# shim's zero-DT_NEEDED rule exists because it must load inside SOMEONE ELSE'S
# process under either libc; the wrapper only has to RUN.
WRAPPER_SOURCE_NAME = "ksm-wrapper.c"
WRAPPER_TARGET = "/opt/ksm/ksm-exec"


class KsmBuildError(RuntimeError):
    """A configuration/environment error while building the shim (exit 2/3)."""


def shim_source_path() -> Path:
    """Absolute path to the shipped shim source."""
    return Path(__file__).resolve().parent / "data" / SHIM_SOURCE_NAME


def wrapper_source_path() -> Path:
    """Absolute path to the shipped exec-wrapper source (S15.20)."""
    return Path(__file__).resolve().parent / "data" / WRAPPER_SOURCE_NAME


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


def wrapper_cache_path(repo_root: Path, source: Path | None = None) -> Path:
    """Where the built exec-wrapper lives for this checkout (S15.20).

    Same arch+digest keying, and the same reason: an x86-64 binary silently
    reused on aarch64, or a stale artifact that still RUNS after the source
    changed, are both silent-wrong-answers.
    """
    src = source or wrapper_source_path()
    key = f"{platform.machine()}-{_source_digest(src)}"
    return repo_root / MACHINE_DIR / KSM_SUBDIR / f"ksm-exec-{key}"


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


def _verify_wrapper(path: Path) -> None:
    """Refuse a wrapper that is not a non-empty, executable ELF.

    Deliberately does NOT apply the shim's zero-DT_NEEDED rule: that exists
    because the shim must load inside another process under either libc. The
    wrapper only has to RUN, so linking libc is fine and forbidding it would
    reject every correct build.
    """
    if not path.is_file():
        raise KsmBuildError(f"[S15.20] build produced no file at {path}")
    if path.stat().st_size == 0:
        raise KsmBuildError(f"[S15.20] build produced an EMPTY file at {path}")
    if path.read_bytes()[:4] != b"\x7fELF":
        raise KsmBuildError(
            f"[S15.20] {path} is not an ELF executable — refusing to cache an "
            "artifact that cannot be the container's entrypoint. A bad wrapper "
            "does not degrade the container, it stops it starting."
        )
    # The exec bit is CHECKED, not set. gcc already emits 0755, and the artifact
    # is owned by root under DooD (the build container created it), so chmod
    # here raises EPERM on exactly the setup this is built for. Verify the
    # property; do not assume it, and do not try to impose it.
    if not os.access(path, os.X_OK):
        raise KsmBuildError(
            f"[S15.20] {path} is not executable. It is bind-mounted as the "
            "container's entrypoint, so a non-executable artifact stops the "
            "container starting."
        )


def build_wrapper(
    repo_root: Path,
    physical_root: Path | str,
    *,
    force: bool = False,
) -> Path:
    """Build the exec-wrapper into ``.ciu/ksm/`` and return its path (S15.20).

    Mirrors :func:`build` exactly — same container toolchain, same cache
    location and keying, same verify-before-use and delete-on-failure rules.
    See that function for why the cache must live under the repo root
    (to_physical_path only translates paths beneath it).
    """
    source = wrapper_source_path()
    if not source.is_file():
        raise KsmBuildError(
            f"[S15.20] CIU's wrapper source is missing at {source} — the "
            "installed package is incomplete (package data not installed?)."
        )

    target = wrapper_cache_path(repo_root, source)
    if target.is_file() and not force:
        _verify_wrapper(target)
        return target

    if shutil.which("docker") is None:
        raise KsmBuildError(
            "[S15.20] docker is required to build the KSM exec-wrapper (the "
            f"build runs in {BUILDER_IMAGE}, so the host needs no toolchain). "
            'Install docker, or use ksm = "preload" instead.'
        )

    cache_dir = target.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, cache_dir / WRAPPER_SOURCE_NAME)
    physical_cache = Path(physical_root) / cache_dir.resolve().relative_to(
        Path(repo_root).resolve()
    )

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{physical_cache}:/src",
        "-w", "/src",
        BUILDER_IMAGE,
        "gcc", "-O2", "-static", "-o", target.name, WRAPPER_SOURCE_NAME,
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise KsmBuildError(
            f"[S15.20] wrapper build timed out after {BUILD_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise KsmBuildError(f"[S15.20] could not run docker: {exc}") from exc

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        raise KsmBuildError(
            f"[S15.20] wrapper build failed (docker exit {res.returncode}). "
            f"Output:\n{detail}"
        )

    try:
        _verify_wrapper(target)
    except KsmBuildError:
        target.unlink(missing_ok=True)
        raise
    finally:
        (cache_dir / WRAPPER_SOURCE_NAME).unlink(missing_ok=True)
    return target


# ---------------------------------------------------------------------------
# S15.20 — entrypoint discovery + drift detection
# ---------------------------------------------------------------------------

def image_entrypoint(image: str) -> list[str] | None:
    """The image's declared ENTRYPOINT as a list, or None if it has none.

    Returns None for BOTH "no entrypoint" and "cannot inspect" — the caller
    must distinguish them, because they demand opposite actions: no-entrypoint
    is the easy wrapping case, cannot-inspect means refuse.
    """
    try:
        res = subprocess.run(
            ["docker", "image", "inspect", image,
             "--format", "{{json .Config.Entrypoint}}"],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    raw = (res.stdout or "").strip()
    if raw in ("", "null"):
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return [str(v) for v in value] if isinstance(value, list) else None


def wrapper_entrypoint(image: str) -> list[str]:
    """The compose `entrypoint:` that runs *image* under the exec-wrapper.

    Compose's ``entrypoint:`` REPLACES the image's ENTRYPOINT — there is no
    prepend directive — so wrapping means re-stating the original after the
    wrapper. Measured behaviour of the alternatives:

    - ``[wrapper]`` alone WORKS only when the image declares no ENTRYPOINT
      (its CMD then flows through as the wrapper's args). For an image that
      DOES declare one it fails outright, ``execvp`` on the first CMD token —
      CMD is arguments, not a program.
    - ``[wrapper, *original]`` works in both cases.

    Raises KsmBuildError when the image cannot be inspected: re-stating an
    entrypoint we could not read would either drop the original (container
    never starts) or invent one. Neither is acceptable for a memory
    optimisation, so this refuses instead.
    """
    original = image_entrypoint(image)
    if original is None:
        raise KsmBuildError(
            f'[S15.20] cannot read the ENTRYPOINT of image {image!r}, so the '
            "exec-wrapper cannot re-state it. Pull the image first, or use "
            'ksm = "preload" for this service. (Guessing an entrypoint would '
            "stop the container starting — a memory optimisation must never "
            "risk that.)"
        )
    return [WRAPPER_TARGET, *original]


def entrypoint_fingerprint(image: str) -> str:
    """A stable record of *image*'s ENTRYPOINT, for drift detection.

    Wrapping FREEZES the discovered entrypoint into rendered compose. If the
    image is later rebuilt with a different one, the rendered file keeps
    invoking the old — a stale literal standing in for a fact that lives
    elsewhere, failing silently. Recording the fingerprint is what lets a
    deploy-time check catch that instead of trusting the frozen copy.

    KNOWN LIMIT, stated because it is invisible otherwise: this compares the
    entrypoint ARRAY only. An image whose entrypoint is `["/entrypoint.sh"]`
    and whose *script contents* change is byte-identical here and will not be
    detected. The array is what we froze, so the array is what we can verify.
    """
    original = image_entrypoint(image)
    if original is None:
        return ""
    return json.dumps(original, separators=(",", ":"))


def check_entrypoint_drift(image: str, recorded: str) -> tuple[bool, str]:
    """``(ok, message)`` — has *image*'s ENTRYPOINT changed since *recorded*?

    ``ok=False`` means DRIFT: the compose file re-states an entrypoint the image
    no longer has. Also False when the image cannot be inspected — an
    unverifiable claim must never read as a verified one (the CIU-15 lesson).
    """
    if not recorded:
        return True, "no recorded entrypoint — nothing to compare"
    current = entrypoint_fingerprint(image)
    if not current:
        return False, (
            f"[S15.20] cannot inspect {image!r} to verify its entrypoint has "
            "not drifted since render; refusing to assume it is unchanged"
        )
    if current != recorded:
        return False, (
            f"[S15.20] ENTRYPOINT DRIFT for {image!r}: rendered compose "
            f"re-states {recorded}, but the image now declares {current}. "
            "Re-render (`ciu render`) so the wrapper invokes the real "
            "entrypoint — the deployed one would run the OLD command."
        )
    return True, "entrypoint unchanged since render"
