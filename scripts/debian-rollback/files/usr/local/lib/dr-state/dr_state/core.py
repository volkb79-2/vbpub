"""Commands over injected storage, boot, subprocess, clock, and environment."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from . import FORMAT, apt, ids, metadata, retention
from .boot import Boot, BootTarget, Selector
from .boot.grub import GrubBoot
from .boot.sdboot import SdbootBoot
from .errors import (
    BootConfigError,
    BootError,
    CapacityError,
    DrStateError,
    FormatError,
    Refused,
    StorageError,
    Unsupported,
    UsageError,
)
from .lock import FileLock, atomic_write
from .paths import Paths
from .storage import KernelPair, Storage, Stored
from .storage.btrfs import BtrfsStorage

_PACKAGE_RE = re.compile(
    r"^(apt|base-files|dpkg|libc6|systemd|systemd-sysv|systemd-boot|"
    r"systemd-boot-efi|linux-image)"
)


@dataclass(frozen=True, slots=True)
class Completed:
    """A subprocess result, with text streams and no implicit failure."""

    returncode: int
    stdout: str
    stderr: str


class Runner:
    """The only production path to external programs."""

    def run(self, argv: list[str], *, input: str | None = None) -> Completed:
        try:
            completed = subprocess.run(
                argv,
                input=input,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as error:
            return Completed(127, "", str(error))
        return Completed(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(slots=True)
class Context:
    """Every ambient fact a core command consumes."""

    paths: Paths
    storage: Storage
    runner: Runner
    clock: Callable[[], datetime]
    env: Mapping[str, str]
    stdout: TextIO = sys.stdout
    stderr: TextIO = sys.stderr
    boot: Boot | None = None
    writer_lock: FileLock | None = None

    def out(self, text: str = "") -> None:
        print(text, file=self.stdout)

    def err(self, text: str) -> None:
        print(text, file=self.stderr)


def detect_storage(paths: Paths, runner: Runner) -> Storage:
    """Select the only P-20b backend from ``findmnt``."""

    completed = runner.run(
        ["findmnt", "-n", "-o", "FSTYPE", "--target", str(paths.sysroot)]
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise StorageError(
            "cannot determine the root filesystem type"
            + (f": {detail}" if detail else "")
        )
    backend = completed.stdout.strip()
    if backend == "btrfs":
        return BtrfsStorage(paths, runner)
    if backend == "zfs":
        raise Unsupported("ZFS storage is not installed in this runtime")
    raise StorageError(f"unsupported storage backend {backend!r}")


def marker_value(paths: Paths) -> str | None:
    """Read the ESP marker without inventing a default backend."""

    try:
        status = os.lstat(paths.esp_marker)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refused(f"cannot inspect bootloader marker {paths.esp_marker}: {error}") from error
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise Refused(f"bootloader marker {paths.esp_marker} is not a regular file")
    try:
        return paths.esp_marker.read_text(encoding="utf-8").replace("\r", "").replace("\n", "")
    except (OSError, UnicodeError) as error:
        raise Refused(f"cannot read bootloader marker {paths.esp_marker}: {error}") from error


def make_context(
    paths: Paths,
    *,
    runner: Runner | None = None,
    storage: Storage | None = None,
    boot: Boot | None = None,
    clock: Callable[[], datetime] | None = None,
    env: Mapping[str, str] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> Context:
    """Build the production or injected execution context."""

    process_runner = runner or Runner()
    backend = storage or detect_storage(paths, process_runner)
    return Context(
        paths=paths,
        storage=backend,
        runner=process_runner,
        clock=clock or (lambda: datetime.now(UTC)),
        env=os.environ if env is None else env,
        stdout=stdout,
        stderr=stderr,
        boot=boot,
    )


def _require_boot(context: Context) -> Boot:
    if context.boot is not None:
        return context.boot
    marker = marker_value(context.paths)
    if marker is None:
        raise Refused(
            f"bootloader marker {context.paths.esp_marker} is missing; "
            "run install-grub first"
        )
    if not marker:
        raise Refused(
            f"bootloader marker {context.paths.esp_marker} is present but empty"
        )
    if marker == "grub":
        return GrubBoot(context.paths, context.storage, context.runner)
    if marker == "systemd-boot":
        return SdbootBoot(
            context.paths,
            context.storage,
            context.runner,
            warn=lambda text: context.err(f"dr-state: {text}"),
        )
    raise Unsupported(f"boot backend {marker!r} is not installed in this runtime")


def _grub(context: Context) -> GrubBoot:
    return GrubBoot(context.paths, context.storage, context.runner)


def _ensure_top(context: Context) -> None:
    if not context.paths.top.is_dir():
        raise StorageError(f"btrfs top level {context.paths.top} is missing")


def _ensure_control(context: Context) -> None:
    for directory in (context.paths.control_dir, context.paths.metadata_dir):
        try:
            status = os.lstat(directory)
        except FileNotFoundError:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
            continue
        if stat.S_ISLNK(status.st_mode):
            raise FormatError(f"control directory {directory} is a symlink")
        if not stat.S_ISDIR(status.st_mode):
            raise FormatError(f"control directory {directory} is not a directory")


def _stored(context: Context) -> list[Stored]:
    _ensure_top(context)
    return sorted(context.storage.list(), key=lambda item: item.name)


def _managed(stored: Iterable[Stored]) -> dict[str, ids.StateId]:
    result: dict[str, ids.StateId] = {}
    for item in stored:
        parsed = ids.parse(item.name)
        if parsed is not None:
            result[item.name] = parsed
    return result


def _sidecar(context: Context, state: ids.StateId) -> metadata.Metadata | None:
    try:
        return metadata.load_sidecar(metadata.sidecar_path(context.paths, state))
    except (FormatError, OSError):
        return None


def _pinned(context: Context, states: Iterable[ids.StateId]) -> frozenset[str]:
    names: set[str] = set()
    for state in states:
        item = _sidecar(context, state)
        if item is not None and item.pinned:
            names.add(state.name)
    return frozenset(names)


def _running(context: Context) -> tuple[str | None, bool]:
    value = context.storage.running()
    if value is None:
        value = context.env.get("DR_STATE", "")
    if value == "none":
        return None, True
    return value or None, False


def _running_for_required_verb(context: Context, verb: str) -> str:
    if context.env.get("DR_STATE", "") == "none":
        raise Refused(
            f"{verb}: refusing with DR_STATE=none "
            "(the running state would not be protected)"
        )
    running, none_requested = _running(context)
    if none_requested:
        raise Refused(
            f"{verb}: refusing with DR_STATE=none "
            "(the running state would not be protected)"
        )
    if running is None:
        raise Refused(
            "cannot tell the running state; set DR_STATE=<state>, "
            "or DR_STATE=none to skip the snapshot"
        )
    return running


def _os_release(paths: Paths) -> tuple[str, str, str]:
    values: dict[str, str] = {}
    try:
        lines = paths.os_release.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return "", "", ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return (
        values.get("PRETTY_NAME", ""),
        values.get("ID", ""),
        values.get("VERSION_ID", ""),
    )


def _kernel_at_snapshot(context: Context) -> str:
    try:
        return context.paths.proc_osrelease.read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return ""


def _packages(context: Context) -> tuple[str, str]:
    try:
        completed = context.runner.run(
            [
                "dpkg-query",
                "-W",
                "-f=${binary:Package}\t${Version}\n",
            ]
        )
    except OSError:
        return "unavailable", ""
    if completed.returncode != 0:
        return "unavailable", ""
    rows = [
        line
        for line in completed.stdout.splitlines()
        if _PACKAGE_RE.match(line.partition("\t")[0])
    ]
    return "recorded", "\n".join(rows)


def _new_metadata(
    context: Context,
    state: ids.StateId,
    *,
    parent: str,
    source: str,
    reason: str,
    note: str,
    apt_actions: str,
) -> metadata.Metadata:
    pretty, os_id, version = _os_release(context.paths)
    packages_status, packages = _packages(context)
    kernel = _kernel_at_snapshot(context)
    return metadata.Metadata(
        state=state.name,
        sequence=state.sequence,
        origin_slug=state.slug,
        created_utc=state.created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        parent_state=parent,
        source=source,
        reason=reason,
        note=note,
        os_pretty_name=pretty,
        os_id=os_id,
        os_version_id=version,
        kernel=kernel,
        packages_status=packages_status,
        packages=packages,
        apt_actions=apt_actions,
        kind="checkpoint",
        pinned=False,
    )


def _update_apt_actions(
    context: Context,
    state: ids.StateId,
    actions: str,
) -> None:
    item = _sidecar(context, state)
    if item is None:
        return
    lines = [
        line
        for line in (item.apt_actions + "\n" + actions).splitlines()
        if line
    ]
    combined = "\n".join(dict.fromkeys(lines))
    metadata.write_sidecar(
        context.paths,
        state,
        replace(item, apt_actions=combined),
    )


def _label_boot(context: Context) -> Boot | None:
    if context.boot is not None:
        return context.boot
    try:
        marker = marker_value(context.paths)
    except DrStateError:
        return None
    if marker == "grub":
        return GrubBoot(context.paths, context.storage, context.runner)
    if marker == "systemd-boot":
        return SdbootBoot(
            context.paths,
            context.storage,
            context.runner,
            warn=lambda text: context.err(f"dr-state: {text}"),
        )
    return None


def _default_state(context: Context, *, strict: bool) -> Selector | None:
    boot = _label_boot(context)
    if boot is None:
        return None
    try:
        return boot.default(strict=strict)
    except DrStateError:
        if strict:
            raise
        return None


def _oneshot_state(context: Context, *, strict: bool) -> Selector | None:
    boot = _label_boot(context)
    if boot is None:
        return None
    return boot.oneshot(strict=strict)


def _targets(
    context: Context,
    states: Iterable[ids.StateId],
) -> list[BootTarget]:
    result: list[BootTarget] = []
    for state in sorted(states, key=lambda item: item.name):
        kernels = context.storage.kernels(state.name)
        if not kernels:
            continue
        kernel = kernels[0]
        result.append(
            BootTarget(
                state=state,
                title=f"Debian {state.name} (kernel {kernel.version})",
                kernel=kernel,
                root_args=context.storage.root_args(state),
                locator=context.storage.locator(state),
            )
        )
    return result


def _publish(
    context: Context,
    *,
    exclude: Iterable[str] = (),
    requested: ids.StateId | None = None,
    clear_default_variable: bool = False,
) -> None:
    boot = _require_boot(context)
    excluded = frozenset(exclude)
    managed = _managed(_stored(context))
    targets = _targets(
        context,
        (
            state
            for state in managed.values()
            if state.name not in excluded
        ),
    )
    if not targets:
        raise StorageError(
            "no state has a kernel with an initrd; keeping the existing menu"
        )
    names = {target.state.name for target in targets}
    if requested is not None and requested.name not in names:
        raise StorageError(
            f"default state {requested.name} has no kernel with a non-empty initrd; "
            "keeping the existing menu"
        )

    running, _ = _running(context)
    try:
        configured = boot.default(strict=False)
    except DrStateError:
        configured = None
    configured_name = configured.state if configured is not None else None
    if requested is not None:
        chosen_name = requested.name
    elif configured_name in names:
        chosen_name = configured_name
    elif running in names:
        chosen_name = running
    else:
        chosen_name = targets[0].state.name
    chosen = managed[chosen_name]
    capacity = boot.capacity(tuple(targets))
    if capacity is not None:
        payload: dict[str, int] = {}
        for target in targets:
            for key, size in boot.payload(target):
                payload[key] = size
        needed = sum(payload.values())
        if needed > capacity:
            raise CapacityError(
                f"ESP cannot fit the selected systemd-boot menu: need {needed} "
                f"bytes, capacity {capacity} bytes after protected entries"
            )
    boot.publish(
        tuple(targets),
        chosen,
        clear_default_variable=clear_default_variable,
    )


def _list_line(
    context: Context,
    item: Stored,
    *,
    running: str | None,
    default: str | None,
) -> str:
    managed = ids.parse(item.name)
    kernel = context.storage.kernels(item.name)
    kernel_name = kernel[0].version if kernel else "unknown"
    labels: list[str] = []
    if managed is None:
        labels.append("unmanaged")
    if item.name == running:
        labels.append("running")
    if item.name == default:
        labels.append("default")
    prefix = item.name + (f" ({', '.join(labels)})" if labels else "")
    if managed is None:
        return f"{prefix} kernel={kernel_name}"

    sidecar = _sidecar(context, managed)
    if sidecar is None:
        return f"{prefix} kernel={kernel_name}"
    created = sidecar.created_utc or "unknown"
    reason = _one_line(sidecar.reason or "unknown", 48)
    pretty = _one_line(sidecar.os_pretty_name or "unknown", 64)
    text = (
        f"{prefix} kernel={kernel_name} created={created} "
        f"reason={reason} os={pretty}"
    )
    if sidecar.note:
        text += f" note={_one_line(sidecar.note, 96)}"
    if sidecar.apt_actions:
        text += f" changes={_one_line(sidecar.apt_actions.splitlines()[0], 72)}"
    return text


def _one_line(value: str, limit: int) -> str:
    return value.replace("\r", " ").replace("\n", " ").replace("\t", " ")[:limit]


def cmd_list(context: Context, command: Sequence[str]) -> int:
    long = False
    for argument in command:
        if argument in ("--long", "--verbose"):
            long = True
        else:
            raise UsageError(f"list: unknown option {argument}")
    stored = _stored(context)
    running, _ = _running(context)
    default_selector = _default_state(context, strict=False)
    default = default_selector.state if default_selector is not None else None
    for item in stored:
        if long:
            _show(context, item.name)
            context.out()
        else:
            context.out(_list_line(context, item, running=running, default=default))
    return 0


def _status_text(
    item: Stored,
    *,
    running: str | None,
    default: str | None,
) -> str:
    labels: list[str] = []
    if ids.parse(item.name) is None:
        labels.append("unmanaged")
    if item.name == running:
        labels.append("running")
    if item.name == default:
        labels.append("default")
    return ", ".join(labels)


def _show(context: Context, name: str) -> None:
    stored = {item.name: item for item in _stored(context)}
    item = stored.get(name)
    if item is None:
        raise UsageError(f"no such state: {name}")
    managed = ids.parse(name)
    running, _ = _running(context)
    configured = _default_state(context, strict=False)
    default = configured.state if configured is not None else None
    status = _status_text(item, running=running, default=default)
    context.out(f"state: {name}")
    if status:
        context.out(f"status: {status}")
    sidecar = _sidecar(context, managed) if managed is not None else None
    if sidecar is not None:
        context.out(f"sequence: {sidecar.sequence:06d}")
        context.out(f"origin-slug: {sidecar.origin_slug or 'unknown'}")
    else:
        context.out("sequence: unknown")
        context.out("origin-slug: unknown")
    kernels = context.storage.kernels(name)
    if kernels:
        context.out(f"boot-kernel-available: {kernels[0].version}")
    else:
        context.out("boot-kernel-available: no")
    if sidecar is None:
        created = (
            managed.created.strftime("%Y-%m-%dT%H:%M:%SZ")
            if managed is not None
            else "unrecorded"
        )
        context.out(f"created-utc: {created}")
        context.out("recorded-metadata: unavailable (state predates metadata capture)")
        return
    context.out(f"created-utc: {sidecar.created_utc or 'unknown'}")
    context.out(f"source: {sidecar.source or 'unknown'}")
    context.out(f"reason: {sidecar.reason or 'unknown'}")
    context.out(f"copied-from: {sidecar.parent_state or 'unknown'}")
    context.out(f"note: {sidecar.note or 'none'}")
    context.out(f"os-at-snapshot: {sidecar.os_pretty_name or 'unknown'}")
    context.out(f"os-id: {sidecar.os_id or 'unknown'}")
    context.out(f"os-version-id: {sidecar.os_version_id or 'unknown'}")
    context.out(f"running-kernel-at-snapshot: {sidecar.kernel or 'unknown'}")
    context.out(
        f"package-version-capture: {sidecar.packages_status or 'unavailable'}"
    )
    if sidecar.packages:
        context.out(f"key-package-versions:\n{sidecar.packages}")
    else:
        context.out("key-package-versions: unavailable")
    if sidecar.apt_actions:
        context.out(f"apt-package-actions:\n{sidecar.apt_actions}")
    else:
        context.out("apt-package-actions: not recorded")


def cmd_show(context: Context, command: Sequence[str]) -> int:
    if len(command) != 1:
        raise UsageError("usage: dr-state show STATE")
    _show(context, command[0])
    return 0


def _snapshot(
    context: Context,
    *,
    reason: str,
    note: str,
    apt_actions: str,
) -> int:
    if context.env.get("DR_STATE", "") == "none":
        context.err("dr-state: DR_STATE=none, snapshot skipped")
        return 0
    running, none_requested = _running(context)
    if none_requested:
        context.err("dr-state: DR_STATE=none, snapshot skipped")
        return 0
    if running is None:
        raise Refused(
            "cannot tell the running state; set DR_STATE=<state>, "
            "or DR_STATE=none to skip the snapshot"
        )
    managed = _managed(_stored(context))
    source = ids.parse(running)
    if source is None:
        raise Refused(f"running state {running} is unmanaged")
    if source.name not in managed:
        raise Refused(f"cannot verify the running state {running}")
    source_name = ids.slug("apt" if reason == "apt" else "manual", reason)
    if source_name is None:
        raise StorageError(
            f"reason {reason!r} cannot produce a state-name slug"
        )
    source_kind = "apt" if reason == "apt" else "manual"
    interval_text = context.env.get("DR_SNAPSHOT_MIN_INTERVAL", "900")
    if not interval_text.isdecimal():
        raise UsageError("DR_SNAPSHOT_MIN_INTERVAL must be a number of seconds")
    interval = int(interval_text)
    now = context.clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    if source_kind == "apt" and interval > 0:
        newest: ids.StateId | None = None
        for candidate in managed.values():
            item = _sidecar(context, candidate)
            if item is None or item.source != "apt":
                continue
            try:
                parent = context.storage.parent(candidate)
            except StorageError:
                continue
            if parent is not None and parent.name == source.name:
                if newest is None or candidate.sequence > newest.sequence:
                    newest = candidate
        if newest is not None and apt.window_blocks(newest, interval, now):
            _update_apt_actions(context, newest, apt_actions)
            context.err(
                f"dr-state: {newest.name} (a snapshot of {source.name}) is inside "
                f"the window of {interval} s, no new snapshot"
            )
            return 0

    _ensure_control(context)
    metadata.write_control_format(context.paths)
    writer_lock = context.writer_lock
    if writer_lock is None:
        raise RuntimeError("snapshot requires the writer lock")
    allocation = metadata.allocate_sequence(
        context.paths,
        managed.values(),
        lock=writer_lock,
    )
    if allocation.warning:
        context.err(f"dr-state: {allocation.warning}")
    target = ids.make(
        allocation.sequence,
        now.replace(microsecond=0),
        source_name,
    )
    if target is None:
        raise StorageError(
            f"cannot construct snapshot name at sequence {allocation.sequence}"
        )
    if target.name in managed:
        raise Refused(
            f"state sequence {allocation.sequence} collides with {target.name}"
        )
    context.storage.snapshot(source, target)
    try:
        _ensure_control(context)
        new_sidecar = _new_metadata(
            context,
            target,
            parent=source.name,
            source=source_kind,
            reason=reason,
            note=note,
            apt_actions=apt_actions,
        )
        metadata.write_sidecar(context.paths, target, new_sidecar)
    except BaseException as error:
        try:
            context.storage.delete(target)
        except BaseException as cleanup_error:
            raise StorageError(
                f"cannot persist pre-change metadata for {target.name}; "
                f"the snapshot could not be removed: {cleanup_error}"
            ) from error
        raise StorageError(
            f"cannot persist pre-change metadata for {target.name}"
        ) from error

    try:
        _publish(context)
    except (
        StorageError,
        BootError,
        CapacityError,
        BootConfigError,
        Unsupported,
        Refused,
        FormatError,
        OSError,
    ) as error:
        context.err(
            f"dr-state: warning: checkpoint {target.name} was written but "
            f"the boot menu was not updated: {error}"
        )
    context.out(f"dr-state: wrote {target.name} (copy of {source.name})")
    return 0


def cmd_snapshot(context: Context, command: Sequence[str]) -> int:
    reason = "manual"
    note = ""
    apt_actions = ""
    index = 0
    while index < len(command):
        option = command[index]
        if option not in ("--reason", "--note", "--apt-actions"):
            raise UsageError(f"snapshot: {option}")
        if index + 1 >= len(command):
            raise UsageError(f"snapshot: {option} needs a value")
        value = command[index + 1]
        if option == "--reason":
            reason = value
        elif option == "--note":
            note = value
        else:
            apt_actions = value
        index += 2
    return _snapshot(context, reason=reason, note=note, apt_actions=apt_actions)


def _managed_target(name: str, stored: Iterable[Stored]) -> ids.StateId:
    if not any(item.name == name for item in stored):
        raise UsageError(f"no such state: {name}")
    parsed = ids.parse(name)
    if parsed is None:
        raise Refused(f"state {name} is unmanaged")
    return parsed


def cmd_gen_menu(context: Context, command: Sequence[str]) -> int:
    requested: ids.StateId | None = None
    if command:
        if len(command) != 2 or command[0] != "--default":
            raise UsageError(
                "gen-menu: expected no arguments or --default STATE"
            )
        requested = _managed_target(command[1], _stored(context))
    _publish(context, requested=requested)
    return 0


def cmd_set_default(context: Context, command: Sequence[str]) -> int:
    if len(command) != 1:
        raise UsageError("usage: dr-state set-default STATE")
    target = _managed_target(command[0], _stored(context))
    if not context.storage.kernels(target.name):
        raise StorageError(f"state {target.name} has no bootable kernel")
    context.storage.activate(target)
    _publish(
        context,
        requested=target,
        clear_default_variable=True,
    )
    return 0


def cmd_boot_once(context: Context, command: Sequence[str]) -> int:
    if len(command) != 1:
        raise UsageError("usage: dr-state boot-once STATE")
    target = _managed_target(command[0], _stored(context))
    boot = _require_boot(context)
    if not boot.supports_oneshot:
        raise Unsupported(f"{boot.name} does not support a one-shot boot")
    if boot.name == "systemd-boot":
        # The selector names an existing BLS entry, so parity requires publication
        # before bootctl observes the target.
        _publish(context)
    boot.set_oneshot(target)
    return 0


def _refuse_targeted_selector(
    context: Context,
    target: ids.StateId,
    *,
    kind: str,
    selector: Selector | None,
) -> None:
    if selector is None or selector.state is None:
        return
    if selector.state == target.name:
        raise Refused(
            f"refusing to delete {kind} state {target.name}; "
            "boot and select another default first"
        )


def cmd_delete(context: Context, command: Sequence[str]) -> int:
    target_name: str | None = None
    dry = False
    confirmed = False
    for argument in command:
        if argument == "--dry-run":
            dry = True
        elif argument == "--yes":
            confirmed = True
        elif argument.startswith("--"):
            raise UsageError(f"delete: unknown option {argument}")
        elif target_name is None:
            target_name = argument
        else:
            raise UsageError("delete: provide exactly one state name")
    if target_name is None:
        raise UsageError("usage: dr-state delete STATE [--dry-run|--yes]")
    if not dry and not confirmed:
        raise Refused("delete requires --yes (use --dry-run to inspect the target first)")
    running = _running_for_required_verb(context, "delete")
    stored = _stored(context)
    target = _managed_target(target_name, stored)
    if target.name == running:
        raise Refused(f"refusing to delete the running state {target.name}")
    boot = _require_boot(context)
    default = boot.default(strict=True)
    if default is None:
        raise Refused(
            f"{boot.name} has no readable configured default; "
            "refusing delete without a known configured default"
        )
    _refuse_targeted_selector(
        context,
        target,
        kind=f"configured {boot.name} default",
        selector=default,
    )
    oneshot = boot.oneshot(strict=True)
    if oneshot is not None and oneshot.state == target.name:
        raise Refused(
            f"refusing to delete the pending {boot.name} one-shot state {target.name}"
        )
    if target.name in boot.protected():
        raise Refused(
            f"refusing to delete {boot.name} selector state {target.name}; "
            "boot and select another default first"
        )
    if dry:
        context.out(f"would delete {target.name}")
        return 0

    wanted_default = (
        ids.parse(default.state)
        if default is not None and default.state is not None
        else None
    )
    try:
        _publish(context, exclude=(target.name,), requested=wanted_default)
        context.storage.delete(target)
    except BaseException:
        try:
            _publish(context, requested=wanted_default)
        except DrStateError as republish_error:
            context.err(
                f"dr-state: menu regeneration also failed after delete error: "
                f"{republish_error}"
            )
        raise
    try:
        metadata.sidecar_path(context.paths, target).unlink(missing_ok=True)
    except OSError as error:
        raise StorageError(
            f"deleted {target.name} but could not remove its snapshot metadata: {error}"
        ) from error
    context.out(f"deleted {target.name}")
    return 0


def cmd_prune(context: Context, command: Sequence[str]) -> int:
    keep = 10
    dry = False
    index = 0
    while index < len(command):
        option = command[index]
        if option == "--dry-run":
            dry = True
            index += 1
        elif option == "--keep":
            if index + 1 >= len(command):
                raise UsageError("prune: --keep needs a value")
            value = command[index + 1]
            if not value.isdecimal():
                raise UsageError("prune: --keep must be a number")
            keep = int(value)
            index += 2
        else:
            raise UsageError(f"prune: {option}")
    running = _running_for_required_verb(context, "prune")
    boot = _require_boot(context)
    default = boot.default(strict=True)
    oneshot = boot.oneshot(strict=True)
    if default is None and boot.name == "systemd-boot":
        raise Refused(
            f"{boot.name} has no readable configured default; refusing prune"
        )
    managed = _managed(_stored(context))
    states = tuple(managed.values())
    protected = {running}
    protected.update(boot.protected())
    if default is not None and default.state is not None:
        protected.add(default.state)
    if oneshot is not None and oneshot.state is not None:
        protected.add(oneshot.state)
    doomed = retention.select_prune(
        states,
        keep,
        protected=protected,
        pinned=_pinned(context, states),
    )
    if dry:
        for state in doomed:
            context.out(f"would delete {state.name}")
        return 0

    wanted_default = (
        ids.parse(default.state)
        if default is not None and default.state is not None
        else None
    )
    doomed_names = frozenset(state.name for state in doomed)
    if doomed:
        _publish(context, exclude=doomed_names, requested=wanted_default)
    failed = False
    for state in doomed:
        try:
            context.storage.delete(state)
        except DrStateError as error:
            context.err(f"dr-state: could not delete {state.name}: {error}")
            failed = True
            continue
        try:
            metadata.sidecar_path(context.paths, state).unlink(missing_ok=True)
        except OSError as error:
            context.err(
                f"dr-state: deleted {state.name} but could not remove its metadata: {error}"
            )
            failed = True
        context.out(f"deleted {state.name}")
    _publish(context, requested=wanted_default)
    return 1 if failed else 0


def cmd_initialize(context: Context, command: Sequence[str]) -> int:
    if len(command) != 2:
        raise UsageError("usage: dr-state initialize-state STATE TIMESTAMP")
    name, stamp = command
    try:
        created = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError as error:
        raise UsageError(
            "initial state must use a valid UTC timestamp"
        ) from error
    expected = ids.make(1, created, "install")
    if expected is None or name != expected.name:
        raise UsageError(f"initial state name must be {expected.name if expected else '<invalid>'}")
    stored = _stored(context)
    if tuple(item.name for item in stored) != (name,):
        raise Refused(
            f"initialize-state requires a fresh filesystem containing only {name}"
        )
    if not (context.paths.top / name).is_dir():
        raise Refused(f"initial state {name} is not a real top-level directory")
    kernels = context.storage.kernels(name)
    if not kernels:
        raise Refused("initial state has no complete kernel/initrd pair")
    _ensure_control(context)
    counter = metadata.read_counter(context.paths.counter_file)
    if counter.value is not None or counter.issue != f"counter {context.paths.counter_file} is missing":
        if counter.value != 1:
            raise FormatError(
                f"sequence counter exists but is invalid: {counter.issue or counter.value}"
            )
        sidecar = _sidecar(context, expected)
        if sidecar is None or sidecar.state != name or sidecar.sequence != 1:
            raise Refused(
                "sequence counter is initialized but the initial state "
                "metadata is missing or inconsistent"
            )
        context.out(f"dr-state: {name} is already initialized with sequence 000001")
        return 0
    metadata.write_control_format(context.paths)
    sidecar = _new_metadata(
        context,
        expected,
        parent="none",
        source="install",
        reason="install",
        note="",
        apt_actions="",
    )
    sidecar = replace(
        sidecar,
        kernel=_kernel_at_snapshot(context) or kernels[0].version,
        kind="live",
        pinned=True,
    )
    metadata.write_sidecar(context.paths, expected, sidecar)
    try:
        atomic_write(context.paths.counter_file, "000001\n")
    except BaseException as error:
        metadata.sidecar_path(context.paths, expected).unlink(missing_ok=True)
        raise StorageError("cannot initialize the durable state sequence counter") from error
    context.out(f"dr-state: initialized {name} with sequence 000001")
    return 0


def _read_fd(fd: int) -> str:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as error:
        raise UsageError(f"APT package-plan descriptor is not UTF-8: {error}") from error


def cmd_apt_hook(context: Context, command: Sequence[str]) -> int:
    if command:
        raise UsageError("apt-pre-install-pkgs takes no arguments")
    descriptor = context.env.get("APT_HOOK_INFO_FD", "")
    if not descriptor.isdecimal() or int(descriptor) < 3:
        raise Refused(
            "APT_HOOK_INFO_FD is missing or invalid; refusing to guess the APT package plan"
        )
    text = _read_fd(int(descriptor))
    plan = apt.parse_plan(text)
    if plan is None:
        actions = "package plan unavailable (APT hook protocol was not recognized)"
        context.err(
            "dr-state: APT package plan could not be parsed; recording an apt "
            "snapshot without inferred actions"
        )
    else:
        actions = plan.summary()
    return _snapshot(context, reason="apt", note="", apt_actions=actions)


def cmd_install_grub(context: Context, command: Sequence[str]) -> int:
    if command:
        raise UsageError("install-grub takes no arguments")
    if not context.paths.esp.is_dir():
        raise Refused(f"ESP {context.paths.esp} is missing or not mounted")
    context.paths.esp_dir.mkdir(parents=True, exist_ok=True)
    (context.paths.esp / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)
    completed = context.runner.run(
        [
            "grub-mkimage",
            "-O",
            "x86_64-efi",
            "-o",
            str(context.paths.esp / "EFI" / "BOOT" / "BOOTX64.EFI"),
            "-p",
            "(,gpt1)/EFI/dr",
            "part_gpt",
            "fat",
            "btrfs",
            "search",
            "search_fs_uuid",
            "normal",
            "linux",
            "configfile",
            "echo",
            "test",
            "ls",
            "loadenv",
            "serial",
            "terminal",
            "efi_gop",
            "gzio",
        ]
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise BootError(
            "grub-mkimage failed" + (f": {detail}" if detail else "")
        )
    if not context.paths.esp_grubenv.exists():
        completed = context.runner.run(
            ["grub-editenv", str(context.paths.esp_grubenv), "create"]
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise BootError(
                "cannot create GRUB environment"
                + (f": {detail}" if detail else "")
            )
    _grub(context).install()
    return 0


def cmd_install_systemd_boot(context: Context, command: Sequence[str]) -> int:
    if command:
        raise UsageError("install-systemd-boot takes no arguments")
    SdbootBoot(
        context.paths,
        context.storage,
        context.runner,
        warn=lambda text: context.err(f"dr-state: {text}"),
    ).install()
    return 0


Dispatch = Callable[[Context, Sequence[str]], int]


COMMANDS: dict[str, Dispatch] = {
    "install-grub": cmd_install_grub,
    "install-systemd-boot": cmd_install_systemd_boot,
    "initialize-state": cmd_initialize,
    "gen-menu": cmd_gen_menu,
    "snapshot": cmd_snapshot,
    "apt-pre-install-pkgs": cmd_apt_hook,
    "list": cmd_list,
    "show": cmd_show,
    "set-default": cmd_set_default,
    "boot-once": cmd_boot_once,
    "prune": cmd_prune,
    "delete": cmd_delete,
}

WRITERS = frozenset(
    {
        "install-grub",
        "install-systemd-boot",
        "initialize-state",
        "gen-menu",
        "snapshot",
        "apt-pre-install-pkgs",
        "set-default",
        "boot-once",
        "prune",
        "delete",
    }
)


def execute(
    context: Context,
    command: Sequence[str],
) -> int:
    """Dispatch one parsed command, taking the writer lock where required."""

    if not command:
        raise UsageError("a command is required")
    name = command[0]
    handler = COMMANDS.get(name)
    if handler is None:
        raise UsageError(
            "usage: dr-state install-grub|install-systemd-boot|"
            "initialize-state STATE TIMESTAMP|"
            "gen-menu [--default STATE]|snapshot [--reason WORD] [--note TEXT]|"
            "apt-pre-install-pkgs|list [--long]|show STATE|set-default STATE|"
            "boot-once STATE|delete STATE [--dry-run|--yes]|"
            "prune [--keep N] [--dry-run]"
        )
    if name not in WRITERS:
        return handler(context, tuple(command[1:]))
    context.paths.lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(context.paths.lock_file, timeout=60)
    with lock:
        context.writer_lock = lock
        try:
            return handler(context, tuple(command[1:]))
        finally:
            context.writer_lock = None
