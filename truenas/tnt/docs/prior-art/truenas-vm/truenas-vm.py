#!/usr/bin/env python3
"""Manage TrueNAS 27+ virtual machines through the supported WebSocket API."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Iterator
from urllib.parse import urlsplit

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)


IDENTITY = CliIdentity.resolve(
    name="TRUENAS VM",
    command="truenas-vm",
    long_name="TrueNAS virtual-machine lifecycle client",
    version_file=Path(__file__).resolve().with_name("VERSION"),
)

_TRUENAS_API_CLIENT_COMMIT = "2a04491953d38c13f13f40729d2a4966a77c6297"
_IMAGE_SUFFIXES = (".qcow2", ".qed", ".raw", ".vdi", ".vhdx", ".vmdk")
_ZFS_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")


def _nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _option(
    flags: tuple[str, ...], description: str, *, group: str, **kwargs: Any
) -> OptionSpec:
    return OptionSpec(flags, description, group=group, parser_kwargs=kwargs)


def _argument(name: str, description: str, *, metavar: str | None = None, **kwargs: Any) -> ArgumentSpec:
    return ArgumentSpec(name, description, metavar=metavar, parser_kwargs=kwargs)


def _read_json_object(path_value: str) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliFailure(f"cannot read JSON file {path}: {exc}", exit_code=2) from exc

    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=no_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CliFailure(f"invalid JSON in {path}: {exc}", exit_code=2) from exc
    if not isinstance(result, dict):
        raise CliFailure(f"{path} must contain a JSON object", exit_code=2)
    return result


def _api_connection_values(args: argparse.Namespace) -> tuple[str, str, Path]:
    url = args.url or os.environ.get("TRUENAS_URL")
    username = args.username or os.environ.get("TRUENAS_USERNAME")
    key_file_value = args.api_key_file or os.environ.get("TRUENAS_API_KEY_FILE")
    if not url:
        raise CliFailure("missing TrueNAS URL", exit_code=2, hint="set TRUENAS_URL or pass --url")
    if not username:
        raise CliFailure("missing TrueNAS API-key username", exit_code=2, hint="set TRUENAS_USERNAME or pass --username")
    if not key_file_value:
        raise CliFailure("missing TrueNAS API-key file", exit_code=2, hint="set TRUENAS_API_KEY_FILE or pass --api-key-file")

    parts = urlsplit(url)
    if (
        parts.scheme != "wss"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path != "/api/v27.0"
        or parts.query
        or parts.fragment
    ):
        raise CliFailure(
            "TrueNAS URL must be a TLS WebSocket URL ending in /api/v27.0, "
            "without embedded credentials, query, or fragment",
            exit_code=2,
        )

    key_file = Path(key_file_value).expanduser()
    try:
        file_stat = key_file.stat()
    except OSError as exc:
        raise CliFailure(f"cannot read API-key file {key_file}: {exc}", exit_code=2) from exc
    if not key_file.is_file():
        raise CliFailure(f"API-key path is not a regular file: {key_file}", exit_code=2)
    if file_stat.st_mode & 0o077:
        raise CliFailure(
            f"API-key file {key_file} is accessible by group or other users; run chmod 600",
            exit_code=2,
        )
    return url, username, key_file.resolve()


def _version_pair(version: Any) -> tuple[int, int]:
    if not isinstance(version, str):
        raise CliFailure(
            "TrueNAS system.version_short did not provide a version string; refusing to guess API compatibility",
            exit_code=2,
        )
    match = re.fullmatch(
        r"([0-9]+)\.([0-9]+)(?:\.[0-9]+)*(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?",
        version,
    )
    if not match:
        raise CliFailure(
            f"cannot parse TrueNAS version {version!r}; refusing to guess API compatibility",
            exit_code=2,
        )
    result = (int(match.group(1)), int(match.group(2)))
    if result < (27, 0):
        raise CliFailure(
            f"TrueNAS {version} is not supported; this client requires TrueNAS 27.0 or newer",
            exit_code=2,
        )
    return result


@contextmanager
def _session(args: argparse.Namespace) -> Iterator[tuple[Any, dict[str, Any]]]:
    url, username, key_file = _api_connection_values(args)
    try:
        import truenas_api_client as tn_client
    except ImportError as exc:
        raise CliFailure(
            "TrueNAS API client dependency is missing",
            exit_code=2,
            hint="from truenas/tnt/docs/prior-art/truenas-vm run: python3 -m pip install -r requirements-runtime.txt",
        ) from exc

    client = None
    try:
        client = tn_client.Client(uri=url, verify_ssl=True)
        client.login_with_api_key(username, str(key_file))
        system_info = client.call("system.info")
        version_short = client.call("system.version_short")
        _version_pair(version_short)
        if not isinstance(system_info, dict):
            raise CliFailure("TrueNAS system.info returned an unexpected response", exit_code=2)
        yield client, system_info
    except CliFailure:
        raise
    except (tn_client.ClientException, tn_client.WebSocketException, OSError, ValueError) as exc:
        raise CliFailure(f"TrueNAS WebSocket/API request failed: {exc}") from exc
    finally:
        if client is not None:
            client.close()


def _call(client: Any, method: str, *params: Any, job: bool = False) -> Any:
    try:
        return client.call(method, *params, job=job)
    except CliFailure:
        raise
    except Exception as exc:
        # The upstream client represents method, authentication, and job
        # failures through its exception hierarchy; avoid printing secrets or
        # raw websocket frames by surfacing only its concise exception text.
        raise CliFailure(f"TrueNAS API method {method} failed: {exc}") from exc


def _emit(runtime: Any, value: Any) -> int:
    if runtime.json_mode:
        runtime.output.primary_json(value)
    else:
        print(json.dumps(value, indent=2, sort_keys=True))
    return 0


def _confirm(runtime: Any, operation: str) -> bool:
    return runtime.confirm(operation)


def _require_host_virtualization(client: Any) -> None:
    supported = _call(client, "vm.supports_virtualization")
    if supported is not True:
        raise CliFailure(
            "TrueNAS reports that hardware virtualization is unavailable; "
            "check VT-x/AMD-V exposure before starting a VM"
        )


def _validated_image_path(path_value: str) -> str:
    path = PurePosixPath(path_value)
    if (
        not path.is_absolute()
        or not str(path).startswith("/mnt/")
        or ".." in path.parts
        or path.suffix not in _IMAGE_SUFFIXES
    ):
        raise CliFailure(
            "disk image must be an absolute TrueNAS dataset path under /mnt/ "
            "with a supported lowercase image suffix (.qcow2, .qed, .raw, .vdi, .vhdx, .vmdk)",
            exit_code=2,
        )
    return str(path)


def _require_vm_stopped_for_disk_import(client: Any, vm_id: int) -> None:
    vm = _call(client, "vm.get_instance", vm_id)
    if not isinstance(vm, dict):
        raise CliFailure(f"TrueNAS did not return VM {vm_id} as an object; refusing disk import")
    status = vm.get("status")
    state = status.get("state") if isinstance(status, dict) else None
    problems = []
    if state != "STOPPED":
        problems.append(f"status.state is {state!r}, not 'STOPPED'")
    autostart = vm.get("autostart")
    if autostart is not False:
        if autostart is True:
            problems.append("autostart is enabled")
        else:
            problems.append(f"autostart has unrecognized value {autostart!r}")
    if problems:
        raise CliFailure(
            f"VM {vm_id} is not ready for disk import: {'; '.join(problems)}. Stop it and disable autostart, "
            "then retry. Keep it stopped until the conversion job is confirmed complete."
        )


def _require_zvol_name_unused(client: Any, zvol_name: str) -> None:
    datasets = _call(
        client,
        "pool.dataset.query",
        [["id", "=", zvol_name]],
        {"select": ["id"]},
    )
    if not isinstance(datasets, list):
        raise CliFailure(
            "TrueNAS returned an unexpected result while checking the ZFS volume name; refusing disk import"
        )
    if not datasets:
        return
    if any(not isinstance(dataset, dict) or dataset.get("id") != zvol_name for dataset in datasets):
        raise CliFailure(
            "TrueNAS returned an unexpected dataset for the exact ZFS volume-name query; refusing disk import"
        )
    raise CliFailure(
        f"ZFS dataset name {zvol_name!r} already exists; choose a new unused --zvol-name. No changes were made."
    )


def _validated_zvol_name(value: str) -> str:
    components = value.split("/")
    if (
        value.startswith("/")
        or len(components) < 2
        or any(component in ("", ".", "..") or not _ZFS_COMPONENT.fullmatch(component) for component in components)
    ):
        raise CliFailure(
            "ZFS volume name must be a relative pool/dataset/volume path using letters, digits, dot, underscore, or hyphen",
            exit_code=2,
        )
    return value


def _impl_system_info(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    virtualization = _call(client, "vm.supports_virtualization")
    return _emit(
        runtime,
        {
            "system": info,
            "vm_supports_virtualization": virtualization,
            "api_client_source_commit": _TRUENAS_API_CLIENT_COMMIT,
        },
    )


def _impl_vm_list(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    return _emit(runtime, _call(client, "vm.query"))


def _impl_vm_show(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    return _emit(runtime, _call(client, "vm.get_instance", args.vm_id))


def _impl_vm_create(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    if not _confirm(runtime, "Create the TrueNAS virtual machine from the reviewed JSON file?"):
        return 0
    return _emit(runtime, _call(client, "vm.create", args.payload))


def _impl_vm_update(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    if not _confirm(runtime, f"Update TrueNAS VM {args.vm_id} from the reviewed JSON file?"):
        return 0
    return _emit(runtime, _call(client, "vm.update", args.vm_id, args.payload))


def _impl_vm_delete(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    options = {"force": args.force, "zvols": args.delete_zvols}
    if not _confirm(
        runtime,
        f"Delete TrueNAS VM {args.vm_id}; delete associated ZFS volumes={args.delete_zvols}, force={args.force}?",
    ):
        return 0
    return _emit(runtime, _call(client, "vm.delete", args.vm_id, options))


def _impl_vm_lifecycle(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    if args.verb == "vm-start":
        method = "vm.start"
        options = {"overcommit": args.overcommit}
        is_job = False
    elif args.verb == "vm-stop":
        method = "vm.stop"
        options = {"force": args.force, "force_after_timeout": args.force_after_timeout}
        is_job = True
    else:
        methods = {
            "vm-poweroff": ("vm.poweroff", False),
            "vm-restart": ("vm.restart", True),
            "vm-suspend": ("vm.suspend", False),
            "vm-resume": ("vm.resume", False),
        }
        method, is_job = methods[args.verb]
        options = None
    if method == "vm.start":
        _require_host_virtualization(client)
    if not _confirm(runtime, f"Run {method} for TrueNAS VM {args.vm_id}?"):
        return 0
    params = (args.vm_id,) if options is None else (args.vm_id, options)
    return _emit(runtime, _call(client, method, *params, job=is_job))


def _impl_device_list(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    filters = [["vm", "=", args.vm_id]] if args.vm_id is not None else []
    result = _call(client, "vm.device.query", filters) if filters else _call(client, "vm.device.query")
    return _emit(runtime, result)


def _impl_device_create(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    if args.payload.get("vm") != args.vm_id:
        raise CliFailure(f"device JSON vm must equal --vm-id {args.vm_id}", exit_code=2)
    if not _confirm(runtime, f"Create a device on TrueNAS VM {args.vm_id} using the reviewed JSON file?"):
        return 0
    return _emit(runtime, _call(client, "vm.device.create", args.payload))


def _impl_device_update(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    current = _call(client, "vm.device.get_instance", args.device_id)
    if not isinstance(current, dict) or isinstance(current.get("vm"), bool) or not isinstance(current.get("vm"), int):
        raise CliFailure(
            f"TrueNAS did not report the owning VM for device {args.device_id}; refusing to guess its update target"
        )
    current_vm = current["vm"]
    target_vm = args.payload.get("vm", current_vm)
    if isinstance(target_vm, bool) or not isinstance(target_vm, int) or target_vm <= 0:
        raise CliFailure("device update JSON field 'vm' must be a positive VM id", exit_code=2)
    if target_vm == current_vm:
        operation = f"Update TrueNAS VM device {args.device_id} on VM {current_vm} from the reviewed JSON file?"
    else:
        operation = (
            f"Update TrueNAS VM device {args.device_id} and move it from VM {current_vm} "
            f"to VM {target_vm} using the reviewed JSON file?"
        )
    if not _confirm(runtime, operation):
        return 0
    return _emit(runtime, _call(client, "vm.device.update", args.device_id, args.payload))


def _impl_device_delete(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    options = {
        "force": args.force,
        "raw_file": args.delete_raw_file,
        "zvol": args.delete_zvol,
    }
    if not _confirm(
        runtime,
        f"Delete TrueNAS VM device {args.device_id}; delete raw backing file={args.delete_raw_file}, "
        f"delete backing ZFS volume={args.delete_zvol}, force={args.force}?",
    ):
        return 0
    return _emit(runtime, _call(client, "vm.device.delete", args.device_id, options))


def _impl_disk_image_size(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    path = _validated_image_path(args.path)
    size = _call(client, "vm.device.virtual_size", {"path": path})
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise CliFailure("vm.device.virtual_size returned an invalid size")
    return _emit(runtime, {"path": path, "virtual_size_bytes": size})


def _impl_disk_image_import(client: Any, args: argparse.Namespace, runtime: Any, info: dict[str, Any]) -> int:
    source = _validated_image_path(args.source)
    zvol_name = _validated_zvol_name(args.zvol_name)
    virtual_size = _call(client, "vm.device.virtual_size", {"path": source})
    if isinstance(virtual_size, bool) or not isinstance(virtual_size, int) or virtual_size <= 0:
        raise CliFailure("vm.device.virtual_size returned an invalid size")
    zvol_size = args.zvol_size_gib * 1024**3
    if zvol_size < virtual_size:
        minimum_gib = (virtual_size + 1024**3 - 1) // 1024**3
        raise CliFailure(
            f"--zvol-size-gib {args.zvol_size_gib} is smaller than the image's "
            f"{virtual_size}-byte virtual size; choose at least {minimum_gib} GiB",
            exit_code=2,
        )
    _require_vm_stopped_for_disk_import(client, args.vm_id)
    _require_zvol_name_unused(client, zvol_name)
    if not _confirm(
        runtime,
        f"Create ZFS volume {zvol_name} ({zvol_size} bytes) on stopped VM {args.vm_id} "
        f"and convert {source} into it? Keep the VM stopped until conversion is confirmed complete.",
    ):
        return 0

    # The confirmation prompt may take time; recheck immediately before the
    # first side effect so a VM started in the meantime is refused.
    _require_vm_stopped_for_disk_import(client, args.vm_id)
    _require_zvol_name_unused(client, zvol_name)
    device = None
    phase = "disk-device creation"
    try:
        device = _call(
            client,
            "vm.device.create",
            {
                "vm": args.vm_id,
                # The automatic order is not a boot safeguard. Keep the VM
                # stopped and set the requested order after conversion.
                "order": None,
                "attributes": {
                    "dtype": "DISK",
                    "path": None,
                    "type": args.disk_type,
                    "create_zvol": True,
                    "zvol_name": zvol_name,
                    "zvol_volsize": zvol_size,
                },
            },
        )
        if (
            not isinstance(device, dict)
            or isinstance(device.get("id"), bool)
            or not isinstance(device.get("id"), int)
            or device["id"] <= 0
        ):
            raise CliFailure(
                f"TrueNAS may have created the disk device for VM {args.vm_id} and ZFS volume {zvol_name} "
                "but returned no valid device id. Inspect its devices and the volume before retrying."
            )

        phase = "image conversion"
        _call(
            client,
            "vm.device.convert",
            {"source": source, "destination": f"/dev/zvol/{zvol_name}"},
            job=True,
        )

        phase = "boot-order update"
        device = _call(client, "vm.device.update", device["id"], {"order": args.order})
    except KeyboardInterrupt as exc:
        if phase == "disk-device creation":
            detail = (
                f"disk-device creation was interrupted for VM {args.vm_id} and ZFS volume {zvol_name}; "
                "the request may have completed, but disk conversion was not started. The name was unused at "
                "preflight, but a concurrent change may have claimed it; inspect all VM devices and the volume "
                "before retrying."
            )
        elif phase == "image conversion":
            detail = (
                f"image conversion was interrupted after creating VM device {device['id']} and ZFS volume "
                f"{zvol_name}; TrueNAS may still be running the job. Check for a matching "
                "vm.device.convert task."
            )
        else:
            detail = (
                f"image conversion completed, but setting boot order {args.order} for VM device {device['id']} "
                "was interrupted. The update may have completed; inspect and confirm the boot order."
            )
        raise CliFailure(
            f"{detail} Do not start VM {args.vm_id}; see scripts/truenas/docs/CONSUMERS.md for recovery steps.",
            exit_code=130,
        ) from exc
    except CliFailure as exc:
        if phase == "disk-device creation":
            detail = (
                f"TrueNAS did not confirm disk-device creation for VM {args.vm_id} and ZFS volume {zvol_name}. "
                "The name was unused at preflight, but a concurrent change may have claimed it; the request may "
                "also have created the device or volume. Disk conversion was not started."
            )
        elif phase == "image conversion":
            detail = (
                f"image conversion did not complete successfully, or its completion could not be confirmed, after "
                f"creating VM device {device['id']} and ZFS volume {zvol_name}. The volume is preserved."
            )
        else:
            detail = (
                f"image conversion completed, but could not set boot order {args.order} for VM device "
                f"{device['id']}; the device's boot order is unknown and ZFS volume {zvol_name} is preserved."
            )
        raise CliFailure(
            f"{detail} Do not start VM {args.vm_id}; see scripts/truenas/docs/CONSUMERS.md for recovery steps: "
            f"{exc.message}"
        ) from exc
    return _emit(
        runtime,
        {
            "device": device,
            "image": source,
            "virtual_size_bytes": virtual_size,
            "zvol_size_bytes": zvol_size,
            "zvol": zvol_name,
            "conversion": "complete",
        },
    )


def _prepare_file(args: argparse.Namespace) -> None:
    args.payload = _read_json_object(args.file)


def _command_handler(implementation):
    def invoke(args: argparse.Namespace, runtime: Any) -> int:
        try:
            with _session(args) as (client, info):
                return implementation(client, args, runtime, info)
        except CliFailure:
            raise
        except Exception as exc:
            raise CliFailure(f"TrueNAS VM command failed: {exc}") from exc

    return invoke


def build_cli():
    registry = CliRegistry(
        IDENTITY,
        prog="truenas-vm.py",
        description="Inspect and manage the lifecycle and devices of TrueNAS VMs over the v27+ WebSocket API.",
        getting_started=(
            "./truenas-vm.py system-info",
            "./truenas-vm.py vm-list",
            "./truenas-vm.py vm-create --file vm.json",
        ),
        expected_exceptions=(OSError, ValueError),
        unexpected_exceptions="report",
        global_options=(
            _option(("--url",), "TrueNAS TLS WebSocket endpoint; defaults to TRUENAS_URL", group="CONNECTION", metavar="WSS_URL"),
            _option(("--username",), "TrueNAS API-key owner; defaults to TRUENAS_USERNAME", group="CONNECTION", metavar="USER"),
            _option(("--api-key-file",), "protected file containing a TrueNAS API key; defaults to TRUENAS_API_KEY_FILE", group="CONNECTION", metavar="FILE"),
        ),
    )

    def register(name: str, summary: str, implementation, *, arguments=(), options=(), mutating=False, examples=(), prepare=None):
        def handler(args, runtime):
            if prepare:
                prepare(args)
            return _command_handler(implementation)(args, runtime)

        registry.register(
            VerbSpec(
                name,
                None,
                summary,
                group=VerbGroup.MODIFICATION.value if mutating else VerbGroup.EXPLORATION.value,
                examples=examples,
                mutating=mutating,
                include_json=True,
                include_progress=False,
                arguments=arguments,
                options=options,
                handler=handler,
                summary_description=summary,
            )
        )

    register("system-info", "show TrueNAS version and VM hardware-virtualization support", _impl_system_info)
    register("vm-list", "list TrueNAS virtual machines", _impl_vm_list)
    register("vm-show", "show one TrueNAS virtual machine", _impl_vm_show, arguments=(_argument("vm_id", "TrueNAS VM id", metavar="VM_ID", type=_positive_int),))
    register("vm-create", "create a VM from an API v27 JSON object", _impl_vm_create, options=(_option(("--file",), "JSON object accepted by vm.create", group="VM CONFIGURATION", metavar="FILE", required=True),), mutating=True, examples=("./truenas-vm.py vm-create --file vm.json --yes",), prepare=_prepare_file)
    register("vm-update", "update a TrueNAS VM from an API v27 JSON object", _impl_vm_update, arguments=(_argument("vm_id", "TrueNAS VM id", metavar="VM_ID", type=_positive_int),), options=(_option(("--file",), "JSON object accepted by vm.update", group="VM CONFIGURATION", metavar="FILE", required=True),), mutating=True, prepare=_prepare_file)
    register("vm-delete", "delete a TrueNAS VM; backing ZFS volumes are preserved unless requested", _impl_vm_delete, arguments=(_argument("vm_id", "TrueNAS VM id", metavar="VM_ID", type=_positive_int),), options=(_option(("--delete-zvols",), "also delete VM-associated ZFS volumes", group="DELETE OPTIONS", action="store_true", default=False), _option(("--force",), "delete even if the VM is running", group="DELETE OPTIONS", action="store_true", default=False)), mutating=True)
    lifecycle_arg = (_argument("vm_id", "TrueNAS VM id", metavar="VM_ID", type=_positive_int),)
    register("vm-start", "start a TrueNAS VM", _impl_vm_lifecycle, arguments=lifecycle_arg, options=(_option(("--overcommit",), "allow start without enough free memory for configured VMs", group="START OPTIONS", action="store_true", default=False),), mutating=True)
    register("vm-stop", "request guest shutdown, optionally forcing shutdown", _impl_vm_lifecycle, arguments=lifecycle_arg, options=(_option(("--force",), "force immediate poweroff without a graceful guest shutdown", group="STOP OPTIONS", action="store_true", default=False), _option(("--force-after-timeout",), "force poweroff if guest shutdown times out", group="STOP OPTIONS", action="store_true", default=False)), mutating=True)
    for name, summary in (("vm-poweroff", "power off a TrueNAS VM immediately"), ("vm-restart", "restart a TrueNAS VM"), ("vm-suspend", "suspend a TrueNAS VM"), ("vm-resume", "resume a suspended TrueNAS VM")):
        register(name, summary, _impl_vm_lifecycle, arguments=lifecycle_arg, mutating=True)
    register("device-list", "list VM devices, optionally filtered by VM id", _impl_device_list, options=(_option(("--vm-id",), "filter devices belonging to this VM", group="FILTERS", type=_positive_int, metavar="VM_ID"),))
    register("device-create", "create a TrueNAS VM device from an API v27 JSON object", _impl_device_create, arguments=(_argument("vm_id", "TrueNAS VM id; must match the JSON object's vm field", metavar="VM_ID", type=_positive_int),), options=(_option(("--file",), "JSON object accepted by vm.device.create", group="DEVICE CONFIGURATION", metavar="FILE", required=True),), mutating=True, prepare=_prepare_file)
    register("device-update", "update a TrueNAS VM device from an API v27 JSON object", _impl_device_update, arguments=(_argument("device_id", "TrueNAS VM device id", metavar="DEVICE_ID", type=_positive_int),), options=(_option(("--file",), "JSON object accepted by vm.device.update", group="DEVICE CONFIGURATION", metavar="FILE", required=True),), mutating=True, prepare=_prepare_file)
    register("device-delete", "delete a VM device and optionally its backing storage", _impl_device_delete, arguments=(_argument("device_id", "TrueNAS VM device id", metavar="DEVICE_ID", type=_positive_int),), options=(_option(("--force",), "delete even if the device is in use", group="DELETE OPTIONS", action="store_true", default=False), _option(("--delete-raw-file",), "also delete the underlying raw disk file", group="DELETE OPTIONS", action="store_true", default=False), _option(("--delete-zvol",), "also delete the underlying ZFS volume", group="DELETE OPTIONS", action="store_true", default=False)), mutating=True)
    register("disk-image-size", "read the virtual size of a disk image on TrueNAS", _impl_disk_image_size, arguments=(_argument("path", "absolute TrueNAS image path under /mnt", metavar="PATH", type=_nonempty),))
    register("disk-image-import", "convert an image into a VM ZFS disk (VM stopped, autostart disabled)", _impl_disk_image_import, arguments=(_argument("vm_id", "TrueNAS VM id", metavar="VM_ID", type=_positive_int),), options=(_option(("--source",), "absolute disk-image path under /mnt on TrueNAS", group="DISK IMAGE", metavar="PATH", required=True), _option(("--zvol-name",), "new relative ZFS volume name, such as tank/vm/debian-root", group="DISK IMAGE", metavar="POOL/DATASET/VOLUME", required=True), _option(("--zvol-size-gib",), "required final virtual disk capacity in GiB; must fit the source image", group="DISK IMAGE", type=_positive_int, metavar="GIB", required=True), _option(("--order",), "VM boot order priority (lower numbers boot first)", group="DISK DEVICE", type=_positive_int, default=1, metavar="N"), _option(("--disk-type",), "virtual disk controller", group="DISK DEVICE", choices=("AHCI", "VIRTIO"), default="VIRTIO")), mutating=True, examples=("./truenas-vm.py disk-image-import 4 --source /mnt/tank/staging/debian-13-genericcloud-amd64.qcow2 --zvol-name tank/vm/debian-root --zvol-size-gib 40 --yes",))

    return registry.build()


def main(argv=None) -> int:
    return build_cli().run(argv=argv)


if __name__ == "__main__":
    sys.exit(main())
