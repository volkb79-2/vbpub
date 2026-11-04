"""Create and verify VCDIFF patches with the external xdelta3 executable."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Callable


class DeltaError(RuntimeError):
    """The xdelta executable or a generated delta failed validation."""


VERSION_RE = re.compile(r"^Xdelta version ([0-9]+\.[0-9]+\.[0-9]+)", re.MULTILINE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_xdelta(executable: str | Path | None = None) -> tuple[str, str]:
    candidate = str(executable) if executable is not None else shutil.which("xdelta3")
    if not candidate:
        raise DeltaError(
            "xdelta3 was not found; install xdelta3 3.x and make it available on PATH"
        )
    resolved = shutil.which(candidate) if not Path(candidate).is_file() else candidate
    if resolved is None:
        raise DeltaError(f"xdelta3 executable does not exist: {candidate}")
    try:
        result = subprocess.run(
            [resolved, "-V"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeltaError(f"could not run xdelta3 executable {resolved!r}: {exc}") from exc
    match = VERSION_RE.search(result.stdout + result.stderr)
    if result.returncode != 0 or match is None:
        detail = (result.stderr or result.stdout).strip()
        raise DeltaError(
            f"xdelta3 preflight failed for {resolved!r} with exit status {result.returncode}: {detail}"
        )
    return str(Path(resolved).resolve()), match.group(1)


def _run(argv: list[str], *, phase: str) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DeltaError(f"could not start xdelta3 during {phase}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 6000:
            detail = detail[-6000:]
        raise DeltaError(
            f"xdelta3 {phase} failed with exit status {result.returncode}: {detail}"
        )


def create_and_verify_delta(
    executable: str,
    source: Path,
    target: Path,
    delta_output: Path,
    *,
    progress: Callable[[str], None] | None = None,
    expected_source_sha256: str | None = None,
) -> dict[str, str | int]:
    """Encode a maximum-compression delta and decode it to verify exact output."""
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    delta_output = delta_output.resolve(strict=False)
    if not source.is_file() or not target.is_file():
        raise DeltaError("xdelta source and target must be regular files")
    if source == target or source == delta_output or target == delta_output:
        raise DeltaError("xdelta source, target, and delta output paths must differ")
    if delta_output.exists():
        raise DeltaError(f"delta output already exists: {delta_output}")
    if not delta_output.parent.is_dir():
        raise DeltaError(f"delta output directory does not exist: {delta_output.parent}")

    executable, version = resolve_xdelta(executable)
    source_sha256 = sha256_file(source)
    if expected_source_sha256 is not None and source_sha256 != expected_source_sha256.lower():
        raise DeltaError(
            "xdelta source ISO changed after its initial verification: "
            f"expected {expected_source_sha256.lower()}, got {source_sha256}"
        )
    target_sha256 = sha256_file(target)
    fd, staged_name = tempfile.mkstemp(prefix=f".{delta_output.name}.xdelta-", dir=delta_output.parent)
    os.close(fd)
    staged_delta = Path(staged_name)
    staged_delta.unlink()
    try:
        if progress is not None:
            progress(f"Encoding VCDIFF delta with xdelta3 {version} (maximum compression).")
        _run(
            [
                executable,
                "-q",
                "-e",
                "-9",
                "-S",
                "lzma",
                "-s",
                str(source),
                str(target),
                str(staged_delta),
            ],
            phase="encoding",
        )
        if not staged_delta.is_file() or staged_delta.stat().st_size == 0:
            raise DeltaError("xdelta3 reported success but did not create a non-empty delta")
        with tempfile.TemporaryDirectory(prefix=".xdelta-verify-", dir=delta_output.parent) as temp_name:
            decoded = Path(temp_name) / "decoded.iso"
            if progress is not None:
                progress("Applying the generated delta locally and checking the reconstructed ISO hash.")
            _run(
                [
                    executable,
                    "-q",
                    "-d",
                    "-s",
                    str(source),
                    str(staged_delta),
                    str(decoded),
                ],
                phase="verification decode",
            )
            if not decoded.is_file() or decoded.stat().st_size != target.stat().st_size:
                raise DeltaError("decoded delta has the wrong output length")
            decoded_sha256 = sha256_file(decoded)
            if decoded_sha256 != target_sha256:
                raise DeltaError(
                    "generated delta does not reconstruct the patched ISO: "
                    f"expected {target_sha256}, got {decoded_sha256}"
                )
            if sha256_file(source) != source_sha256:
                raise DeltaError("xdelta source ISO changed while the delta was being verified")
            if sha256_file(target) != target_sha256:
                raise DeltaError("patched ISO changed while the delta was being verified")
        try:
            os.link(staged_delta, delta_output)
        except FileExistsError as exc:
            raise DeltaError(f"delta output appeared during encoding; refusing to overwrite: {delta_output}") from exc
        return {
            "xdelta_version": version,
            "delta_bytes": delta_output.stat().st_size,
            "delta_sha256": sha256_file(delta_output),
            "target_bytes": target.stat().st_size,
            "target_sha256": target_sha256,
        }
    finally:
        staged_delta.unlink(missing_ok=True)
