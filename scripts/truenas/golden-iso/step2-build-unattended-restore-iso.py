#!/usr/bin/env python3
"""Create a Debian Live ISO that restores the TrueNAS golden disk unattended."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime"
EFI_LOADER = r"\EFI\BOOT\BOOTX64.EFI"
EXPECTED_PARTITION_CODES = ["EF02", "EF00", "BF01"]
PARTITION_TYPE_CODES = {
    "21686148-6449-6e6f-744e-656564454649": "EF02",
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "EF00",
    "6a898cc3-1dd2-11b2-99a6-080020736631": "BF01",
}
REQUIRED_TOOLS = {
    "build": [
        "xorriso",
        "unsquashfs",
        "mksquashfs",
        "zstd",
        "sgdisk",
        "mdir",
        "sha512sum",
        "chroot",
        "mount",
        "umount",
    ],
}
GIB_BYTES = 1024**3
MIN_GOLDEN_DISK_BYTES = 16 * GIB_BYTES


class BuildError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"could not run {argv[0]}: {type(exc).__name__}") from None
    if check and result.returncode != 0:
        detail = [
            line
            for line in (result.stdout + "\n" + result.stderr).strip().splitlines()
            if line.strip()
        ]
        suffix = "\n".join(detail[-12:])
        raise BuildError(
            f"{argv[0]} exited {result.returncode}"
            + (f":\n{suffix}" if suffix else "")
        )
    return result


def require_tools(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise BuildError("missing build tools: " + ", ".join(missing))


def validate_webhook(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or not parsed.path.startswith("/hooks/")
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in value)
    ):
        raise BuildError(
            "Mattermost incoming webhook must be a complete HTTPS /hooks/ URL"
        )


def toml_string(value: str) -> str:
    # JSON string escapes are a valid subset of TOML basic-string escapes.
    return json.dumps(value, ensure_ascii=False)


def public_key_from_private(path: Path) -> str:
    if not path.is_file():
        raise BuildError(f"root private key not found: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise BuildError(f"root private key must not be accessible by group/other: {path}")
    result = run(["ssh-keygen", "-y", "-f", str(path)], timeout=30)
    parts = result.stdout.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise BuildError("root SSH private key must be an Ed25519 key")
    return result.stdout.strip()


def initialize_secrets(args: argparse.Namespace) -> None:
    image = args.image.resolve()
    output = args.output.resolve()
    if output.parent != image.parent:
        raise BuildError("the secrets TOML must be a sibling of the raw disk image")
    if output.exists() and not args.force:
        raise BuildError(f"refusing to replace existing secrets file: {output}")

    password_path = args.admin_password_file.resolve()
    private_path = args.root_private_key.resolve()
    public_path = args.root_public_key.resolve()
    webhook_path = args.webhook_file.resolve()
    if output in {image, password_path, private_path, public_path, webhook_path}:
        raise BuildError("secrets TOML output must not replace one of its input files")
    if not password_path.is_file():
        raise BuildError(f"administrator password file not found: {password_path}")
    if stat.S_IMODE(password_path.stat().st_mode) & 0o077:
        raise BuildError("administrator password file must be mode 0600 or stricter")
    password = password_path.read_text(encoding="utf-8").strip()
    if len(password) < 16 or "\n" in password or "\r" in password:
        raise BuildError("administrator password must contain at least 16 characters")

    public_key = public_key_from_private(private_path)
    supplied_public = public_path.read_text(encoding="utf-8").strip()
    if supplied_public.split()[:2] != public_key.split()[:2]:
        raise BuildError("root public key does not match the supplied private key")

    webhook = webhook_path.read_text(encoding="utf-8").strip()
    validate_webhook(webhook)
    private_key = private_path.read_text(encoding="utf-8").strip()
    sidecar = "\n".join(
        [
            "schema_version = 1",
            f"image_file = {toml_string(image.name)}",
            "",
            "[truenas_admin]",
            'username = "truenas_admin"',
            f"password = {toml_string(password)}",
            "",
            "[root_ssh]",
            'username = "root"',
            f"private_key = {toml_string(private_key)}",
            f"public_key = {toml_string(supplied_public)}",
            "",
            "[mattermost]",
            f"webhook_url = {toml_string(webhook)}",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(sidecar)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    log(f"Private sibling secret file created: {output}")


def load_secrets(path: Path, image: Path) -> dict[str, str]:
    if not path.is_file():
        raise BuildError(f"secret TOML not found: {path}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise BuildError("secret TOML must be mode 0600 or stricter")
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BuildError(f"cannot parse secret TOML: {type(exc).__name__}") from None
    if values.get("schema_version") != 1 or values.get("image_file") != image.name:
        raise BuildError("secret TOML does not identify this golden image")
    admin = values.get("truenas_admin", {})
    root_ssh = values.get("root_ssh", {})
    mattermost = values.get("mattermost", {})
    password = admin.get("password")
    private_key = root_ssh.get("private_key")
    public_key = root_ssh.get("public_key")
    webhook = mattermost.get("webhook_url")
    if (
        admin.get("username") != "truenas_admin"
        or not isinstance(password, str)
        or len(password) < 16
        or root_ssh.get("username") != "root"
        or not isinstance(private_key, str)
        or not isinstance(public_key, str)
        or not public_key.startswith("ssh-ed25519 ")
        or not isinstance(webhook, str)
    ):
        raise BuildError("secret TOML is missing expected TrueNAS credentials")
    validate_webhook(webhook)
    with tempfile.TemporaryDirectory(prefix="truenas-secret-check-") as temp:
        private_path = Path(temp) / "root_ed25519"
        private_path.write_text(private_key + "\n", encoding="utf-8")
        os.chmod(private_path, 0o600)
        derived = public_key_from_private(private_path)
    if derived.split()[:2] != public_key.split()[:2]:
        raise BuildError("root SSH public key in secret TOML does not match its private key")
    return {"mattermost_webhook_url": webhook}


def hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha512_from_sums(iso: Path, sums: Path) -> str:
    if not sums.is_file():
        raise BuildError(f"official SHA512SUMS file not found: {sums}")
    expected: str | None = None
    for line in sums.read_text(encoding="ascii", errors="replace").splitlines():
        match = re.match(r"^([0-9a-fA-F]{128})\s+\*?(.+?)\s*$", line)
        if match and Path(match.group(2)).name == iso.name:
            expected = match.group(1).lower()
            break
    if expected is None:
        raise BuildError(f"{iso.name} is not listed in {sums}")
    actual = hash_file(iso, "sha512")
    if actual != expected:
        raise BuildError(
            f"base ISO SHA-512 mismatch: expected {expected}, got {actual}"
        )
    return actual


def verify_signature(args: argparse.Namespace) -> None:
    if bool(args.base_signature) != bool(args.debian_cd_keyring):
        raise BuildError("pass both --base-signature and --debian-cd-keyring")
    if args.base_signature:
        run(
            [
                "gpgv",
                "--keyring",
                str(args.debian_cd_keyring.resolve()),
                str(args.base_signature.resolve()),
                str(args.base_checksums.resolve()),
            ],
            timeout=60,
        )
        log("Debian checksum signature verified with the supplied CD signing keyring.")
    else:
        log("Base ISO checksum matches SHA512SUMS; its detached signature was not checked.")


def inspect_golden_image(image: Path) -> dict[str, object]:
    if not image.is_file():
        raise BuildError(f"golden raw image not found: {image}")
    raw_size = image.stat().st_size
    golden_image_payload_name(raw_size)
    result = run(["sgdisk", "--print", str(image)])
    disk_match = re.search(
        r"Disk identifier \(GUID\):\s*([0-9A-Fa-f-]{36})",
        result.stdout,
    )
    if not disk_match:
        raise BuildError("golden image has no readable GPT disk identifier")

    partitions: list[dict[str, object]] = []
    for number in (1, 2, 3):
        part = run(["sgdisk", "--info", str(number), str(image)])
        patterns = {
            "start": r"First sector:\s*(\d+)",
            "end": r"Last sector:\s*(\d+)",
            "type_code": r"Partition GUID code:\s*([0-9A-Fa-f-]{36})",
            "unique_guid": r"Partition unique GUID:\s*([0-9A-Fa-f-]{36})",
        }
        values: dict[str, str] = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, part.stdout)
            if not match:
                raise BuildError(f"could not inspect GPT partition {number}: {key}")
            values[key] = match.group(1)
        partitions.append(
            {
                "number": number,
                "start": int(values["start"]),
                "end": int(values["end"]),
                "type_code": PARTITION_TYPE_CODES.get(
                    values["type_code"].lower(), values["type_code"].upper()
                ),
                "unique_guid": values["unique_guid"].lower(),
            }
        )
    if [str(p["type_code"]) for p in partitions] != EXPECTED_PARTITION_CODES:
        raise BuildError(
            "golden image must contain BIOS boot, EFI System, and ZFS partitions "
            "in slots 1, 2, and 3"
        )
    verify = run(["sgdisk", "--verify", str(image)])
    if "No problems found" not in verify.stdout:
        raise BuildError("golden image GPT did not pass sgdisk verification")
    if int(partitions[2]["end"]) >= raw_size // 512:
        raise BuildError("ZFS partition extends beyond the raw image")
    esp_offset = int(partitions[1]["start"]) * 512
    esp = run(["mdir", "-i", f"{image}@@{esp_offset}", "-s", "::/EFI/BOOT"])
    if "bootx64" not in esp.stdout.lower():
        raise BuildError("golden EFI partition lacks EFI/BOOT/BOOTX64.EFI")

    return {
        "raw_image_size_bytes": raw_size,
        "logical_sector_size_bytes": 512,
        "disk_guid": disk_match.group(1).lower(),
        "partitions": partitions,
    }


def golden_image_payload_name(raw_size_bytes: int) -> str:
    if (
        isinstance(raw_size_bytes, bool)
        or not isinstance(raw_size_bytes, int)
        or raw_size_bytes < MIN_GOLDEN_DISK_BYTES
        or raw_size_bytes % GIB_BYTES
        or raw_size_bytes % 512
    ):
        raise BuildError(
            "golden image must be a whole-GiB, sector-aligned disk of at least 16 GiB"
        )
    return f"truenas-27-{raw_size_bytes // GIB_BYTES}GiB-golden.raw.zst"


def compress_image(image: Path, output: Path, raw_size: int) -> tuple[str, str]:
    log(f"Compressing {image.name} ({raw_size} bytes) with Zstandard.")
    raw_digest = hashlib.sha256()
    try:
        process = subprocess.Popen(
            [
                "zstd",
                "--long=27",
                "--check",
                "--threads=2",
                "--compress",
                "-12",
                "--stdout",
            ],
            stdin=subprocess.PIPE,
            stdout=output.open("wb"),
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise BuildError(f"could not start zstd: {type(exc).__name__}") from None
    if process.stdin is None or process.stderr is None:
        process.kill()
        raise BuildError("could not open Zstandard input")

    count = 0
    next_report = 10
    try:
        with image.open("rb") as source:
            for chunk in iter(lambda: source.read(16 * 1024 * 1024), b""):
                process.stdin.write(chunk)
                raw_digest.update(chunk)
                count += len(chunk)
                percent = count * 100 // raw_size
                if percent >= next_report:
                    log(f"  compressed input: {percent}%")
                    next_report += 10
        process.stdin.close()
    except Exception:
        process.terminate()
        process.wait(timeout=10)
        raise
    stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
    code = process.wait()
    if code:
        raise BuildError(
            f"zstd compression failed (exit {code})"
            + (f": {stderr[-500:]}" if stderr else "")
        )
    if count != raw_size:
        raise BuildError(f"read {count} bytes from image; expected {raw_size}")
    run(["zstd", "--test", "--quiet", str(output)], timeout=1800)
    return raw_digest.hexdigest(), hash_file(output, "sha256")


def mount_chroot(args: list[str]) -> None:
    run(["mount", *args])


def install_runtime_packages(rootfs: Path, suite: str) -> None:
    apt = rootfs / "etc" / "apt"
    sources = apt / "sources.list"
    sources.parent.mkdir(parents=True, exist_ok=True)
    for item in list((apt / "sources.list.d").glob("*")) if (apt / "sources.list.d").exists() else []:
        if item.is_file() or item.is_symlink():
            item.unlink()
    sources.write_text(
        "\n".join(
            [
                f"deb http://deb.debian.org/debian {suite} main non-free-firmware",
                f"deb http://deb.debian.org/debian {suite}-updates main non-free-firmware",
                f"deb http://security.debian.org/debian-security {suite}-security main non-free-firmware",
                "",
            ]
        ),
        encoding="utf-8",
    )
    resolver = rootfs / "etc" / "resolv.conf"
    original_resolver_link = os.readlink(resolver) if resolver.is_symlink() else None
    original_resolver = (
        resolver.read_bytes()
        if resolver.exists() and not resolver.is_symlink()
        else None
    )
    original_resolver_mode = (
        stat.S_IMODE(resolver.stat().st_mode)
        if resolver.exists() and not resolver.is_symlink()
        else 0o644
    )
    if resolver.exists() or resolver.is_symlink():
        resolver.unlink()
    resolver.write_text(
        Path("/etc/resolv.conf").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    mounted: list[Path] = []
    try:
        for source, target, options in (
            ("/dev", rootfs / "dev", ["--bind", "/dev", str(rootfs / "dev")]),
            ("proc", rootfs / "proc", ["-t", "proc", "proc", str(rootfs / "proc")]),
            ("sysfs", rootfs / "sys", ["-t", "sysfs", "sysfs", str(rootfs / "sys")]),
        ):
            target.mkdir(parents=True, exist_ok=True)
            mount_chroot(options)
            mounted.append(target)
        log("Installing sgdisk, efibootmgr, and eject into the Debian Live root.")
        run(["chroot", str(rootfs), "/usr/bin/apt-get", "update"], timeout=1800)
        run(
            [
                "chroot",
                str(rootfs),
                "/usr/bin/apt-get",
                "install",
                "--yes",
                "--no-install-recommends",
                "gdisk",
                "efibootmgr",
                "eject",
            ],
            timeout=1800,
        )
        run(["chroot", str(rootfs), "/usr/bin/apt-get", "clean"], timeout=300)
    finally:
        failures: list[str] = []
        for target in reversed(mounted):
            result = run(["umount", str(target)], check=False, timeout=60)
            if result.returncode != 0:
                failures.append(str(target))
        if failures:
            raise BuildError("failed to unmount chroot paths: " + ", ".join(failures))
        if resolver.exists() or resolver.is_symlink():
            resolver.unlink()
        if original_resolver_link is not None:
            resolver.symlink_to(original_resolver_link)
        elif original_resolver is not None:
            resolver.write_bytes(original_resolver)
            os.chmod(resolver, original_resolver_mode)
        shutil.rmtree(rootfs / "var" / "lib" / "apt" / "lists", ignore_errors=True)


def write_runtime_files(rootfs: Path, webhook: str) -> None:
    runtime_script = RUNTIME_DIR / "truenas-restore.py"
    service_source = RUNTIME_DIR / "truenas-restore.service"
    for path in (runtime_script, service_source):
        if not path.is_file():
            raise BuildError(f"runtime file not found: {path}")
    validate_runtime_methods(runtime_script)
    destination = rootfs / "usr" / "local" / "lib" / "truenas-unattended-restore"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime_script, destination / runtime_script.name)
    os.chmod(destination / runtime_script.name, 0o755)

    config = rootfs / "etc" / "truenas-unattended-restore"
    config.mkdir(parents=True, exist_ok=True)
    runtime_config = config / "runtime.json"
    runtime_config.write_text(
        json.dumps({"mattermost_webhook_url": webhook}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(runtime_config, 0o600)

    service = rootfs / "etc" / "systemd" / "system" / "truenas-restore.service"
    service.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(service_source, service)
    wants = service.parent / "multi-user.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    link = wants / service.name
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to("../truenas-restore.service")
    verify = run(["systemd-analyze", "verify", str(service)], check=False, timeout=60)
    if verify.returncode != 0:
        raise BuildError(
            "systemd-analyze rejected the restore service: "
            + (verify.stderr or verify.stdout).strip()[-500:]
        )


def validate_runtime_methods(runtime_script: Path) -> None:
    """Fail the build if the runtime calls an undefined Installer method."""
    try:
        tree = ast.parse(runtime_script.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise BuildError(f"cannot parse restore runtime: {type(exc).__name__}") from None
    installer = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Installer"
        ),
        None,
    )
    if installer is None:
        raise BuildError("restore runtime does not define Installer")
    methods = {
        node.name for node in installer.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    unresolved = sorted(
        {
            node.func.attr
            for node in ast.walk(installer)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr not in methods
        }
    )
    if unresolved:
        raise BuildError(
            "restore runtime calls undefined Installer method(s): "
            + ", ".join(unresolved)
        )


def unattended_grub_config(base_iso: Path, work: Path) -> Path:
    """Return a GRUB config that boots Debian Live without operator input."""
    main_config = work / "base-grub.cfg"
    boot_config = work / "unattended-grub-config.cfg"
    for iso_path, output in (
        ("/boot/grub/grub.cfg", main_config),
        ("/boot/grub/config.cfg", boot_config),
    ):
        run(
            [
                "xorriso",
                "-osirrox",
                "on",
                "-indev",
                str(base_iso),
                "-extract",
                iso_path,
                str(output),
            ],
            timeout=120,
        )

    main_text = main_config.read_text(encoding="utf-8")
    first_entry = re.search(
        r"(?m)^menuentry\s+(['\"])(.*?)\1(?:\s|$)", main_text
    )
    if first_entry is None or first_entry.group(2) != "Live system (amd64)":
        raise BuildError(
            "Debian Live GRUB entry zero is not 'Live system (amd64)'; "
            "refusing to build an unattended restore ISO"
        )

    config_text = boot_config.read_text(encoding="utf-8")
    defaults = list(
        re.finditer(r"(?m)^set[ \t]+default[ \t]*=[ \t]*([^\s#]+)[ \t]*$", config_text)
    )
    if len(defaults) != 1 or defaults[0].group(1) != "0":
        raise BuildError(
            "Debian Live GRUB config must select entry zero as its only default"
        )
    if re.search(r"(?m)^\s*set\s+timeout(?:_style)?\s*=", config_text):
        raise BuildError(
            "Debian Live GRUB config already sets a timeout; review the source "
            "before changing unattended boot behavior"
        )

    default_line = defaults[0].group(0)
    config_text = config_text.replace(
        default_line,
        default_line + "\nset timeout=5\nset timeout_style=menu",
        1,
    )
    if (
        config_text.count("set timeout=5") != 1
        or config_text.count("set timeout_style=menu") != 1
    ):
        raise BuildError("could not configure the five-second unattended GRUB boot")
    boot_config.write_text(config_text, encoding="utf-8")
    return boot_config


def create_iso(
    *,
    base_iso: Path,
    output: Path,
    work: Path,
    compressed_image: Path,
    manifest: dict[str, object],
    webhook: str,
) -> None:
    source_squash = work / "base-filesystem.squashfs"
    rootfs = work / "rootfs"
    new_squash = work / "new-filesystem.squashfs"
    payload = work / "payload"
    grub_config = unattended_grub_config(base_iso, work)
    payload.mkdir()
    cleared_checksums = work / "empty-md5sum.txt"
    cleared_checksums.write_text("", encoding="ascii")
    shutil.copy2(compressed_image, payload / compressed_image.name)
    (payload / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    run(
        [
            "xorriso",
            "-osirrox",
            "on",
            "-indev",
            str(base_iso),
            "-extract",
            "/live/filesystem.squashfs",
            str(source_squash),
        ],
        timeout=600,
    )
    run(["unsquashfs", "-no-progress", "-d", str(rootfs), str(source_squash)], timeout=1200)
    release = (rootfs / "etc" / "os-release").read_text(encoding="utf-8")
    suite_match = re.search(r"^VERSION_CODENAME=(.+)$", release, flags=re.MULTILINE)
    if not suite_match:
        raise BuildError("could not read Debian suite from live root")
    suite = suite_match.group(1).strip().strip('"')
    if not re.fullmatch(r"[a-z0-9-]+", suite):
        raise BuildError("Debian suite name from the live root is invalid")

    install_runtime_packages(rootfs, suite)
    write_runtime_files(rootfs, webhook)
    run(
        [
            "mksquashfs",
            str(rootfs),
            str(new_squash),
            "-noappend",
            "-comp",
            "xz",
            "-b",
            "1M",
            "-processors",
            "4",
            "-no-progress",
        ],
        timeout=2400,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "xorriso",
        "-indev",
        str(base_iso),
        "-outdev",
        str(output),
        "-boot_image",
        "any",
        "replay",
    ]
    command.extend(
        [
            "-map",
            str(new_squash),
            "/live/filesystem.squashfs",
            "-map",
            str(payload),
            "/truenas-restore",
            "-map",
            str(grub_config),
            "/boot/grub/config.cfg",
            "-map",
            str(cleared_checksums),
            "/md5sum.txt",
            "-volid",
            "TRUENAS27_RESTORE",
            "-commit",
        ]
    )
    run(command, timeout=3600)

    report = run(["xorriso", "-indev", str(output), "-report_el_torito", "plain"])
    report_text = report.stdout + report.stderr
    if "El Torito catalog" not in report_text or "El Torito boot img" not in report_text:
        raise BuildError("built ISO did not retain its El Torito boot catalog")
    extracted = work / "manifest-check.json"
    run(
        [
            "xorriso",
            "-osirrox",
            "on",
            "-indev",
            str(output),
            "-extract",
            "/truenas-restore/manifest.json",
            str(extracted),
        ],
        timeout=120,
    )
    if json.loads(extracted.read_text(encoding="utf-8")) != manifest:
        raise BuildError("manifest read-back from built ISO does not match")
    extracted_grub_config = work / "grub-config-check.cfg"
    run(
        [
            "xorriso",
            "-osirrox",
            "on",
            "-indev",
            str(output),
            "-extract",
            "/boot/grub/config.cfg",
            str(extracted_grub_config),
        ],
        timeout=120,
    )
    if extracted_grub_config.read_bytes() != grub_config.read_bytes():
        raise BuildError("GRUB unattended boot configuration read-back did not match")


def build(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise BuildError(
            "ISO construction needs root to preserve Debian Live filesystem owners; "
            "run this command with sudo"
        )
    tools = REQUIRED_TOOLS["build"] + ["ssh-keygen", "systemd-analyze"]
    if args.base_signature:
        tools.append("gpgv")
    require_tools(tools)
    base_iso = args.base_iso.resolve()
    sums = args.base_checksums.resolve()
    image = args.image.resolve()
    secrets_file = args.secrets.resolve()
    output = args.output.resolve()
    if output in {base_iso, sums, image, secrets_file}:
        raise BuildError("output ISO path must differ from all build inputs")
    if not base_iso.is_file():
        raise BuildError(f"base Debian Live ISO not found: {base_iso}")
    if output.exists() and not args.force:
        raise BuildError(f"refusing to replace existing ISO: {output}")
    if not image.is_file():
        raise BuildError(f"golden raw image not found: {image}")

    webhook_config = load_secrets(secrets_file, image)
    verify_signature(args)
    base_hash = sha512_from_sums(base_iso, sums)
    log(f"Base Debian Live ISO SHA-512 verified: {base_hash}")
    image_facts = inspect_golden_image(image)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="truenas-restore-build-",
        dir=str(output.parent),
    ) as temp_name:
        work = Path(temp_name)
        compressed = work / golden_image_payload_name(
            int(image_facts["raw_image_size_bytes"])
        )
        raw_sha, compressed_sha = compress_image(
            image,
            compressed,
            int(image_facts["raw_image_size_bytes"]),
        )
        manifest: dict[str, object] = {
            "schema_version": 1,
            **image_facts,
            "raw_image_sha256": raw_sha,
            "compressed_image_sha256": compressed_sha,
            "efi_loader": EFI_LOADER,
            "source_iso": base_iso.name,
            "source_iso_sha512": base_hash,
        }
        stage = work / "unattended-restore.iso"
        create_iso(
            base_iso=base_iso,
            output=stage,
            work=work,
            compressed_image=compressed,
            manifest=manifest,
            webhook=webhook_config["mattermost_webhook_url"],
        )
        if output.exists():
            output.unlink()
        os.replace(stage, output)
    caller_uid = int(os.environ.get("SUDO_UID", os.geteuid()))
    caller_gid = int(os.environ.get("SUDO_GID", os.getegid()))
    os.chown(output, caller_uid, caller_gid)
    os.chmod(output, 0o600)
    digest = hash_file(output, "sha256")
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="ascii")
    os.chown(checksum, caller_uid, caller_gid)
    os.chmod(checksum, 0o600)
    log(f"Built ISO: {output} ({output.stat().st_size} bytes)")
    log(f"ISO SHA-256: {digest}")
    log("The ISO contains the Mattermost webhook; keep this build artifact private.")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a Mattermost-reporting, unattended TrueNAS restore ISO."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init-secrets",
        help="combine credential inputs into a mode-0600 TOML sibling of the image",
    )
    init.add_argument("--image", type=Path, required=True)
    init.add_argument("--admin-password-file", type=Path, required=True)
    init.add_argument("--root-private-key", type=Path, required=True)
    init.add_argument("--root-public-key", type=Path, required=True)
    init.add_argument("--webhook-file", type=Path, required=True)
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=initialize_secrets)

    build_parser = commands.add_parser(
        "build",
        help="build the bootable ISO from Debian Live, the raw disk, and secret TOML",
    )
    build_parser.add_argument("--base-iso", type=Path, required=True)
    build_parser.add_argument("--base-checksums", type=Path, required=True)
    build_parser.add_argument("--base-signature", type=Path)
    build_parser.add_argument("--debian-cd-keyring", type=Path)
    build_parser.add_argument("--image", type=Path, required=True)
    build_parser.add_argument("--secrets", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--force", action="store_true")
    build_parser.set_defaults(func=build)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        args.func(args)
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
