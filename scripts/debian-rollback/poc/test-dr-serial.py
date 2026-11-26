#!/usr/bin/env python3
"""Offline test of dr-serial.py's marker logic against a fake serial socket. No guest, no host.

  python3 -I scripts/debian-rollback/poc/test-dr-serial.py
"""
import importlib.util
import base64
import contextlib
import errno
import hashlib
import io
import inspect
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("drs", Path(__file__).with_name("dr-serial.py"))
drs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drs)


ABSENT = object()


def fake_guest(reply: bytes):
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a

    def serve():
        b.recv(4096)
        b.sendall(reply)

    threading.Thread(target=serve, daemon=True).start()
    return g


class ScriptedGuest:
    """Run scenarios against command-keyed output without a serial socket."""

    def __init__(self, responder):
        self.responder = responder
        self.commands = []

    def login(self, timeout=900):
        return None

    def run(self, cmd, timeout=300):
        self.commands.append(cmd)
        try:
            return self.responder(cmd)
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - an unscripted command is a test failure
            raise AssertionError(f"unscripted command: {cmd}") from exc


def capture_scenario(call):
    """Run a scenario/helper while isolating the module-global failure list."""
    old_fails = drs.FAILS
    drs.FAILS = []
    result = None
    raised = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = call()
    except Exception as exc:  # noqa: BLE001 - report scenario exceptions as test failures
        raised = exc
    finally:
        fails = list(drs.FAILS)
        drs.FAILS = old_fails
    return result, fails, raised


def b64(text):
    return base64.b64encode(text.encode()).decode()


PAYLOAD_HOOKS = (
    "etc/apt/apt.conf.d/80dr-state",
    "etc/kernel/postinst.d/zz-dr-state",
    "etc/kernel/postrm.d/zz-dr-state",
)


def copy_payload_files(destination: Path) -> None:
    """Copy every bytecode-free file in files/ into a guest-like fixture root."""
    for path in drs.payload_files():
        relative = path.relative_to(drs.PAYLOAD_ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def installed_digest_at(root: Path) -> str:
    """Run the production guest digest expression against a temporary root."""
    command = drs.installed_payload_digest_command()
    prefix = "cd / && "
    if not command.startswith(prefix):
        raise AssertionError(f"guest digest command does not start at /: {command}")
    fixture_command = f"cd {shlex.quote(str(root))} && {command[len(prefix):]}"
    result = subprocess.run(
        ["bash", "-c", fixture_command],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"guest digest command failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def acceptance_environment(value=ABSENT):
    """Patch DR_ACCEPTANCE without dropping PATH and the rest of the gate environment."""
    environment = dict(os.environ)
    environment.pop("DR_ACCEPTANCE", None)
    if value is not ABSENT:
        environment["DR_ACCEPTANCE"] = value
    return mock.patch.dict(os.environ, environment, clear=True)


def backend_output(marker=ABSENT, config=ABSENT, done=True):
    lines = []
    for name, value in (("CONFIG", config), ("MARKER", marker)):
        if value is ABSENT:
            lines.append(f"BACKEND_{name}_PRESENT:0")
            continue
        lines.append(f"BACKEND_{name}_PRESENT:1")
        lines.append(f"BACKEND_{name}_READABLE:1")
        lines.append(f"BACKEND_{name}_B64:{b64(value)}")
    if done:
        lines.append("BACKEND_SOURCES_DONE:1")
    return 0, "\n".join(lines) + "\n"


def backend_guest(marker=ABSENT, config=ABSENT, done=True):
    def responder(cmd):
        required = (
            "/etc/dr-state.conf",
            "/boot/efi/EFI/dr/bootloader",
            "BACKEND_CONFIG_PRESENT",
            "BACKEND_CONFIG_READABLE",
            "BACKEND_CONFIG_B64",
            "BACKEND_MARKER_PRESENT",
            "BACKEND_MARKER_READABLE",
            "BACKEND_MARKER_B64",
            "BACKEND_SOURCES_DONE",
        )
        missing = [fragment for fragment in required if fragment not in cmd]
        if missing:
            raise AssertionError(f"backend command missing {missing}: {cmd}")
        return backend_output(marker, config, done)

    return ScriptedGuest(responder)


KID_A = "6.12.117+deb13-amd64-" + "0123456789abcdef" * 4
KID_B = "6.12.107+deb13-amd64-" + "fedcba9876543210" * 4
KVER_A = "6.12.117+deb13-amd64"
KVER_B = "6.12.107+deb13-amd64"


def bls_text(state, kid, kver, linux=None, initrd=None):
    linux = linux or f"/EFI/dr/kernels/{kid}/vmlinuz"
    initrd = initrd or f"/EFI/dr/kernels/{kid}/initrd.img"
    return (
        "# generated by dr-state sdboot; do not edit\n"
        f"title Debian {state} (kernel {kver})\n"
        f"linux {linux}\n"
        f"initrd {initrd}\n"
        f"options root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol={state} ro console=ttyS0,115200\n"
        f"sort-key {state.removeprefix('@')}\n"
    )


def entry_sources_output(entries):
    lines = []
    for path, text in entries:
        lines.append(f"BLS_PATH:{path}")
        lines.append(f"BLS_B64:{b64(text)}")
    return 0, "\n".join(lines) + "\n"


def scenario_guest(*, backend=None, states=("@os-000001-20261118-120000Z-install", "@os-000002-20261118-120100Z-apt"), default="@os-000001-20261118-120000Z-install",
                   entries=None, file_paths=None, file_extra=(), file_done=True, file_rc=0,
                   loader="default os-000001-20261118-120000Z-install.conf\ntimeout 5\n", scratch=(), scratch_done=True, scratch_rc=0,
                   free="12345"):
    entries = entries if entries is not None else [
        ("/boot/efi/loader/entries/os-000001-20261118-120000Z-install.conf", bls_text("@os-000001-20261118-120000Z-install", KID_A, KVER_A)),
        ("/boot/efi/loader/entries/os-000002-20261118-120100Z-apt.conf", bls_text("@os-000002-20261118-120100Z-apt", KID_B, KVER_B)),
    ]
    if file_paths is None:
        file_paths = {
            f"/boot/efi/EFI/dr/kernels/{KID_A}/vmlinuz",
            f"/boot/efi/EFI/dr/kernels/{KID_A}/initrd.img",
            f"/boot/efi/EFI/dr/kernels/{KID_B}/vmlinuz",
            f"/boot/efi/EFI/dr/kernels/{KID_B}/initrd.img",
        }

    def responder(cmd):
        if "BACKEND_SOURCES_DONE" in cmd:
            return backend if backend is not None else backend_output(config="BOOTLOADER=systemd-boot\n")
        if "dr-state list" in cmd and "STATES:" in cmd:
            return 0, "STATES:" + ",".join(states) + "\n"
        if "DEF:" in cmd:
            return 0, f"DEF:{default}\n"
        if "BLS_B64" in cmd:
            return entry_sources_output(entries)
        if "ESP_FILE_OK" in cmd:
            required = ("[ -f \"$f\" ]", "[ -s \"$f\" ]", "ESP_FILE_OK", "ESP_FILE_SCAN_DONE")
            missing = [fragment for fragment in required if fragment not in cmd]
            if missing:
                raise AssertionError(f"ESP file command missing {missing}: {cmd}")
            requested = set(re.findall(r"(/boot/efi/[^ ;]+)", cmd))
            lines = [f"ESP_FILE_OK:{path}" for path in sorted(file_paths & requested)]
            lines.extend(f"ESP_FILE_OK:{path}" for path in file_extra)
            if file_done:
                lines.append("ESP_FILE_SCAN_DONE:1")
            return file_rc, "\n".join(lines) + "\n"
        if "LOADER_B64" in cmd:
            if loader is None:
                return 0, "LOADER_MISSING:1\n"
            return 0, f"LOADER_B64:{b64(loader)}\n"
        if "SCRATCH_SCAN_DONE" in cmd:
            required = (
                "/boot/efi/EFI/dr/kernels/.dr-state.tmp.*",
                "/boot/efi/loader/entries/.dr-state.tmp.*",
                "/boot/efi/loader/.dr-state.tmp.*",
                "/boot/efi/EFI/dr/.dr-state.tmp.*",
                "/.top/.dr-state/.dr-state.tmp.*",
                'if [ -e "$p" ] || [ -L "$p" ]',
                "SCRATCH_SCAN_DONE",
            )
            missing = [fragment for fragment in required if fragment not in cmd]
            if missing:
                raise AssertionError(f"scratch command missing {missing}: {cmd}")
            lines = [f"SCRATCH_CANDIDATE:{path}" for path in scratch]
            if scratch_done:
                lines.append("SCRATCH_SCAN_DONE:1")
            return scratch_rc, "\n".join(lines) + "\n"
        if "ESP_FREE_KB" in cmd:
            return 0, f"ESP_FREE_KB:{free}\n"
        raise AssertionError(f"unexpected scenario command: {cmd}")

    return ScriptedGuest(responder)


def main() -> int:
    failures = []

    with tarfile.open(fileobj=io.BytesIO(drs.payload_archive()), mode="r") as archive:
        members = archive.getmembers()
        launcher = archive.getmember("usr/local/sbin/dr-state")
        package = archive.getmember("usr/local/lib/dr-state/dr_state/core.py")
    if any(member.isdir() for member in members):
        failures.append(
            "payload archive contains directory members: "
            f"{[member.name for member in members if member.isdir()]}"
        )
    unexpected_members = [
        member.name
        for member in members
        if member.name != "usr/local/sbin/dr-state"
        and not member.name.startswith("usr/local/lib/dr-state/")
    ]
    if unexpected_members:
        failures.append(
            "runtime archive contains paths outside its two subtrees: "
            f"{unexpected_members}"
        )
    if any("__pycache__" in member.name or member.name.endswith((".pyc", ".pyo"))
           for member in members):
        failures.append("payload archive contains generated bytecode")
    for member in members:
        if (member.uid, member.gid, member.uname, member.gname) != (0, 0, "root", "root"):
            failures.append(
                f"payload archive member {member.name} is not root-owned: "
                f"{member.uid}:{member.gid} {member.uname}:{member.gname}"
            )
    if (
        launcher.name != "usr/local/sbin/dr-state"
        or package.name != "usr/local/lib/dr-state/dr_state/core.py"
    ):
        failures.append("runtime archive lost its launcher or Python package")

    def unscripted(command):
        raise AssertionError(f"unexpected guest command during payload refusal: {command}")

    original_payload_root = drs.PAYLOAD_ROOT
    with tempfile.TemporaryDirectory() as temporary:
        payload = Path(temporary) / "files"
        runtime = payload / "usr" / "local"
        (runtime / "sbin").mkdir(parents=True)
        (runtime / "lib" / "dr-state" / "dr_state").mkdir(parents=True)
        for relative in (
            "etc/apt/apt.conf.d/80dr-state",
            "etc/kernel/postinst.d/zz-dr-state",
            "etc/kernel/postrm.d/zz-dr-state",
        ):
            path = payload / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{relative} v1\n", encoding="utf-8")
        launcher_path = runtime / "sbin" / "dr-state"
        core_path = runtime / "lib" / "dr-state" / "dr_state" / "core.py"
        hook_path = payload / "etc" / "apt" / "apt.conf.d" / "80dr-state"
        launcher_path.write_text("launcher\n", encoding="utf-8")
        core_path.write_text("core v1\n", encoding="utf-8")
        drs.PAYLOAD_ROOT = payload
        try:
            before = drs.payload_manifest_digest()
            launcher_hash = hashlib.sha256(launcher_path.read_bytes()).hexdigest()
            core_path.write_text("core v2\n", encoding="utf-8")
            after_core = drs.payload_manifest_digest()
            hook_path.write_text("hook v2\n", encoding="utf-8")
            after_hook = drs.payload_manifest_digest()
            hook_config_exact = drs.current_apt_hook_config() == b"hook v2\n"
            launcher_unchanged = (
                hashlib.sha256(launcher_path.read_bytes()).hexdigest() == launcher_hash
            )
        finally:
            drs.PAYLOAD_ROOT = original_payload_root
    if before == after_core:
        failures.append("payload digest ignored a core.py change")
    if after_core != after_hook:
        failures.append("runtime payload digest included an APT hook outside files/usr/local")
    if not hook_config_exact:
        failures.append("current_apt_hook_config() did not read the hook from PAYLOAD_ROOT/files")
    if not launcher_unchanged:
        failures.append("payload digest fixture changed the launcher unexpectedly")

    driver_source = Path(drs.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "_window-blocks",
        "subvol_parent_uuid",
        ".dr-state-entry.new.",
        ".vmlinuz.tmp.",
        ".initrd.img.tmp.",
        "loader.conf.new.",
        "bootloader.new.",
        "/mnt/seed/poc/files/usr/local/. /usr/local/",
    ):
        if forbidden in driver_source:
            failures.append(
                f"serial driver still depends on deleted bash shape {forbidden!r}"
            )
    for required in (
        "--same-owner",
        "chown -R 0:0 /usr/local/sbin/dr-state /usr/local/lib/dr-state "
        "/etc/apt/apt.conf.d/80dr-state /etc/kernel/postinst.d/zz-dr-state "
        "/etc/kernel/postrm.d/zz-dr-state; echo OWNRC:$?;",
        "/etc/apt/apt.conf.d/80dr-state",
        "/etc/kernel/postinst.d/zz-dr-state",
        "/etc/kernel/postrm.d/zz-dr-state",
        "DR_ACCEPTANCE unset, empty or 0 means dev; 1 means acceptance and refuses hot patches; "
        "any other value is refused.",
    ):
        if required not in driver_source:
            failures.append(f"serial driver lost its Python payload contract {required!r}")
    for helper in (
        drs.install_current_dr_state,
        drs.install_current_apt_hook,
        drs._update_dr_state,
    ):
        if drs.HOT_PATCH_REFUSAL not in (inspect.getdoc(helper) or ""):
            failures.append(
                f"{helper.__name__} docstring omits {drs.HOT_PATCH_REFUSAL!r}"
            )
    installed_digest_command = drs.installed_payload_digest_command()
    for required in (
        "usr/local/sbin/dr-state",
        "usr/local/lib/dr-state",
        "-type f -print0",
    ):
        if required not in installed_digest_command:
            failures.append(f"installed payload digest command omits {required!r}")

    # Execute the guest digest expression against a temporary tree with every file under
    # files/usr/local, then compare it with the Python manifest.
    with tempfile.TemporaryDirectory() as temporary:
        digest_root = Path(temporary) / "installed"
        copy_payload_files(digest_root)
        guest_digest = installed_digest_at(digest_root)
        manifest_digest = drs.payload_manifest_digest()
    if guest_digest != manifest_digest:
        failures.append(
            "digest-path-list-equivalence: guest digest file set differs from runtime payload files: "
            f"guest={guest_digest} python={manifest_digest}"
        )

    original_payload_root = drs.PAYLOAD_ROOT
    with tempfile.TemporaryDirectory() as temporary:
        unexpected_root = Path(temporary) / "files"
        shutil.copytree(original_payload_root, unexpected_root)
        extra_runtime_file = unexpected_root / "usr/local/share/dr-state-extra"
        extra_runtime_file.parent.mkdir(parents=True)
        extra_runtime_file.write_text("unexpected\n", encoding="utf-8")
        drs.PAYLOAD_ROOT = unexpected_root
        try:
            try:
                drs.payload_archive()
            except ValueError as exc:
                if "usr/local/share/dr-state-extra" not in str(exc):
                    failures.append(f"runtime archive refusal omitted the path name: {exc}")
            else:
                failures.append("runtime archive accepted a file outside its two subtrees")
        finally:
            drs.PAYLOAD_ROOT = original_payload_root

    for helper_name, helper in (
        ("_update_dr_state", drs._update_dr_state),
        ("verify_current_dr_state", drs.verify_current_dr_state),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            drs.PAYLOAD_ROOT = Path(temporary) / "missing-files"
            guest = ScriptedGuest(unscripted)
            result, helper_fails, helper_raised = capture_scenario(
                lambda helper=helper: helper(guest, io.StringIO())
            )
            if (
                result is not False
                or not helper_fails
                or helper_raised is not None
                or guest.commands
            ):
                failures.append(
                    f"{helper_name} did not record a missing files/ payload as FAIL: "
                    f"result={result!r} fails={helper_fails!r} raised={helper_raised!r} "
                    f"commands={guest.commands!r}"
                )
    drs.PAYLOAD_ROOT = original_payload_root

    design_root = Path(drs.__file__).parent.parent / "docs"
    core_design = (design_root / "DESIGN-CORE-SPLIT.md").read_text(encoding="utf-8")
    convergence_design = (design_root / "DESIGN-INSTALL-CONVERGENCE.md").read_text(
        encoding="utf-8"
    )
    if re.search(r"dr-state:\d", convergence_design):
        failures.append("install-convergence design still cites the retired dr-state shell by line")
    for required in (
        "sdboot rehashes source and ESP payloads until P-23",
        "omits directory members only for directories that already exist at the destination",
        "takes modes from the guest's umask",
    ):
        if required not in core_design:
            failures.append(f"core-split design omits the updated payload contract {required!r}")

    hot_patch_calls = (
        ("install_current_dr_state", lambda guest, log: drs.install_current_dr_state(guest, log)),
        ("install_current_apt_hook", lambda guest, log: drs.install_current_apt_hook(guest, log)),
        ("_update_dr_state", lambda guest, log: drs._update_dr_state(guest, log)),
    )

    for case, value, want, message in (
        ("unset", ABSENT, False, None),
        ("empty", "", False, None),
        ("zero", "0", False, None),
        ("one", "1", True, drs.HOT_PATCH_REFUSAL),
        ("invalid-true", "true", True, drs.DR_ACCEPTANCE_ERROR),
        ("invalid-whitespace", " 1", True, drs.DR_ACCEPTANCE_ERROR),
    ):
        helper_log = io.StringIO()
        with acceptance_environment(value):
            result, helper_fails, helper_raised = capture_scenario(
                lambda log=helper_log: drs._hot_patch_refused(log, f"DR_ACCEPTANCE {case}")
            )
        expected_failure = f"DR_ACCEPTANCE {case} refused: {message}"
        if (
            result is not want
            or helper_raised is not None
            or (message is None and helper_fails)
            or (message is not None
                and not any(expected_failure in failure for failure in helper_fails))
        ):
            failures.append(
                f"DR_ACCEPTANCE parsing {case}: result={result!r} fails={helper_fails!r} "
                f"raised={helper_raised!r}"
            )

    for raw, message in (
        ("1", drs.HOT_PATCH_REFUSAL),
        ("true", drs.DR_ACCEPTANCE_ERROR),
        (" 1", drs.DR_ACCEPTANCE_ERROR),
    ):
        for label, call in hot_patch_calls:
            guest = ScriptedGuest(unscripted)
            helper_log = io.StringIO()
            with acceptance_environment(raw):
                result, helper_fails, helper_raised = capture_scenario(
                    lambda call=call, guest=guest, helper_log=helper_log: call(guest, helper_log)
                )
            expected = f"{label} refused: {message}"
            if (
                result is not False
                or helper_raised is not None
                or guest.commands
                or not any(expected in failure for failure in helper_fails)
            ):
                failures.append(
                    f"DR_ACCEPTANCE={raw!r} refusal for {label}: result={result!r} "
                    f"fails={helper_fails!r} raised={helper_raised!r} commands={guest.commands!r}"
                )

    apt_hook_payload = drs.current_apt_hook_config()
    apt_hook_digest = hashlib.sha256(apt_hook_payload).hexdigest()

    def apt_hook_responder(command):
        match = re.search(r"<<'APT_HOOK_B64'\n(.*?)APT_HOOK_B64\n", command, re.S)
        if match is None:
            raise AssertionError("APT hook helper did not delimit its payload")
        decoded = base64.b64decode("".join(match.group(1).split()), validate=True)
        if decoded != apt_hook_payload:
            raise AssertionError("APT hook helper did not send the files/ hook bytes")
        return 0, f"APTHOOK_SHA:{apt_hook_digest}\nAPTHOOK_INSTALL_RC:0\n"

    apt_hook_guest = ScriptedGuest(apt_hook_responder)
    with acceptance_environment():
        _, apt_fails, apt_raised = capture_scenario(
            lambda: drs.install_current_apt_hook(apt_hook_guest, io.StringIO())
        )
    if apt_raised is not None or apt_fails or len(apt_hook_guest.commands) != 1:
        failures.append(
            f"APT hook helper without DR_ACCEPTANCE: fails={apt_fails!r} "
            f"raised={apt_raised!r} commands={apt_hook_guest.commands!r}"
        )

    # The runtime archive converges only its two allowed subtrees; hook updates use separate helpers.
    with tempfile.TemporaryDirectory() as temporary:
        changed_payload = Path(temporary) / "files"
        shutil.copytree(
            original_payload_root,
            changed_payload,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        for relative in PAYLOAD_HOOKS:
            hook = changed_payload / relative
            hook.write_bytes(hook.read_bytes() + b"x")
        changed_core = changed_payload / "usr/local/lib/dr-state/dr_state/core.py"
        changed_core.write_bytes(changed_core.read_bytes() + b"x")
        drs.PAYLOAD_ROOT = changed_payload
        try:
            changed_manifest = drs.payload_manifest_digest()

            def archive_patch_responder(command, root):
                match = re.search(r"<<'DR_STATE_B64'\n(.*?)DR_STATE_B64\n", command, re.S)
                if match is None:
                    raise AssertionError("runtime helper did not delimit its archive")
                decoded = base64.b64decode("".join(match.group(1).split()), validate=True)
                if decoded != drs.payload_archive():
                    raise AssertionError("runtime helper did not send its complete runtime archive")
                if "tar -C / --same-owner -xf" not in command:
                    raise AssertionError("runtime helper does not extract file members at /")
                shutil.rmtree(root / "usr/local/lib/dr-state", ignore_errors=True)
                with tarfile.open(fileobj=io.BytesIO(decoded), mode="r") as archive:
                    if any(member.isdir() for member in archive.getmembers()):
                        raise AssertionError("runtime archive contains directory members")
                    archive.extractall(root, filter="fully_trusted")
                for relative in PAYLOAD_HOOKS:
                    installed = (root / relative).read_bytes()
                    expected = (original_payload_root / relative).read_bytes()
                    if installed != expected:
                        raise AssertionError(
                            f"runtime helper unexpectedly changed hook {relative}"
                        )
                return 0, (
                    f"DRSTATE_SHA:{installed_digest_at(root)}\n"
                    "DRSTATE_INSTALL_RC:0\n"
                )

            archive_root = Path(temporary) / "archive-guest"
            shutil.copytree(
                original_payload_root,
                archive_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            archive_guest = ScriptedGuest(
                lambda command: archive_patch_responder(command, archive_root)
            )
            with acceptance_environment():
                archive_result, archive_fails, archive_raised = capture_scenario(
                    lambda: drs.install_current_dr_state(archive_guest, io.StringIO())
                )
            if (
                archive_result is not True
                or archive_fails
                or archive_raised is not None
                or len(archive_guest.commands) != 1
                or installed_digest_at(archive_root) != changed_manifest
            ):
                failures.append(
                    "hot-patch-runtime-archive-converges: runtime hot patch did not converge changed files: "
                    f"result={archive_result!r} fails={archive_fails!r} "
                    f"raised={archive_raised!r} commands={archive_guest.commands!r} "
                    f"sha={installed_digest_at(archive_root)} expected={changed_manifest}"
                )

            def changed_apt_responder(command):
                match = re.search(r"<<'APT_HOOK_B64'\n(.*?)APT_HOOK_B64\n", command, re.S)
                if match is None:
                    raise AssertionError("APT hook helper did not delimit its payload")
                decoded = base64.b64decode("".join(match.group(1).split()), validate=True)
                expected = (drs.PAYLOAD_ROOT / PAYLOAD_HOOKS[0]).read_bytes()
                if decoded != expected:
                    raise AssertionError("APT hook helper did not send the changed APT hook")
                return 0, (
                    f"APTHOOK_SHA:{hashlib.sha256(decoded).hexdigest()}\n"
                    "APTHOOK_INSTALL_RC:0\n"
                )

            changed_apt_guest = ScriptedGuest(changed_apt_responder)
            with acceptance_environment():
                apt_result, apt_fails, apt_raised = capture_scenario(
                    lambda: drs.install_current_apt_hook(changed_apt_guest, io.StringIO())
                )
            if (
                apt_result is not True
                or apt_fails
                or apt_raised is not None
                or len(changed_apt_guest.commands) != 1
            ):
                failures.append(
                    "hot-patch-apt-changed-hook: changed APT hook was not installed: "
                    f"result={apt_result!r} fails={apt_fails!r} "
                    f"raised={apt_raised!r} commands={changed_apt_guest.commands!r}"
                )

            seed_root = Path(temporary) / "seed-guest"
            shutil.copytree(
                original_payload_root,
                seed_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )

            def update_responder(command):
                required = (
                    "mount -o ro /dev/sr0 /mnt/seed",
                    "/mnt/seed/poc/files/usr/local/sbin/dr-state",
                    "/mnt/seed/poc/files/usr/local/lib/dr-state",
                    "chown -R 0:0 /usr/local/sbin/dr-state /usr/local/lib/dr-state "
                    "/etc/apt/apt.conf.d/80dr-state /etc/kernel/postinst.d/zz-dr-state "
                    "/etc/kernel/postrm.d/zz-dr-state; echo OWNRC:$?;",
                )
                required += tuple(
                    f"/mnt/seed/poc/files/{relative} /{relative}"
                    for relative in PAYLOAD_HOOKS
                )
                required += tuple(f"/{relative}" for relative in PAYLOAD_HOOKS)
                missing = [fragment for fragment in required if fragment not in command]
                if missing:
                    raise AssertionError(f"seed update command missing {missing}: {command}")
                shutil.rmtree(seed_root / "usr/local/lib/dr-state", ignore_errors=True)
                copy_payload_files(seed_root)
                for relative in PAYLOAD_HOOKS:
                    if ((seed_root / relative).read_bytes()
                            != (drs.PAYLOAD_ROOT / relative).read_bytes()):
                        raise AssertionError(f"seed update left {relative} stale")
                return 0, (
                    "MNTRC:0\nINSRC:0\nOWNRC:0\n"
                    f"DRSTATE_SHA:{installed_digest_at(seed_root)}\n"
                )

            update_guest = ScriptedGuest(update_responder)
            with acceptance_environment():
                update_result, update_fails, update_raised = capture_scenario(
                    lambda: drs._update_dr_state(update_guest, io.StringIO())
                )
            if (
                update_result is not True
                or update_fails
                or update_raised is not None
                or len(update_guest.commands) != 1
                or installed_digest_at(seed_root) != changed_manifest
            ):
                failures.append(
                    "hot-patch-seed-converges-hooks: seed hot patch did not converge changed hooks: "
                    f"result={update_result!r} fails={update_fails!r} "
                    f"raised={update_raised!r} commands={update_guest.commands!r} "
                    f"sha={installed_digest_at(seed_root)} expected={changed_manifest}"
                )
        finally:
            drs.PAYLOAD_ROOT = original_payload_root

    default_state_cases = (
        ("running-default", "@os-000001-20261118-120000Z-install (running, default) kernel=6.12.107\n", "@os-000001-20261118-120000Z-install"),
        ("default-only", "@os-000001-20261121-041941Z-apt (default) kernel=6.12.107\n",
         "@os-000001-20261121-041941Z-apt"),
        ("running-only", "@os-000001-20261118-120000Z-install (running) kernel=6.12.107\n", ""),
    )
    for label, listing, expected in default_state_cases:
        parsed = subprocess.run(
            ["awk", drs.DEFAULT_STATE_AWK], input=listing, text=True, capture_output=True, check=False
        )
        if parsed.returncode != 0 or parsed.stdout.strip() != expected:
            failures.append(
                f"default-state parser {label}: rc={parsed.returncode} got={parsed.stdout.strip()!r} want={expected!r}"
            )

    # The echoed wrapper does not contain a numeric marker; the real marker ends the command.
    g = fake_guest(b"eval 'ls'; __dr_serial_rc=$?; printf '__DR_END_%s__\\n' \"$__dr_serial_rc\"\r\nfile1\r\nfile2\r\n__DR_END_0__\r\n")
    rc, text = g.run("ls", 5)
    if rc != 0 or "file2" not in text or re.search(r"__DR_END_\d+__", text):
        failures.append(f"plain command: rc={rc} text={text!r}")

    # a failing command reports its exit status
    g = fake_guest(b"eval 'false'; __dr_serial_rc=$?; printf '__DR_END_%s__\\n' \"$__dr_serial_rc\"\r\n__DR_END_1__\r\n")
    rc, _ = g.run("false", 5)
    if rc != 1:
        failures.append(f"exit status not carried: {rc}")

    # The wrapper isolates the input command in eval, so syntax-breaking trailing operators and comments
    # cannot absorb the marker command or turn the guest run into a timeout.
    for command, want_status in (("false;", 1), ("false &&", 2), ("true ||", 2), ("# comment", 0)):
        wrapped = drs.marker_wrapped_command(command)
        syntax = subprocess.run(["bash", "-n"], input=wrapped, text=True, capture_output=True, check=False)
        if syntax.returncode != 0:
            failures.append(f"marker wrapper is not syntactically complete for {command!r}: {syntax.stderr.strip()!r}")
            continue
        result = subprocess.run(["bash"], input=wrapped, text=True, capture_output=True, check=False)
        marker = re.search(r"__DR_END_(\d+)__", result.stdout)
        if marker is None or int(marker.group(1)) != want_status:
            failures.append(
                f"marker wrapper did not report {want_status} for {command!r}: "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )

    # a marker split across two reads is still found
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a

    def serve_split():
        b.recv(4096)
        b.sendall(b"out\r\n__DR_EN")
        threading.Event().wait(0.3)
        b.sendall(b"D_7__\r\n")

    threading.Thread(target=serve_split, daemon=True).start()
    rc, _ = g.run("x", 5)
    if rc != 7:
        failures.append(f"split marker: rc={rc}")

    # A permanent AF_UNIX address error must fail immediately instead of being
    # retried until the guest startup timeout (e.g. an overlong host path).
    connect_attempts = []

    class PermanentSocketError:
        def connect(self, _path):
            connect_attempts.append(True)
            raise OSError(errno.ENAMETOOLONG, "AF_UNIX path too long")

        def close(self):
            pass

    old_socket = drs.socket.socket
    drs.socket.socket = lambda _family: PermanentSocketError()
    try:
        try:
            drs.Guest("/long/path", io.StringIO()).connect(timeout=600)
            failures.append("permanent serial-socket address error unexpectedly connected")
        except OSError as exc:
            if exc.errno != errno.ENAMETOOLONG:
                failures.append(f"permanent serial-socket error changed: {exc!r}")
    finally:
        drs.socket.socket = old_socket
    if len(connect_attempts) != 1:
        failures.append(f"permanent serial-socket error retried {len(connect_attempts)} times")

    # Missing/refusing sockets are transient during QEMU startup and still retry.
    transient_attempts = []

    class TransientSocketError:
        def connect(self, _path):
            transient_attempts.append(True)
            if len(transient_attempts) == 1:
                raise OSError(errno.ENOENT, "socket not created yet")

        def setblocking(self, _enabled):
            pass

        def close(self):
            pass

    old_socket = drs.socket.socket
    old_sleep = drs.time.sleep
    drs.socket.socket = lambda _family: TransientSocketError()
    drs.time.sleep = lambda _seconds: None
    g = drs.Guest("/socket-not-created-yet", io.StringIO())
    try:
        g.connect(timeout=600)
    except Exception as exc:  # noqa: BLE001 - report unexpected fake socket failures
        failures.append(f"transient serial-socket retry failed: {exc!r}")
    finally:
        drs.socket.socket = old_socket
        drs.time.sleep = old_sleep
    if len(transient_attempts) != 2 or not isinstance(g.sock, TransientSocketError):
        failures.append(f"transient serial-socket error was not retried once: attempts={len(transient_attempts)}")

    # login: startup sends only a newline until it has observed a login or root-shell prompt.
    kernel_banner = (
        b"Linux version 6.12.117+deb13-amd64 "
        b"(debian-kernel@lists.debian.org) (gcc-14) #1 SMP PREEMPT_DYNAMIC\r\n"
    )
    if drs.console_prompt_kind(kernel_banner) is not None:
        failures.append("kernel build banner was mistaken for a console prompt")
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a
    login_errors = []

    def recv_until(needle: bytes) -> bytes:
        b.settimeout(25)
        data = b""
        while needle not in data:
            data += b.recv(256)
        return data

    def serve_login():
        try:
            first = b.recv(256)
            if first != b"\n":
                login_errors.append(f"unsafe pre-login input: {first!r}")
                return
            b.sendall(kernel_banner + b"drpoc login: ")
            recv_until(b"root\n")
            b.sendall(b"Password: ")
            recv_until(b"root\n")
            b.sendall(b"\r\nLinux drpoc 6.12 ...\r\n")
            recv_until(b"__DR_READY_")
            b.sendall(b"echo __DR_READY_$((20+22))__\r\n__DR_READY_42__\r\n")
        except Exception as exc:  # noqa: BLE001 - report fake serial exchange failures
            login_errors.append(repr(exc))

    threading.Thread(target=serve_login, daemon=True).start()
    try:
        g.login(30)
    except Exception as exc:  # noqa: BLE001 - report any failure of the fake exchange
        failures.append(f"safe login handshake failed: {exc!r}")
    if login_errors or not g.shell_ready:
        failures.append(f"safe login handshake: errors={login_errors!r}, shell_ready={g.shell_ready}")

    # An existing root shell answers a newline with its prompt; no shell command is sent.
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a
    existing_shell_errors = []

    def serve_existing_shell():
        try:
            first = b.recv(256)
            if first != b"\n":
                existing_shell_errors.append(f"unsafe existing-shell input: {first!r}")
                return
            b.sendall(b"\r\n\x1b[?2004hroot@drpoc:~# ")
        except Exception as exc:  # noqa: BLE001 - report fake serial exchange failures
            existing_shell_errors.append(repr(exc))

    threading.Thread(target=serve_existing_shell, daemon=True).start()
    try:
        g.login(30)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"login on an already logged-in shell failed: {exc!r}")
    if existing_shell_errors or not g.shell_ready:
        failures.append(f"existing-shell handshake: errors={existing_shell_errors!r}, shell_ready={g.shell_ready}")

    # Reattaching to the Debian Live user's already-open shell must not wait for a root-only prompt.
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a
    sent = []
    original_send = g.send

    def record_live_send(text):
        sent.append(text)
        original_send(text)

    g.send = record_live_send
    live_shell_errors = []

    def serve_live_shell():
        try:
            first = b.recv(256)
            if first != b"\n":
                live_shell_errors.append(f"unsafe live-shell input: {first!r}")
                return
            b.sendall(b"\r\n\x1b[?2004huser@debian:~$ ")
        except Exception as exc:  # noqa: BLE001 - report fake serial exchange failures
            live_shell_errors.append(repr(exc))

    threading.Thread(target=serve_live_shell, daemon=True).start()
    try:
        g.login(30)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"login on an already logged-in live-user shell failed: {exc!r}")
    if live_shell_errors or sent != ["\n"] or not g.shell_ready:
        failures.append(
            f"live-user-shell handshake errors={live_shell_errors!r}, sent={sent!r}, shell_ready={g.shell_ready}"
        )

    # tags: the echoed command line, also wrapped by the tty at 80 columns, never satisfies a check
    echoed = ("root@drpoc:~# echo OFFER:$(apt list --upgradable 2>/dev/null | grep -c '^linux-image'); echo KVER:$(uname\n"
              " -r); echo MODRC:$?\nOFFER:2\nKVER:6.12.115+deb13-amd64\nMODRC:0\n")
    if drs.tag(echoed, "OFFER") != "2" or drs.tag(echoed, "KVER") != "6.12.115+deb13-amd64" or drs.tag(echoed, "MODRC") != "0":
        failures.append(f"tag() read wrong values from {echoed!r}")
    only_echo = "root@drpoc:~# echo OFFER:$(apt list | grep -c linux-image); echo BORC:$?\n"
    if drs.tag(only_echo, "OFFER") is not None or drs.tag(only_echo, "BORC") is not None:
        failures.append("tag() matched the echoed command line (vacuous check)")
    if drs.tags("MP:/home\nMP:/srv\nNOTMP:/opt\nNOTMP:$m\nMP:$m\n", "MP") != ["/home", "/srv"]:
        failures.append("tags() with a loop variable echo")
    if drs.running_state("BOOT_IMAGE=/@os-000647-20261118-072759Z-apt/boot/vmlinuz-6 rootflags=subvol=@os-000647-20261118-072759Z-apt ro") != "@os-000647-20261118-072759Z-apt":
        failures.append("running_state() on a snapshot state")
    for state, want in (
        ("@os-000001-20261121-120000Z-install", False),
        ("@os-000002-20261121-120100Z-apt", True),
        ("@os-000003-20261121-120200Z-manual-before-upgrade", True),
        ("@os-pre-20261118131158-605-apt", False),
        ("@os-000004-20261340-120000Z-apt", False),
    ):
        got = drs.is_snapshot_state(state)
        if got != want:
            failures.append(f"is_snapshot_state({state!r}): want {want} got {got}")
    if drs.snapshot_state_slug("@os-000003-20261121-120200Z-manual-before-upgrade") != "manual-before-upgrade":
        failures.append("snapshot_state_slug() did not return the normalized manual reason")

    # Backend selection follows dr-state: both sources are presence-aware, either may select GRUB,
    # the sources may agree, and empty/conflicting/unknown values never fall back.
    for label, marker, config, want in (
        ("both absent", None, None, "grub"),
        ("grub marker only", "grub", None, "grub"),
        ("systemd-boot marker only", "systemd-boot", None, "systemd-boot"),
        ("CRLF marker", "systemd-boot\r\n", None, "systemd-boot"),
        ("grub config only", None, "BOOTLOADER=grub\n", "grub"),
        ("systemd-boot config only", None, "BOOTLOADER=systemd-boot\n", "systemd-boot"),
        ("config with comments and keys", None,
         "# comment\nHOSTNAME=drpoc\nBOOTLOADER=systemd-boot\n", "systemd-boot"),
        ("both agree", "systemd-boot", "BOOTLOADER=systemd-boot\n", "systemd-boot"),
        ("both agree on grub", "grub", "BOOTLOADER=grub\r\n", "grub"),
    ):
        try:
            got = drs.detect_backend(marker, config)
        except Exception as exc:  # noqa: BLE001 - report any failure of the pure helper
            failures.append(f"{label}: raised {exc!r}")
        else:
            if got != want:
                failures.append(f"{label}: want {want!r} got {got!r}")
    for label, marker, config in (
        ("empty marker", "", None),
        ("empty config value", None, "BOOTLOADER=\n"),
        ("unknown marker", "grub2", None),
        ("unknown config value", None, "BOOTLOADER=grub2\n"),
        ("sources conflict", "grub", "BOOTLOADER=systemd-boot\n"),
        ("duplicate BOOTLOADER", None, "BOOTLOADER=grub\nBOOTLOADER=grub\n"),
        ("malformed config line", None, "BOOTLOADER systemd-boot\n"),
        ("embedded CR is not a line break", None, "BOOTLOADER=grub\rHOSTNAME=other\n"),
        ("whitespace-padded marker", " systemd-boot\n", None),
        ("trailing marker garbage", "systemd-boot extra", None),
    ):
        try:
            drs.detect_backend(marker, config)
        except ValueError:
            pass
        else:
            failures.append(f"{label}: accepted instead of failing loudly")

    # loader.conf: exactly one non-comment default line, naming the entry FILE (<entry-id>.conf).
    loader_conf = (
        "# generated by dr-state sdboot\n"
        "timeout 5\n"
        "default os-000605-20261118-131158Z-manual-s5-conf.conf\n"
    )
    if drs.parse_loader_default(loader_conf) != "os-000605-20261118-131158Z-manual-s5-conf.conf":
        failures.append("parse_loader_default() read the wrong entry file")
    if drs.parse_loader_default(loader_conf.replace("\n", "\r\n")) != "os-000605-20261118-131158Z-manual-s5-conf.conf":
        failures.append("parse_loader_default() did not accept CRLF")
    for label, text in (
        ("zero defaults", "timeout 5\n"),
        ("two defaults", "default one\ndefault two\n"),
        ("missing value", "default\n"),
        ("too many fields", "default one two\n"),
        ("suffix-less old guest value", "default os-000001-20261118-120000Z-install\ntimeout 5\n"),
        ("unsafe shell value", "default '$missing.conf'\n"),
    ):
        try:
            drs.parse_loader_default(text)
        except ValueError:
            pass
        else:
            failures.append(f"parse_loader_default(): {label} accepted")
    try:
        drs.parse_loader_default("default os-000001-20261118-120000Z-install\ntimeout 5\n")
    except ValueError as exc:
        if ".conf" not in str(exc) or "guest finding" not in str(exc):
            failures.append(f"parse_loader_default(): suffix-less value lacks the guest-finding message: {exc}")
    else:
        failures.append("parse_loader_default(): suffix-less value accepted")

    sample_selected = (
        b"\x07\x00\x00\x00"
        + "os-000622-20261118-224917Z-manual-apt-conf.conf".encode("utf-16-le")
        + b"\x00\x00"
    )
    try:
        if drs.parse_efivar_string(sample_selected) != "os-000622-20261118-224917Z-manual-apt-conf.conf":
            failures.append("parse_efivar_string() read the wrong LoaderEntrySelected value")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"parse_efivar_string() raised on sample efivar content: {exc!r}")
    if drs.parse_bootctl_list_evidence(
        "BOOTCTL_LIST_BEGIN:1\n"
        "id: os-000001-20261118-120000Z-install.conf\nselected: no\ndefault: yes\n"
        "id: os-000622-20261118-224917Z-manual-apt-conf.conf\nselected: yes\ndefault: no\n"
        "BOOTCTL_LIST_RC:0\nBOOTCTL_LIST_END:1\n"
    ) != [
        "id: os-000001-20261118-120000Z-install.conf",
        "selected: no",
        "default: yes",
        "id: os-000622-20261118-224917Z-manual-apt-conf.conf",
        "selected: yes",
        "default: no",
    ]:
        failures.append("parse_bootctl_list_evidence() did not retain selected/default marks")

    # BLS entries: exactly one kernel and one matching initrd, and the state from rootflags=subvol=
    bls_entry = (
        "# generated by dr-state sdboot; do not edit\n"
        "title Debian @os-000001-20261118-120000Z-install (kernel 6.12.117+deb13-amd64)\n"
        f"linux /EFI/dr/kernels/{KID_A}/vmlinuz\n"
        f"initrd /EFI/dr/kernels/{KID_A}/initrd.img\n"
        "options root=UUID=11111111-2222-3333-4444-555555555555 "
        "rootflags=subvol=@os-000001-20261118-120000Z-install ro console=ttyS0,115200\n"
        "sort-key os-000001-20261118-120000Z-install\n"
    )
    try:
        parsed = drs.parse_bls_entry(bls_entry)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"parse_bls_entry() raised on a valid entry: {exc!r}")
    else:
        if parsed["linux"] != f"/EFI/dr/kernels/{KID_A}/vmlinuz":
            failures.append(f"parse_bls_entry() read linux={parsed['linux']!r}")
        if parsed["initrd"] != [f"/EFI/dr/kernels/{KID_A}/initrd.img"]:
            failures.append(f"parse_bls_entry() read initrd={parsed['initrd']!r}")
        if parsed["state"] != "@os-000001-20261118-120000Z-install":
            failures.append(f"parse_bls_entry() read state={parsed['state']!r}")

    parsed_crlf = drs.parse_bls_entry(bls_entry.replace("\n", "\r\n"))
    if parsed_crlf["state"] != "@os-000001-20261118-120000Z-install":
        failures.append("parse_bls_entry() did not accept CRLF")
    legacy_id = "6.12.117+deb13-amd64-0123456789ab"
    try:
        parsed_legacy = drs.parse_bls_entry(bls_text("@os-000001-20261118-120000Z-install", legacy_id, KVER_A))
    except ValueError as exc:
        failures.append(f"parse_bls_entry() rejected a retained legacy kernel id: {exc}")
    else:
        if parsed_legacy["linux"] != f"/EFI/dr/kernels/{legacy_id}/vmlinuz":
            failures.append("parse_bls_entry() read the wrong legacy kernel path")
    for label, text in (
        ("missing linux", bls_entry.replace("linux /EFI/dr/kernels/", "# linux /EFI/dr/kernels/")),
        ("missing initrd", bls_entry.replace("initrd /EFI/dr/kernels/", "# initrd /EFI/dr/kernels/")),
        ("missing state", bls_entry.replace("rootflags=subvol=@os-000001-20261118-120000Z-install ", "")),
        ("parent traversal", bls_entry.replace(
            f"/EFI/dr/kernels/{KID_A}/vmlinuz",
            "/EFI/dr/kernels/../vmlinuz",
        )),
        ("relative path", bls_entry.replace(
            f"/EFI/dr/kernels/{KID_A}/vmlinuz",
            "EFI/dr/kernels/vmlinuz",
        )),
        ("two kernels", bls_entry.replace(
            "initrd /EFI/dr/kernels/",
            "linux /EFI/dr/kernels/second-vmlinuz\ninitrd /EFI/dr/kernels/",
        )),
        ("two initrds", bls_entry.replace(
            f"initrd /EFI/dr/kernels/{KID_A}/initrd.img\n",
            f"initrd /EFI/dr/kernels/{KID_A}/initrd.img\n"
            f"initrd /EFI/dr/kernels/{KID_A}/initrd.img\n",
        )),
        ("fallback initrd name", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            initrd=f"/EFI/dr/kernels/{KID_A}/initrd-fallback.img",
        )),
        ("kernel with shell variable", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            linux="/EFI/dr/kernels/$missing/vmlinuz",
        )),
        ("kernel with backtick", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            linux="/EFI/dr/kernels/`id`/vmlinuz",
        )),
        ("kernel with parentheses", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            linux="/EFI/dr/kernels/(bad)/vmlinuz",
        )),
        ("invalid kernel id hash", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            linux="/EFI/dr/kernels/6.12.117+deb13-amd64-0123456789a/vmlinuz",
        )),
        ("uppercase kernel id hash", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            linux="/EFI/dr/kernels/6.12.117+deb13-amd64-0123456789AB/vmlinuz",
        )),
        ("kernel id parent traversal", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            linux="/EFI/dr/kernels/6.12.117..evil-0123456789ab/vmlinuz",
        )),
        ("wrong kernel filename", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            linux=f"/EFI/dr/kernels/{KID_A}/vmlinuz.old",
        )),
        ("initrd names another kernel id", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A,
            initrd=f"/EFI/dr/kernels/{KID_B}/initrd.img",
            linux=f"/EFI/dr/kernels/{KID_A}/vmlinuz",
        )),
    ):
        try:
            drs.parse_bls_entry(text)
        except ValueError:
            pass
        else:
            failures.append(f"parse_bls_entry(): {label} accepted")

    # The kernel-upgrade scenario compares the state/kernel pairs in BLS titles.
    if drs.parse_bls_title("title Debian @os-000001-20261118-120000Z-install (kernel 6.12.117+deb13-amd64)") != (
        "@os-000001-20261118-120000Z-install", "6.12.117+deb13-amd64"
    ):
        failures.append("parse_bls_title() read the wrong state/kernel pair")
    for text in ("title Debian (kernel 6.12)", "title other @os-000001-20261118-120000Z-install (kernel 6.12)", "title Debian @os-000001-20261118-120000Z-install"):
        try:
            drs.parse_bls_title(text)
        except ValueError:
            pass
        else:
            failures.append(f"parse_bls_title() accepted malformed title {text!r}")

    # Python's single scratch prefix is detected only in the directories it owns.
    scratch_paths = (
        "/boot/efi/EFI/dr/kernels/.dr-state.tmp.pair",
        "/boot/efi/loader/entries/.dr-state.tmp.entry",
        "/boot/efi/loader/.dr-state.tmp.loader",
        "/boot/efi/EFI/dr/.dr-state.tmp.marker",
        "/.top/.dr-state/.dr-state.tmp.metadata",
    )
    for path in scratch_paths:
        if not drs.is_sdboot_scratch_path(path):
            failures.append(f"scratch shape not detected: {path}")
    for path in (
        "/boot/efi/EFI/dr/kernels/.tmp.not-ours",
        "/boot/efi/EFI/dr/kernels/.tmp.Ab12C",
        "/boot/efi/EFI/dr/kernels/.tmp.Ab12CdEF",
        "/boot/efi/EFI/dr/kernels/.dr-state.tmp.",
        "/boot/efi/foreign/.dr-state.tmp.pair",
        f"/boot/efi/EFI/dr/kernels/{KID_A}/.dr-state.tmp.pair",
        f"/boot/efi/EFI/dr/kernels/{KID_A}/.vmlinuz.tmp.123",
        f"/boot/efi/EFI/dr/kernels/{KID_A}/.initrd.img.tmp.123",
        "/boot/efi/loader/entries/.dr-state-entry.new.backup",
        "/boot/efi/loader/entries/.os-000001-20261118-120000Z-install.conf.new.backup",
        "/boot/efi/loader/entries/.os-000001-20261118-120000Z-install..evil.conf.new.123",
        "/boot/efi/loader/loader.conf.new.backup",
        "/boot/efi/EFI/dr/bootloader.new.backup",
        "/.top/.dr-state/foreign.tmp",
    ):
        if drs.is_sdboot_scratch_path(path):
            failures.append(f"foreign scratch look-alike accepted: {path}")

    # The upgrade baseline treats the binary as mandatory, hashes whichever source files exist,
    # and refuses missing completion, malformed hashes, duplicates or unreadable sources.
    hash_a = "a" * 64
    hash_b = "b" * 64
    good_baseline = (
        f"SDBOOT_BINARY_SHA:{hash_a}\n"
        f"BACKEND_SOURCE_SHA:/etc/dr-state.conf_{hash_b}\n"
        "EFI_BASELINE_DONE:1\n"
    )
    if drs.parse_efi_probe(good_baseline, "systemd-boot") != {
        "binary": hash_a,
        "sources": (f"/etc/dr-state.conf_{hash_b}",),
    }:
        failures.append("parse_efi_probe() did not read a config-only baseline")
    for label, out in (
        ("missing completion", good_baseline.replace("EFI_BASELINE_DONE:1\n", "")),
        ("missing binary", good_baseline.replace(f"SDBOOT_BINARY_SHA:{hash_a}\n", "")),
        ("short binary hash", good_baseline.replace(hash_a, "a" * 63)),
        ("missing source", good_baseline.replace(f"BACKEND_SOURCE_SHA:/etc/dr-state.conf_{hash_b}\n", "")),
        ("unreadable source", good_baseline.replace(
            "EFI_BASELINE_DONE:1\n",
            "BACKEND_SOURCE_UNREADABLE:/boot/efi/EFI/dr/bootloader\nEFI_BASELINE_DONE:1\n",
        )),
        ("duplicate source", good_baseline.replace(
            "EFI_BASELINE_DONE:1\n",
            f"BACKEND_SOURCE_SHA:/etc/dr-state.conf_{hash_b}\nEFI_BASELINE_DONE:1\n",
        )),
    ):
        if drs.parse_efi_probe(out, "systemd-boot") is not None:
            failures.append(f"parse_efi_probe(): {label} accepted")

    # guest_backend reads both sources and applies dr-state's presence/conflict/unknown rules.
    for label, marker, config, want in (
        ("marker grub only", "grub", ABSENT, "grub"),
        ("marker systemd only", "systemd-boot", ABSENT, "systemd-boot"),
        ("config grub only", ABSENT, "BOOTLOADER=grub\n", "grub"),
        ("config systemd only", ABSENT, "BOOTLOADER=systemd-boot\n", "systemd-boot"),
        ("both agree", "systemd-boot", "BOOTLOADER=systemd-boot\n", "systemd-boot"),
        ("both agree on grub", "grub", "BOOTLOADER=grub\n", "grub"),
    ):
        got, fails, raised = capture_scenario(
            lambda m=marker, c=config: drs.guest_backend(backend_guest(m, c), io.StringIO())
        )
        if raised is not None or fails or got != want:
            failures.append(f"guest_backend {label}: got={got!r} fails={fails!r} raised={raised!r}")
    for label, marker, config in (
        ("conflicting sources", "grub", "BOOTLOADER=systemd-boot\n"),
        ("empty config", ABSENT, "BOOTLOADER=\n"),
        ("empty marker", "", ABSENT),
        ("unknown config", ABSENT, "BOOTLOADER=grub2\n"),
        ("unknown marker", "grub2", ABSENT),
        ("duplicate config key", ABSENT, "BOOTLOADER=grub\nBOOTLOADER=grub\n"),
    ):
        got, fails, raised = capture_scenario(
            lambda m=marker, c=config: drs.guest_backend(backend_guest(m, c), io.StringIO())
        )
        if raised is not None or not fails or got is not None:
            failures.append(f"guest_backend {label}: got={got!r} fails={fails!r} raised={raised!r}")
    got, fails, raised = capture_scenario(
        lambda: drs.guest_backend(backend_guest(done=False), io.StringIO())
    )
    if raised is not None or not fails or got is not None:
        failures.append(f"guest_backend omitted tags: got={got!r} fails={fails!r} raised={raised!r}")

    # Execute sdboot-esp against scripted guests: the good config-only backend passes, while each
    # omission/malformed value/foreign entry/scratch shape records a failed check.
    good_entries = [
        ("/boot/efi/loader/entries/os-000001-20261118-120000Z-install.conf", bls_text("@os-000001-20261118-120000Z-install", KID_A, KVER_A)),
        ("/boot/efi/loader/entries/os-000002-20261118-120100Z-apt.conf", bls_text("@os-000002-20261118-120100Z-apt", KID_B, KVER_B)),
    ]

    def sdboot_result(guest):
        return capture_scenario(lambda: drs.scenario_sdboot_esp(guest, io.StringIO()))

    _, good_fails, good_raised = sdboot_result(scenario_guest(entries=good_entries))
    if good_raised is not None or good_fails:
        failures.append(f"sdboot-esp good guest: fails={good_fails!r} raised={good_raised!r}")

    _, old_default_fails, old_default_raised = sdboot_result(scenario_guest(
        entries=good_entries,
        loader="default os-000001-20261118-120000Z-install\ntimeout 5\n",
    ))
    if old_default_raised is not None or not old_default_fails:
        failures.append(
            "sdboot-esp suffix-less loader.conf default: "
            f"fails={old_default_fails!r} raised={old_default_raised!r}"
        )

    _, missing_default_fails, missing_default_raised = sdboot_result(scenario_guest(
        entries=good_entries,
        loader="default os-9999.conf\ntimeout 5\n",
    ))
    if missing_default_raised is not None or not missing_default_fails:
        failures.append(
            "sdboot-esp loader.conf default names a missing entry file: "
            f"fails={missing_default_fails!r} raised={missing_default_raised!r}"
        )

    fabricated_entries = [
        good_entries[0],
        ("/boot/efi/loader/entries/foreign.conf", bls_text("@os-000002-20261118-120100Z-apt", KID_B, KVER_B)),
    ]
    _, fabricated_fails, fabricated_raised = sdboot_result(
        scenario_guest(entries=fabricated_entries)
    )
    if fabricated_raised is not None or not fabricated_fails:
        failures.append(
            f"sdboot-esp fabricated entry id: fails={fabricated_fails!r} raised={fabricated_raised!r}"
        )

    swapped_entries = [
        ("/boot/efi/loader/entries/os-000001-20261118-120000Z-install.conf", bls_text("@os-000002-20261118-120100Z-apt", KID_B, KVER_B)),
        ("/boot/efi/loader/entries/os-000002-20261118-120100Z-apt.conf", bls_text("@os-000001-20261118-120000Z-install", KID_A, KVER_A)),
    ]
    _, swapped_fails, swapped_raised = sdboot_result(scenario_guest(
        states=("@os-000002-20261118-120100Z-apt", "@os-000001-20261118-120000Z-install"),
        default="@os-000002-20261118-120100Z-apt",
        entries=swapped_entries,
    ))
    if swapped_raised is not None or not swapped_fails:
        failures.append(
            f"sdboot-esp entry id/state mapping: fails={swapped_fails!r} raised={swapped_raised!r}"
        )

    wrong_title_entries = [
        good_entries[0],
        (
            "/boot/efi/loader/entries/os-000002-20261118-120100Z-apt.conf",
            good_entries[1][1].replace(
                "title Debian @os-000002-20261118-120100Z-apt",
                "title Debian @os-000001-20261118-120000Z-install",
                1,
            ),
        ),
    ]
    _, title_fails, title_raised = sdboot_result(scenario_guest(entries=wrong_title_entries))
    if title_raised is not None or not title_fails:
        failures.append(f"sdboot-esp title/state mismatch: fails={title_fails!r} raised={title_raised!r}")

    wrong_marker_entries = [
        good_entries[0],
        (
            "/boot/efi/loader/entries/os-000002-20261118-120100Z-apt.conf",
            good_entries[1][1].replace(
                "# generated by dr-state sdboot; do not edit",
                "# generated by another bootloader",
                1,
            ),
        ),
    ]
    _, marker_fails, marker_raised = sdboot_result(scenario_guest(entries=wrong_marker_entries))
    if marker_raised is not None or not marker_fails:
        failures.append(f"sdboot-esp BLS marker mismatch: fails={marker_fails!r} raised={marker_raised!r}")

    _, tag_fails, tag_raised = sdboot_result(scenario_guest(
        entries=good_entries,
        file_paths={
            f"/boot/efi/EFI/dr/kernels/{KID_A}/vmlinuz",
            f"/boot/efi/EFI/dr/kernels/{KID_B}/vmlinuz",
            f"/boot/efi/EFI/dr/kernels/{KID_B}/initrd.img",
        },
    ))
    if tag_raised is not None or not tag_fails:
        failures.append(f"sdboot-esp omitted file tag: fails={tag_fails!r} raised={tag_raised!r}")

    _, extra_fails, extra_raised = sdboot_result(scenario_guest(
        entries=good_entries,
        file_extra=("/boot/efi/EFI/dr/kernels/$missing/vmlinuz",),
    ))
    if extra_raised is not None or not extra_fails:
        failures.append(f"sdboot-esp unexpected file tag: fails={extra_fails!r} raised={extra_raised!r}")

    _, file_done_fails, file_done_raised = sdboot_result(scenario_guest(
        entries=good_entries,
        file_done=False,
    ))
    if file_done_raised is not None or not file_done_fails:
        failures.append(f"sdboot-esp omitted file scan tag: fails={file_done_fails!r} raised={file_done_raised!r}")

    _, file_rc_fails, file_rc_raised = sdboot_result(scenario_guest(
        entries=good_entries,
        file_rc=1,
    ))
    if file_rc_raised is not None or not file_rc_fails:
        failures.append(f"sdboot-esp nonzero file scan rc: fails={file_rc_fails!r} raised={file_rc_raised!r}")

    empty_entries = [("/boot/efi/loader/entries/os-000001-20261118-120000Z-install.conf", "")]
    _, empty_fails, empty_raised = sdboot_result(scenario_guest(entries=empty_entries, states=("@os-000001-20261118-120000Z-install",),
                                                               default="@os-000001-20261118-120000Z-install"))
    if empty_raised is not None or not empty_fails:
        failures.append(f"sdboot-esp empty entry: fails={empty_fails!r} raised={empty_raised!r}")

    foreign_entries = good_entries + [
        ("/boot/efi/loader/entries/foreign.conf", bls_text("@os-foreign", KID_A, KVER_A)),
    ]
    _, foreign_fails, foreign_raised = sdboot_result(scenario_guest(entries=foreign_entries))
    if foreign_raised is not None or not foreign_fails:
        failures.append(f"sdboot-esp foreign entry: fails={foreign_fails!r} raised={foreign_raised!r}")

    bad_path_entries = [
        ("/boot/efi/loader/entries/os-000001-20261118-120000Z-install.conf", bls_text(
            "@os-000001-20261118-120000Z-install", KID_A, KVER_A, linux="/EFI/dr/kernels/$missing/vmlinuz",
        )),
        good_entries[1],
    ]
    _, path_fails, path_raised = sdboot_result(scenario_guest(entries=bad_path_entries))
    if path_raised is not None or not path_fails:
        failures.append(f"sdboot-esp foreign path: fails={path_fails!r} raised={path_raised!r}")

    for path in scratch_paths:
        _, scratch_fails, scratch_raised = sdboot_result(scenario_guest(entries=good_entries, scratch=(path,)))
        if scratch_raised is not None or not scratch_fails:
            failures.append(f"sdboot-esp scratch {path}: fails={scratch_fails!r} raised={scratch_raised!r}")
    _, scan_fails, scan_raised = sdboot_result(scenario_guest(entries=good_entries, scratch_done=False))
    if scan_raised is not None or not scan_fails:
        failures.append(f"sdboot-esp omitted scan tag: fails={scan_fails!r} raised={scan_raised!r}")
    _, scan_rc_fails, scan_rc_raised = sdboot_result(scenario_guest(entries=good_entries, scratch_rc=1))
    if scan_rc_raised is not None or not scan_rc_fails:
        failures.append(f"sdboot-esp nonzero scratch scan rc: fails={scan_rc_fails!r} raised={scan_rc_raised!r}")

    # s2-s3 must derive menu states from canonical marked entries, not merely count *.conf files.
    s2_state0 = "@os-000001-20261118-120000Z-install"
    s2_pre = "@os-000605-20261118-131158Z-apt"
    dr_state_payload = drs.payload_archive()
    dr_state_sha = drs.payload_manifest_digest()
    apt_hook_source = drs.current_apt_hook_config()
    apt_hook_sha = hashlib.sha256(apt_hook_source).hexdigest()
    s2_reboots = 0
    s2_list_calls = 0

    def s2_s3_guest(
        menu_entries,
        loader="default os-000001-20261118-120000Z-install.conf\ntimeout 5\n",
    ):
        nonlocal s2_reboots, s2_list_calls
        s2_reboots = 0
        s2_list_calls = 0

        def responder(cmd):
            nonlocal s2_reboots, s2_list_calls
            if "BACKEND_SOURCES_DONE" in cmd:
                return backend_output(config="BOOTLOADER=systemd-boot\n")
            if "DR_STATE_B64" in cmd:
                match = re.search(r"<<'DR_STATE_B64'\n(.*?)DR_STATE_B64\n", cmd, re.S)
                if match is None or base64.b64decode("".join(match.group(1).split()), validate=True) != dr_state_payload:
                    raise AssertionError("s2-s3 did not upload the exact current dr-state payload")
                return 0, f"DRSTATE_SHA:{dr_state_sha}\nDRSTATE_INSTALL_RC:0\n"
            if "APT_HOOK_B64" in cmd:
                match = re.search(r"<<'APT_HOOK_B64'\n(.*?)APT_HOOK_B64\n", cmd, re.S)
                if match is None or base64.b64decode("".join(match.group(1).split()), validate=True) != apt_hook_source:
                    raise AssertionError("s2-s3 did not upload the exact current APT hook config")
                return 0, f"APTHOOK_SHA:{apt_hook_sha}\nAPTHOOK_INSTALL_RC:0\n"
            if cmd == "dr-state list":
                return 0, f"@os-000001-20261118-120000Z-install (default) kernel={KVER_A}\n{s2_pre} kernel={KVER_B} created=2026-11-18T13:11:58Z reason=apt os=Debian GNU/Linux 13 changes=install hello - -> 2.10-3\n"
            if cmd == f"dr-state show {s2_pre}":
                return 0, (
                    f"state: {s2_pre}\nsequence: 000605\norigin-slug: apt\nsource: apt\nreason: apt\n"
                    "created-utc: 2026-11-18T13:11:58Z\n"
                    "os-at-snapshot: Debian GNU/Linux 13 (fixture)\n"
                    f"running-kernel-at-snapshot: {KVER_A}\n"
                    "key-package-versions:\napt\t3.0\ndpkg\t1.22\n"
                    "apt-package-actions:\ninstall hello - -> 2.10-3\n"
                )
            if "dr-state list" in cmd and "STATES:" in cmd:
                s2_list_calls += 1
                states = (s2_state0,) if s2_list_calls == 1 else (s2_state0, s2_pre)
                return 0, "STATES:" + ",".join(states) + "\n"
            if "DEF:" in cmd:
                return 0, f"DEF:{s2_state0}\n"
            if "LOADER_B64" in cmd:
                return 0, f"LOADER_B64:{b64(loader)}\n"
            if "BLS_B64" in cmd:
                return entry_sources_output(menu_entries)
            if "SELECTION_EVIDENCE_DONE" in cmd:
                required = (
                    "bootctl list --no-pager",   # a pager on the serial console hangs the guest (sd-g5)
                    "LoaderEntrySelected-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f",
                    "BOOTCTL_LIST_BEGIN",
                    "BOOTCTL_LIST_END",
                    "LOADER_ENTRY_SELECTED",
                    "SELECTION_EVIDENCE_DONE",
                )
                missing = [fragment for fragment in required if fragment not in cmd]
                if missing:
                    raise AssertionError(f"selection evidence command missing {missing}: {cmd}")
                return 0, (
                    "BOOTCTL_LIST_BEGIN:1\n"
                    "id: os-000001-20261118-120000Z-install.conf\n"
                    "selected: no\n"
                    "default: yes\n"
                    f"id: {s2_pre.removeprefix('@')}.conf\n"
                    "selected: yes\n"
                    "default: no\n"
                    "BOOTCTL_LIST_RC:0\n"
                    "BOOTCTL_LIST_END:1\n"
                    f"LOADER_ENTRY_SELECTED:{s2_pre.removeprefix('@')}.conf\n"
                    "SELECTION_EVIDENCE_DONE:1\n"
                )
            if "find /boot/efi/loader/entries" in cmd:
                return 0, f"MENU:{len(menu_entries)}\n"
            if "APTRC:" in cmd:
                return 0, "APTRC:0\nHELLO:/usr/bin/hello\n"
            if "dr-state boot-once" in cmd:
                return 0, "BORC:0\n"
            if "SDRC:" in cmd:
                return 0, "SDRC:0\n"
            if "KERNEL:" in cmd:
                return 0, f"KERNEL:{KVER_A}\n"
            if "MK:" in cmd:
                return 0, "MK:m1_m2_m3_etc1\n"
            if "HOME:" in cmd and "SRV:" in cmd:
                return 0, (
                    f"CMDLINE:rootflags=subvol={s2_pre} ro\n"
                    "HELLO:none\nHOME:m1+m1b+\nSRV:m2\nDOCKER:m3\nETC:etc1+\n"
                )
            if "CMDLINE:" in cmd:
                state = s2_pre if s2_reboots in (1, 3) else s2_state0
                return 0, f"CMDLINE:rootflags=subvol={state} ro\n"
            if "echo m1 > /home/marker" in cmd or "echo etc2 >> /etc/dr-etc-marker" in cmd:
                return 0, ""
            raise AssertionError(f"unexpected s2-s3 command: {cmd}")

        guest = ScriptedGuest(responder)

        def s2_reboot(timeout=1200):
            nonlocal s2_reboots
            s2_reboots += 1
            return f"s2-boot-{s2_reboots}"

        guest.reboot = s2_reboot
        return guest

    s2_good_entries = [
        ("/boot/efi/loader/entries/os-000001-20261118-120000Z-install.conf", bls_text(s2_state0, KID_A, KVER_A)),
        (f"/boot/efi/loader/entries/{s2_pre.removeprefix('@')}.conf",
         bls_text(s2_pre, KID_B, KVER_B)),
    ]
    s2_good_guest = s2_s3_guest(s2_good_entries)
    _, s2_good_fails, s2_good_raised = capture_scenario(
        lambda: drs.scenario_s2_s3(s2_good_guest, io.StringIO())
    )
    if s2_good_raised is not None or s2_good_fails:
        failures.append(f"s2-s3 good guest: fails={s2_good_fails!r} raised={s2_good_raised!r}")
    if not any("BOOTCTL_LIST_BEGIN" in cmd for cmd in s2_good_guest.commands):
        failures.append("s2-s3 did not record bootctl list selection/default evidence")
    if not any("LoaderEntrySelected-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f" in cmd
               for cmd in s2_good_guest.commands):
        failures.append("s2-s3 did not record LoaderEntrySelected efivar evidence")

    _, s2_old_default_fails, s2_old_default_raised = capture_scenario(
        lambda: drs.scenario_s2_s3(
            s2_s3_guest(s2_good_entries, loader="default os-000001-20261118-120000Z-install\ntimeout 5\n"),
            io.StringIO(),
        )
    )
    if s2_old_default_raised is not None or not s2_old_default_fails:
        failures.append(
            "s2-s3 suffix-less loader.conf default: "
            f"fails={s2_old_default_fails!r} raised={s2_old_default_raised!r}"
        )

    # A standalone scenario proves the one-shot is gone from the userspace reached by the reboot.
    if not hasattr(drs, "scenario_sdboot_oneshot_consumed"):
        failures.append("sdboot-oneshot-consumed scenario is missing")
    else:
        def oneshot_guest(
            absent=True,
            done=True,
            rc=0,
            running="@os-000001-20261118-120000Z-install",
            default="@os-000001-20261118-120000Z-install",
            target="@os-000002-20261118-120100Z-apt",
            initial_states=None,
            snapshot_rc=0,
            snapshot_target="@os-000002-20261118-120100Z-manual",
        ):
            boots = 0
            selected_target = [target]
            states = list(initial_states) if initial_states is not None else [
                "@os-000001-20261118-120000Z-install", target,
            ]

            def responder(cmd):
                if "BACKEND_SOURCES_DONE" in cmd:
                    return backend_output(config="BOOTLOADER=systemd-boot\n")
                if "dr-state list" in cmd and "STATES:" in cmd:
                    return 0, "STATES:" + ",".join(states) + "\n"
                if "dr-state snapshot --note" in cmd:
                    if snapshot_rc == 0 and snapshot_target not in states:
                        states.append(snapshot_target)
                    return 0, f"SNAPRC:{snapshot_rc}\n"
                if "DEF:" in cmd:
                    return 0, f"DEF:{default}\n"
                if "dr-state boot-once" in cmd:
                    selected_target[0] = cmd.split("dr-state boot-once ", 1)[1].split(";", 1)[0].strip()
                    return 0, "BORC:0\n"
                if "ONESHOT_SCAN_DONE" in cmd:
                    required = (
                        "/sys/firmware/efi/efivars",
                        "LoaderEntryOneShot-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f",
                        "ONESHOT_SCAN_DONE",
                    )
                    missing = [fragment for fragment in required if fragment not in cmd]
                    if missing:
                        raise AssertionError(f"one-shot scan missing {missing}: {cmd}")
                    lines = []
                    if absent:
                        lines.append("ONESHOT_ABSENT:1")
                    else:
                        lines.append("ONESHOT_PRESENT:1")
                    if done:
                        lines.append("ONESHOT_SCAN_DONE:1")
                    return rc, "\n".join(lines) + "\n"
                if "CMDLINE:" in cmd:
                    state = selected_target[0] if boots else running
                    return 0, f"CMDLINE:rootflags=subvol={state} ro\n"
                raise AssertionError(f"unexpected one-shot command: {cmd}")

            guest = ScriptedGuest(responder)

            def reboot(timeout=1200):
                nonlocal boots
                boots += 1
                return f"oneshot-boot-{boots}"

            guest.reboot = reboot
            return guest

        one_good_guest = oneshot_guest()
        _, one_good_fails, one_good_raised = capture_scenario(
            lambda: drs.scenario_sdboot_oneshot_consumed(one_good_guest, io.StringIO())
        )
        if one_good_raised is not None or one_good_fails:
            failures.append(
                f"sdboot-oneshot-consumed good guest: fails={one_good_fails!r} raised={one_good_raised!r}"
            )
        if not any("dr-state boot-once @os-000002-20261118-120100Z-apt" in cmd for cmd in one_good_guest.commands):
            failures.append("sdboot-oneshot-consumed did not select a state distinct from the default")

        fresh_guest = oneshot_guest(initial_states=("@os-000001-20261118-120000Z-install",))
        _, fresh_fails, fresh_raised = capture_scenario(
            lambda: drs.scenario_sdboot_oneshot_consumed(fresh_guest, io.StringIO())
        )
        if fresh_raised is not None or fresh_fails:
            failures.append(
                f"sdboot-oneshot-consumed fresh guest: fails={fresh_fails!r} raised={fresh_raised!r}"
            )
        if not any("dr-state snapshot --note 'one-shot consumption acceptance fixture'" in cmd
                   for cmd in fresh_guest.commands):
            failures.append("sdboot-oneshot-consumed did not create a fixture snapshot on a fresh guest")

        snapshot_failure_guest = oneshot_guest(
            initial_states=("@os-000001-20261118-120000Z-install",), snapshot_rc=1,
        )
        _, snapshot_fails, snapshot_raised = capture_scenario(
            lambda: drs.scenario_sdboot_oneshot_consumed(snapshot_failure_guest, io.StringIO())
        )
        if snapshot_raised is not None or not snapshot_fails:
            failures.append(
                "sdboot-oneshot-consumed accepted a failed fixture snapshot: "
                f"fails={snapshot_fails!r} raised={snapshot_raised!r}"
            )

        non_manual_snapshot_guest = oneshot_guest(
            initial_states=("@os-000001-20261118-120000Z-install",),
            snapshot_target="@os-000002-20261118-120100Z-apt",
        )
        _, non_manual_fails, non_manual_raised = capture_scenario(
            lambda: drs.scenario_sdboot_oneshot_consumed(non_manual_snapshot_guest, io.StringIO())
        )
        if non_manual_raised is not None or not non_manual_fails:
            failures.append(
                "sdboot-oneshot-consumed accepted a non-manual fixture snapshot: "
                f"fails={non_manual_fails!r} raised={non_manual_raised!r}"
            )
        if any("dr-state boot-once " in cmd for cmd in non_manual_snapshot_guest.commands):
            failures.append("sdboot-oneshot-consumed selected a fixture with a non-manual reason slug")

        _, reused_fails, reused_raised = capture_scenario(
            lambda: drs.scenario_sdboot_oneshot_consumed(
                oneshot_guest(running="@os-000002-20261118-120100Z-apt", default="@os-000001-20261118-120000Z-install", target="@os-000001-20261118-120000Z-install"),
                io.StringIO(),
            )
        )
        if reused_raised is not None or not reused_fails or not any(
            "running state is the default" in failure for failure in reused_fails
        ):
            failures.append(
                "sdboot-oneshot-consumed non-default running state was accepted: "
                f"fails={reused_fails!r} raised={reused_raised!r}"
            )
        for label, guest in (
            ("variable still present", oneshot_guest(absent=False)),
            ("missing completion tag", oneshot_guest(done=False)),
            ("scan failed", oneshot_guest(rc=1)),
        ):
            _, one_fails, one_raised = capture_scenario(
                lambda fake=guest: drs.scenario_sdboot_oneshot_consumed(fake, io.StringIO())
            )
            if one_raised is not None or not one_fails:
                failures.append(
                    f"sdboot-oneshot-consumed {label}: fails={one_fails!r} raised={one_raised!r}"
                )

    s2_fabricated_entries = [
        s2_good_entries[0],
        ("/boot/efi/loader/entries/foreign.conf", bls_text(s2_pre, KID_B, KVER_B)),
    ]
    _, s2_fabricated_fails, s2_fabricated_raised = capture_scenario(
        lambda: drs.scenario_s2_s3(s2_s3_guest(s2_fabricated_entries), io.StringIO())
    )
    if s2_fabricated_raised is not None or not s2_fabricated_fails:
        failures.append(
            f"s2-s3 fabricated menu entry: fails={s2_fabricated_fails!r} raised={s2_fabricated_raised!r}"
        )

    s2_wrong_state_entries = [
        s2_good_entries[0],
        ("/boot/efi/loader/entries/os-9999.conf", bls_text("@os-9999", KID_B, KVER_B)),
    ]
    _, s2_wrong_state_fails, s2_wrong_state_raised = capture_scenario(
        lambda: drs.scenario_s2_s3(s2_s3_guest(s2_wrong_state_entries), io.StringIO())
    )
    if s2_wrong_state_raised is not None or not s2_wrong_state_fails:
        failures.append(
            f"s2-s3 menu state set: fails={s2_wrong_state_fails!r} raised={s2_wrong_state_raised!r}"
        )

    # A config-only systemd-boot install has no ESP marker. The kernel flow must baseline the
    # independently required systemd-boot binary and hash only the backend source that exists.
    state0 = "@os-000001-20261118-120000Z-install"
    pre = "@os-000605-20261118-131158Z-manual-kernel"
    reboots = 0
    list_calls = 0
    baseline = (
        "SDBOOT_BINARY_SHA:" + "1" * 64 + "\n"
        "BACKEND_SOURCE_SHA:/etc/dr-state.conf_" + "2" * 64 + "\n"
        "EFI_BASELINE_DONE:1\n"
    )
    upgraded_entries = [
        ("/boot/efi/loader/entries/os-000001-20261118-120000Z-install.conf", bls_text(state0, KID_B, KVER_B)),
        (
            "/boot/efi/loader/entries/os-000605-20261118-131158Z-manual-kernel.conf",
            bls_text(pre, KID_A, KVER_A),
        ),
    ]

    def kernel_responder(cmd):
        nonlocal reboots, list_calls
        if "BACKEND_SOURCES_DONE" in cmd:
            return backend_output(config="BOOTLOADER=systemd-boot\n")
        if "DRSTATE_SHA:" in cmd:
            return 0, f"DRSTATE_SHA:{drs.payload_manifest_digest()}\n"
        if "EFI_BASELINE_DONE" in cmd:
            required = (
                "/boot/efi/EFI/systemd/systemd-bootx64.efi",
                "/etc/dr-state.conf",
                "/boot/efi/EFI/dr/bootloader",
                'for f in /etc/dr-state.conf /boot/efi/EFI/dr/bootloader',
                'if [ -e "$f" ]; then',
                "SDBOOT_BINARY_SHA",
                "BACKEND_SOURCE_SHA",
                "EFI_BASELINE_DONE",
            )
            missing = [fragment for fragment in required if fragment not in cmd]
            if missing:
                raise AssertionError(f"EFI baseline command missing {missing}: {cmd}")
            if any(fragment in cmd for fragment in (
                "test -s /boot/efi/EFI/dr/bootloader",
                "[ -s /boot/efi/EFI/dr/bootloader ]",
            )):
                raise AssertionError(f"EFI baseline hard-requires the optional ESP marker: {cmd}")
            return 0, baseline
        if "EFIFILES:" in cmd:
            # a config-only guest has no marker, so the old marker-gated probe is empty
            return 0, "EFIFILES:\n"
        if "dr-state list" in cmd and "STATES:" in cmd:
            list_calls += 1
            states = (state0,) if list_calls == 1 else (state0, pre)
            return 0, "STATES:" + ",".join(states) + "\n"
        if "DEF:" in cmd:
            return 0, f"DEF:{state0}\n"
        if "SDBOOT_HOOKS_OK" in cmd:
            return 0, (
                "SDBOOT_HOOK:postinst:diverted\n"
                "SDBOOT_HOOK:postrm:diverted\n"
                "SDBOOT_HOOKS_OK:1\n"
            )
        if "SDBOOT_ENTRY_PLUGIN_OK" in cmd:
            required = (
                "/usr/lib/kernel/install.d/90-loaderentry.install",
                "/etc/kernel/install.d/90-loaderentry.install",
                '"$(readlink "$override")" = /dev/null',
                "SDBOOT_ENTRY_PLUGIN_OK",
            )
            missing = [fragment for fragment in required if fragment not in cmd]
            if missing:
                raise AssertionError(f"kernel-install plugin probe missing {missing}: {cmd}")
            return 0, "SDBOOT_ENTRY_PLUGIN:disabled\nSDBOOT_ENTRY_PLUGIN_OK:1\n"
        if "BLS_B64" in cmd:
            return entry_sources_output(upgraded_entries)
        if "dr-updates.list" in cmd:
            return 0, ""
        if "OFFER:" in cmd:
            return 0, "UPDRC:0\nOFFER:1\n"
        if "UPGRC:" in cmd:
            return 0, (
                "UPGRC:0\n"
                "ARCHKB:1234\n"
                f"KNEW:/usr/lib/modules/{KVER_B}/vmlinuz-{KVER_B}\n"
                f"BOOTFILES:vmlinuz-{KVER_A},initrd.img-{KVER_A},vmlinuz-{KVER_B},initrd.img-{KVER_B},\n"
            )
        if "dr-state boot-once" in cmd:
            return 0, "BORC:0\n"
        if "MODRC:" in cmd:
            return 0, f"KERNEL:{KVER_A}\nCMDLINE:rootflags=subvol={pre} ro\nMODDIRS:{KVER_A},\nMODRC:0\nLOOP:1\n"
        if "CMDLINE:" in cmd and "KERNEL:" in cmd:
            if reboots in (1, 3):
                return 0, f"KERNEL:{KVER_B}\nCMDLINE:rootflags=subvol={state0} ro\n"
            return 0, f"KERNEL:{KVER_A}\nCMDLINE:rootflags=subvol={state0} ro\n"
        raise AssertionError(f"unexpected kernel-flow command: {cmd}")

    kernel_guest = ScriptedGuest(kernel_responder)

    def kernel_reboot(timeout=1200):
        nonlocal reboots
        reboots += 1
        return f"boot-{reboots}"

    kernel_guest.reboot = kernel_reboot
    _, kernel_fails, kernel_raised = capture_scenario(
        lambda: drs._kernel_flow(
            kernel_guest,
            io.StringIO(),
            drs.UPDATES_SOURCES,
            drs.OFFER_UPGRADABLE,
            drs.DIST_UPGRADE,
        )
    )
    if kernel_raised is not None or kernel_fails:
        failures.append(f"kernel-upgrade config-only systemd-boot: fails={kernel_fails!r} raised={kernel_raised!r}")

    if not any("SDBOOT_HOOKS_OK" in command for command in kernel_guest.commands):
        failures.append("kernel-upgrade did not verify the systemd-boot kernel-hook diversions before upgrading")
    if not any("SDBOOT_ENTRY_PLUGIN_OK" in command for command in kernel_guest.commands):
        failures.append("kernel-upgrade did not verify the systemd kernel-install BLS override before upgrading")
    if not any("DRSTATE_SHA:" in command for command in kernel_guest.commands):
        failures.append("kernel-upgrade did not verify the guest's dr-state source before upgrading")

    for index, command in enumerate(kernel_guest.commands, start=1):
        if command.rstrip().endswith(";"):
            failures.append(f"kernel-upgrade command {index} ends in a semicolon: {command[-160:]!r}")
        wrapped = drs.marker_wrapped_command(command)
        syntax = subprocess.run(["bash", "-n"], input=wrapped, text=True, capture_output=True, check=False)
        if syntax.returncode != 0:
            failures.append(
                f"kernel-upgrade command {index} is not bash -n clean after marker wrapping: "
                f"{syntax.stderr.strip()!r}; command tail={command[-160:]!r}"
            )

    # reboot proof: the boot id must change, and the prompt must come from the NEW boot
    def reboot_guest(first_id, second_id):
        a, b = socket.socketpair()
        a.setblocking(False)
        gg = drs.Guest("unused", io.StringIO())
        gg.sock = a

        def rv(needle: bytes) -> None:
            b.settimeout(30)
            data = b""
            while needle not in data:
                data += b.recv(512)

        def serve():
            rv(b"boot_id")
            b.sendall(b"echo BOOTID:$(cat /proc/sys/kernel/random/boot_id)\r\nBOOTID:" + first_id + b"\r\n__DR_END_0__\r\n")
            rv(b"reboot\n")
            b.sendall(b"[ OK ] Stopped systemd-logind.service - User Login Management.\r\nReached target reboot.target\r\n")
            b.sendall(b"drpoc login: ")
            rv(b"root\n")
            b.sendall(b"Password: ")
            rv(b"root\n")
            rv(b"__DR_READY_")
            b.sendall(b"__DR_READY_42__\r\n")
            rv(b"boot_id")
            b.sendall(b"BOOTID:" + second_id + b"\r\n__DR_END_0__\r\n")

        threading.Thread(target=serve, daemon=True).start()
        return gg

    if reboot_guest(b"aaaa-1111", b"bbbb-2222").reboot(60) != "bbbb-2222":
        failures.append("reboot did not return the new boot id")
    try:
        reboot_guest(b"same-id", b"same-id").reboot(60)
        failures.append("a reboot with an unchanged boot id was accepted")
    except RuntimeError:
        pass

    # QMP: greeting, capabilities, an event before the reply, then the reply
    import json

    with tempfile.TemporaryDirectory() as tmp:
        qpath = os.path.join(tmp, "qmp.sock")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(qpath)
        srv.listen(1)
        seen = []

        def qmp_server():
            conn, _ = srv.accept()
            f = conn.makefile("rw")
            f.write(json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\n")
            f.flush()
            for _ in range(2):
                req = json.loads(f.readline())
                seen.append(req["execute"])
                if req["execute"] == "system_reset":
                    f.write(json.dumps({"event": "RESET", "data": {}}) + "\n")
                f.write(json.dumps({"return": {}}) + "\n")
                f.flush()
            conn.close()

        threading.Thread(target=qmp_server, daemon=True).start()
        reply = drs.qmp_command(qpath, "system_reset", timeout=10)
        if reply != {"return": {}} or seen != ["qmp_capabilities", "system_reset"]:
            failures.append(f"qmp_command: reply={reply!r} seen={seen!r}")
        srv.close()

    # QMP command arguments survive the protocol round trip for virtual-keyboard menu selection.
    with tempfile.TemporaryDirectory() as tmp:
        qpath = os.path.join(tmp, "qmp.sock")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(qpath)
        srv.listen(1)
        seen = []

        def qmp_key_server():
            conn, _ = srv.accept()
            f = conn.makefile("rw")
            f.write(json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\n")
            f.flush()
            for expected in ("qmp_capabilities", "send-key"):
                req = json.loads(f.readline())
                seen.append(req)
                if req["execute"] != expected:
                    break
                f.write(json.dumps({"return": {}}) + "\n")
                f.flush()
            conn.close()

        threading.Thread(target=qmp_key_server, daemon=True).start()
        key_args = {"keys": [{"type": "qcode", "data": "down"}], "hold-time": 100}
        reply = drs.qmp_command(qpath, "send-key", arguments=key_args, timeout=10)
        if reply != {"return": {}} or len(seen) != 2 or seen[-1] != {"execute": "send-key", "arguments": key_args}:
            failures.append(f"qmp_command send-key: reply={reply!r} seen={seen!r}")
        srv.close()

    # Recovery scenarios must exercise a human boot-menu selection. A boot-once staged before damage would not
    # cover the operator path requested for an unusable default environment.
    for scenario_name in (
        "scenario_recovery_kernel", "scenario_recovery_library", "scenario_recovery_dependency"
    ):
        scenario = getattr(drs, scenario_name, None)
        if scenario is None:
            failures.append(f"{scenario_name} is missing")
            continue
        source = inspect.getsource(scenario)
        if "select_older_entry_from_boot_menu" not in source or "boot-once" in source:
            failures.append(f"{scenario_name} does not use a manual boot-menu recovery path")
        if "delete_recovery_state" not in source:
            failures.append(f"{scenario_name} does not exercise cleanup of the failed state")

    dependency_source = inspect.getsource(drs.scenario_recovery_dependency)
    for required in (
        "package_dependency_fixture_setup_command",
        "package_dependency_damage_command",
        "recovery_persistent_marker",
        "set_recovery_default",
    ):
        if required not in dependency_source:
            failures.append(f"recovery-dependency omits required acceptance step {required!r}")
    setup_command = drs.package_dependency_fixture_setup_command()
    damage_command = drs.package_dependency_damage_command()
    for label, command in (("dependency fixture setup", setup_command), ("dependency damage", damage_command)):
        if command.rstrip().endswith(";"):
            failures.append(f"{label} ends in a semicolon before the serial completion marker")
        syntax = subprocess.run(
            ["bash", "-n"], input=drs.marker_wrapped_command(command), text=True, capture_output=True, check=False
        )
        if syntax.returncode != 0:
            failures.append(f"{label} is not valid after serial marker wrapping: {syntax.stderr.strip()!r}")
    for required in (
        "dr-rollback-fixture-library",
        "Depends: dr-rollback-fixture-library (= 1.0)",
        "systemctl enable --now dr-rollback-fixture.service",
        "apt-get check",
    ):
        if required not in setup_command:
            failures.append(f"dependency fixture setup omits {required!r}")
    for required in (
        "dpkg --force-depends --remove dr-rollback-fixture-library",
        "apt-get check",
        "APT_BROKEN_DEP:1",
        "dr-rollback-fixture-app",
        "systemctl restart dr-rollback-fixture.service",
    ):
        if required not in damage_command:
            failures.append(f"dependency damage command omits {required!r}")

    move_source = inspect.getsource(drs.move_package_file_command)
    if "rename($path, $backup)" not in move_source or "$dir->sync" not in move_source:
        failures.append("package damage helper must rename one exact file and fsync its parent directory")
    with tempfile.TemporaryDirectory() as temp_root:
        root = Path(temp_root) / "package path with spaces"
        root.mkdir()
        path = root / "ld-linux-x86-64.so.2"
        path.write_bytes(b"known-good loader payload\n")
        command = drs.move_package_file_command(path)
        argv = drs.shlex.split(command)
        if (argv[:4] != ["perl", "-MIO::Handle", "-MFcntl=O_RDONLY,O_DIRECTORY", "-e"]
                or "rename($path, $backup)" not in argv[4] or "$dir->sync" not in argv[4]
                or argv[-3:] != [str(path), f"{path}.dr-acceptance-original", str(root)]):
            failures.append(f"package damage command lost its exact paths or fsync contract: {argv!r}")
        syntax = subprocess.run(
            ["bash", "-n"], input=drs.marker_wrapped_command(command), text=True, capture_output=True, check=False
        )
        if syntax.returncode != 0:
            failures.append(f"package damage command is not safely marker-wrapped: {syntax.stderr.strip()!r}")

    dr_state_payload = drs.payload_archive()
    dr_state_sha = drs.payload_manifest_digest()

    def dr_state_install_responder(command):
        match = re.search(r"<<'DR_STATE_B64'\n(.*?)DR_STATE_B64\n", command, re.S)
        if not match:
            raise AssertionError("recovery guest did not receive dr-state through a delimited payload")
        decoded = base64.b64decode("".join(match.group(1).split()), validate=True)
        if decoded != dr_state_payload:
            raise AssertionError("recovery guest payload differs from the current dr-state runtime tree")
        return 0, f"DRSTATE_SHA:{dr_state_sha}\nDRSTATE_INSTALL_RC:0\n"

    dr_state_installer = ScriptedGuest(dr_state_install_responder)
    with mock.patch.dict(os.environ, {}, clear=True):
        _, install_fails, install_raised = capture_scenario(
            lambda: drs.install_current_dr_state(dr_state_installer, io.StringIO())
        )
    if install_raised is not None or install_fails or len(dr_state_installer.commands) != 1:
        failures.append(
            f"recovery guest dr-state install: fails={install_fails!r} raised={install_raised!r}"
        )
    if "--same-owner" not in dr_state_installer.commands[0]:
        failures.append("recovery guest dr-state install does not extract the root-owned archive as root")

    bad_digest_guest = ScriptedGuest(
        lambda command: (0, f"DRSTATE_SHA:{'0' * 64}\n")
    )
    _, digest_fails, digest_raised = capture_scenario(
        lambda: drs.verify_current_dr_state(bad_digest_guest, io.StringIO())
    )
    if digest_raised is not None or not digest_fails:
        failures.append(
            "complete installed payload mismatch was accepted: "
            f"fails={digest_fails!r} raised={digest_raised!r}"
        )

    # silence times out instead of hanging
    a, b = socket.socketpair()
    a.setblocking(False)
    g = drs.Guest("unused", io.StringIO())
    g.sock = a
    try:
        g.run("x", 1)
        failures.append("silent guest did not time out")
    except TimeoutError:
        pass

    if failures:
        print("FAIL\n" + "\n".join(failures))
        return 1
    print("test-dr-serial: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
