"""Identity checks for the installed CMRU release controller."""
from __future__ import annotations

import hashlib
import json
import re
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Mapping


_SHA256 = re.compile(r"[0-9a-f]{64}")


def installed_release_controller_identity() -> Mapping[str, str]:
    """Return the identity of the installed, intact, non-editable CMRU wheel."""
    try:
        installed = distribution("cmru")
    except PackageNotFoundError as exc:
        raise RuntimeError("CMRU release requires an installed CMRU wheel") from exc

    files = installed.files
    if not files:
        raise RuntimeError("CMRU distribution has no installed file manifest")
    try:
        installed_module = Path(installed.locate_file("cmru/cli.py")).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"cannot resolve the installed CMRU CLI module: {exc}") from exc

    direct_url_text = installed.read_text("direct_url.json")
    if direct_url_text is None:
        raise RuntimeError(
            "CMRU release requires wheel provenance in direct_url.json; pip records it "
            "only for a direct requirement, so reinstall from the wheel file itself "
            "(`pip install --no-deps --force-reinstall ./cmru-<version>-py3-none-any.whl`)"
        )
    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CMRU direct_url.json is malformed") from exc
    if not isinstance(direct_url, dict):
        raise RuntimeError("CMRU direct_url.json is malformed")
    directory = direct_url.get("dir_info")
    archive = direct_url.get("archive_info")
    hashes = archive.get("hashes") if isinstance(archive, dict) else None
    wheel_url = direct_url.get("url")
    wheel_sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
    legacy_hash = archive.get("hash") if isinstance(archive, dict) else None
    if wheel_sha256 is None and isinstance(legacy_hash, str):
        algorithm, separator, value = legacy_hash.partition("=")
        if algorithm == "sha256" and separator:
            wheel_sha256 = value
    elif (
        isinstance(legacy_hash, str)
        and legacy_hash.startswith("sha256=")
        and wheel_sha256 != legacy_hash.removeprefix("sha256=")
    ):
        raise RuntimeError("CMRU wheel provenance contains conflicting SHA-256 digests")
    if isinstance(directory, dict) and directory.get("editable") is True:
        raise RuntimeError("CMRU release refuses an editable CMRU installation")
    if (
        not isinstance(wheel_url, str)
        or not wheel_url.split("?", 1)[0].split("#", 1)[0].lower().endswith(".whl")
        or not isinstance(wheel_sha256, str)
        or _SHA256.fullmatch(wheel_sha256) is None
    ):
        raise RuntimeError(
            "CMRU release requires a non-editable wheel install with a recorded SHA-256"
        )

    digest = hashlib.sha256()
    for item in sorted(files, key=str):
        path = Path(installed.locate_file(item))
        try:
            if not path.is_file():
                raise RuntimeError(f"installed CMRU file is missing: {item}")
            digest.update(str(item).encode("utf-8", "surrogateescape"))
            digest.update(b"\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise RuntimeError(f"cannot read installed CMRU file {item}: {exc}") from exc

    return {
        "version": installed.version,
        "wheel_sha256": wheel_sha256,
        "distribution_sha256": digest.hexdigest(),
        "module_path": str(installed_module),
    }


def release_controller_identity(module_path: Path) -> Mapping[str, str]:
    """Require the active release controller to be the installed CMRU wheel."""
    identity = installed_release_controller_identity()
    try:
        running_module = module_path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"cannot resolve the active CMRU CLI module: {exc}") from exc
    installed_module = Path(identity["module_path"])
    if running_module != installed_module:
        raise RuntimeError(
            "CMRU release must run from its installed wheel; the active CLI module "
            f"is {running_module}, while the distribution module is {installed_module}"
        )
    return identity
