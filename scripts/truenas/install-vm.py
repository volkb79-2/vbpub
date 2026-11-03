#!/usr/bin/env python3
"""Create and manage a VM on a TrueNAS v27/v28 host."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path, PurePosixPath
import threading
import time
import uuid
from typing import Any

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)

import truenas_api_client as api_module
from truenas_api_client import TrueNASAPIClient, TrueNASAPIError


IDENTITY = CliIdentity.resolve(
    name="TrueNAS VM Installer",
    command="install-vm",
    long_name="TrueNAS virtual machine installer and lifecycle client",
    version_file=Path(__file__).resolve().with_name("VERSION"),
)
IMAGE_FORMATS = {".qcow2", ".qed", ".raw", ".vdi", ".vhdx", ".vmdk"}
TERMINAL_VM_STATES = {"RUNNING", "STOPPED", "SUSPENDED"}


def _argument(name: str, description: str, *, metavar: str | None = None, **kwargs: Any):
    return ArgumentSpec(name, description, metavar=metavar, parser_kwargs=kwargs)


def _option(
    flags: tuple[str, ...],
    description: str,
    *,
    group: str,
    metavar: str | None = None,
    **kwargs: Any,
):
    return OptionSpec(
        flags,
        description,
        group=group,
        metavar=metavar,
        parser_kwargs=kwargs,
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_finite(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def _memory_mib(value: str) -> int:
    parsed = _positive_int(value)
    if parsed < 20:
        raise argparse.ArgumentTypeError("TrueNAS requires at least 20 MiB")
    return parsed


def _vcpus(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > 16:
        raise argparse.ArgumentTypeError("TrueNAS supports at most 16 guest vCPUs")
    return parsed


def _state(vm: dict[str, Any]) -> str | None:
    """Return a known VM state, keeping v28's nullable status as unknown."""

    status = vm.get("status")
    value = status.get("state") if isinstance(status, dict) else None
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _vm_id(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CliFailure(f"TrueNAS returned an invalid VM id while {context}: {value!r}")
    return value


def _create_client(args: Any, runtime: Any) -> TrueNASAPIClient:
    """Read settings and credentials only after a real verb was selected."""

    try:
        api_module.load_env_file()
        config_path = Path(args.config) if getattr(args, "config", None) else None
        settings = api_module.load_settings(config_path)
        api_key = api_module.api_key_from_environment()
    except (OSError, ValueError, TrueNASAPIError) as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    runtime.output.register_secret(api_key)
    return TrueNASAPIClient(settings, api_key)


def _emit(value: Any, runtime: Any) -> None:
    if runtime.json_mode:
        runtime.output.primary_json(value)
    else:
        print(json.dumps(value, indent=2, sort_keys=True), flush=True)


def _dataset_components(value: str, label: str) -> tuple[str, ...]:
    """Validate a dataset path relative to the explicitly selected pool."""

    if not isinstance(value, str) or not value or value.startswith("/"):
        raise CliFailure(f"{label} must be a non-empty path relative to --pool")
    if "\x00" in value or "{" in value or "}" in value:
        raise CliFailure(f"{label} contains unsupported characters")
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise CliFailure(
            f"{label} must use non-empty dataset components and must not contain '.' or '..'"
        )
    return parts


def _mountpoint(record: dict[str, Any], dataset_id: str) -> PurePosixPath:
    value = record.get("mountpoint")
    if not isinstance(value, str) or not value.startswith("/"):
        raise CliFailure(
            f"TrueNAS reports no mounted filesystem path for dataset {dataset_id!r}: "
            f"{value!r}"
        )
    return PurePosixPath(value)


def _rows(value: Any, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise CliFailure(
            f"TrueNAS returned an unexpected {context} shape: expected a list of objects"
        )
    return value


def _pool_inventory(client: TrueNASAPIClient) -> list[dict[str, Any]]:
    pools = _rows(client.call("pool.query"), "pool.query")
    for pool in pools:
        if not isinstance(pool.get("name"), str) or not pool["name"]:
            raise CliFailure("TrueNAS returned a pool without a usable name")
        if not isinstance(pool.get("status"), str):
            raise CliFailure(
                f"TrueNAS returned an invalid status for pool {pool['name']!r}: "
                f"{pool.get('status')!r}"
            )
    return pools


def _dataset_inventory(client: TrueNASAPIClient) -> dict[str, dict[str, Any]]:
    """Return a validated, flat id-to-dataset mapping from the API result."""

    rows = _rows(client.call("pool.dataset.query"), "pool.dataset.query")
    inventory: dict[str, dict[str, Any]] = {}

    def add(record: dict[str, Any]) -> None:
        dataset_id = record.get("id")
        dataset_type = record.get("type")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise CliFailure("TrueNAS returned a dataset without a usable id")
        if dataset_type not in {"FILESYSTEM", "VOLUME"}:
            raise CliFailure(
                f"TrueNAS returned an unknown type for dataset {dataset_id!r}: "
                f"{dataset_type!r}"
            )
        if not isinstance(record.get("pool"), str) or not isinstance(record.get("name"), str):
            raise CliFailure(
                f"TrueNAS returned incomplete pool/name metadata for dataset {dataset_id!r}"
            )
        if dataset_id in inventory:
            raise CliFailure(f"TrueNAS returned duplicate dataset id {dataset_id!r}")
        inventory[dataset_id] = record
        children = record.get("children", [])
        if children is None:
            return
        if not isinstance(children, list):
            raise CliFailure(f"TrueNAS returned malformed children for dataset {dataset_id!r}")
        for child in children:
            if not isinstance(child, dict):
                raise CliFailure(
                    f"TrueNAS returned a non-object child for dataset {dataset_id!r}"
                )
            add(child)

    for row in rows:
        add(row)
    return inventory


def _dataset_display_rows(inventory: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for dataset_id in sorted(inventory):
        dataset = inventory[dataset_id]
        result.append(
            {
                "id": dataset_id,
                "type": dataset["type"],
                "pool": dataset["pool"],
                "relative_path": dataset["name"],
                "mountpoint": dataset.get("mountpoint"),
            }
        )
    return result


def _directory_entry_names(client: TrueNASAPIClient, path: PurePosixPath) -> set[str]:
    entries = _rows(client.call("filesystem.listdir", str(path)), "filesystem.listdir")
    names: set[str] = set()
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str):
            raise CliFailure(
                "TrueNAS filesystem.listdir omitted an entry name; refusing to infer absence"
            )
        names.add(name)
    return names


def _job_progress(method: str, progress: Any):
    last: tuple[Any, ...] | None = None

    def on_update(job: dict[str, Any]) -> None:
        nonlocal last
        raw_state = job.get("state")
        state = raw_state.strip().upper() if isinstance(raw_state, str) else "RUNNING"
        details = job.get("progress")
        percent = details.get("percent") if isinstance(details, dict) else None
        description = details.get("description") if isinstance(details, dict) else None
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            percent = None
        if not isinstance(description, str):
            description = ""
        signature = (state, percent, description)
        if signature == last:
            return
        last = signature
        message = f"{method}: {state}"
        if description.strip():
            message += f" — {description.strip()}"
        progress.update(message, current=percent, total=100 if percent is not None else None)

    return on_update


def _call_job(client: TrueNASAPIClient, method: str, *params: Any, runtime: Any) -> Any:
    progress = runtime.progress()
    try:
        result = client.call_job(
            method,
            *params,
            on_update=_job_progress(method, progress),
        )
    except TrueNASAPIError:
        progress.finish(f"{method} failed")
        raise
    progress.finish(f"{method} completed")
    return result


def _vm_snapshot(client: TrueNASAPIClient, vm_id: int) -> dict[str, Any]:
    value = client.call("vm.get_instance", vm_id)
    if not isinstance(value, dict):
        raise CliFailure(
            f"TrueNAS returned an unexpected vm.get_instance shape for VM {vm_id}: "
            f"{type(value).__name__}"
        )
    return value


def _wait_for_state(
    client: TrueNASAPIClient,
    vm_id: int,
    *,
    target: str | None,
    timeout_seconds: float | None,
    runtime: Any,
) -> dict[str, Any]:
    """Follow vm.query notifications, reading a fresh VM snapshot per change."""

    changed = threading.Event()
    last_state: str | None = None
    start = time.monotonic()
    deadline = start + timeout_seconds if timeout_seconds is not None else None
    progress = runtime.progress()

    def on_event(message_type: str, **message: Any) -> None:
        # Do no RPC work in the WebSocket callback. The caller thread performs
        # vm.get_instance after the event, so the socket reader remains free.
        changed.set()

    subscription_id = client.subscribe("vm.query", on_event, sync=True)
    try:
        while True:
            current = _vm_snapshot(client, vm_id)
            state = _state(current)
            if state != last_state:
                last_state = state
                shown = state or "UNKNOWN (status is null or not yet reported)"
                progress.update(f"VM {vm_id}: {shown}")
            if target is not None and state == target:
                progress.finish(f"VM {vm_id} reached {target}")
                return current

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    progress.finish(f"Timed out waiting for VM {vm_id}")
                    expectation = f" to reach {target}" if target is not None else " for a state change"
                    raise CliFailure(
                        f"timed out waiting for VM {vm_id}{expectation}; "
                        f"last reported state: {state or 'unknown'}"
                    )
                changed.wait(min(remaining, 30.0))
            else:
                changed.wait(30.0)
            changed.clear()
    except KeyboardInterrupt:
        progress.finish(f"Stopped watching VM {vm_id}")
        raise
    finally:
        client.unsubscribe(subscription_id)


def _image_preflight(client: TrueNASAPIClient, args: Any) -> dict[str, Any]:
    if not client.settings.verify_ssl:
        raise CliFailure(
            "install requires api.verify_ssl = true because the file upload endpoint "
            "authenticates with a Bearer API key over HTTPS"
        )
    source = Path(args.source_image)
    try:
        source_stat = source.stat()
    except OSError as exc:
        raise CliFailure(f"could not inspect local source image {source}: {exc}") from exc
    if not source.is_file():
        raise CliFailure(f"local source image is not a regular file: {source}")
    if source_stat.st_size <= 0:
        raise CliFailure(f"local source image is empty: {source}")
    suffix = source.suffix.casefold()
    if suffix not in IMAGE_FORMATS:
        raise CliFailure(
            f"unsupported source image extension {suffix or '(none)'!r}; "
            f"supported extensions: {', '.join(sorted(IMAGE_FORMATS))}"
        )

    pool_rows = _pool_inventory(client)
    pool_matches = [pool for pool in pool_rows if pool["name"] == args.pool]
    if not pool_matches:
        available = ", ".join(sorted(pool["name"] for pool in pool_rows)) or "none"
        raise CliFailure(
            f"pool {args.pool!r} is not available; TrueNAS pools: {available}"
        )
    if len(pool_matches) != 1:
        raise CliFailure(f"TrueNAS returned multiple pools named {args.pool!r}")
    pool = pool_matches[0]
    if pool["status"] != "ONLINE":
        raise CliFailure(
            f"selected pool {args.pool!r} is not ONLINE (status {pool['status']!r})"
        )

    components = _dataset_components(args.dataset, "dataset path")
    datasets = _dataset_inventory(client)
    root = datasets.get(args.pool)
    if root is None or root.get("type") != "FILESYSTEM":
        raise CliFailure(
            f"TrueNAS did not report the filesystem root dataset for pool {args.pool!r}"
        )
    dataset_ids = [args.pool]
    for component in components:
        dataset_ids.append(f"{dataset_ids[-1]}/{component}")

    missing_datasets: list[str] = []
    for dataset_id in dataset_ids[1:]:
        record = datasets.get(dataset_id)
        if record is None:
            missing_datasets.append(dataset_id)
        elif record.get("type") != "FILESYSTEM":
            raise CliFailure(
                f"dataset path component {dataset_id!r} already exists as "
                f"{record.get('type')!r}, expected FILESYSTEM"
            )
    target_dataset_id = dataset_ids[-1]
    target_dataset = datasets.get(target_dataset_id)
    zvol_id = f"{target_dataset_id}/{args.zvol_name}"
    if zvol_id in datasets:
        raise CliFailure(
            f"target ZVOL already exists as dataset {zvol_id!r}; choose another --zvol-name"
        )

    remote_image_path: str | None = None
    if target_dataset is not None:
        mountpoint = _mountpoint(target_dataset, target_dataset_id)
        remote_image_path = str(mountpoint / source.name)
        if source.name in _directory_entry_names(client, mountpoint):
            raise CliFailure(
                f"image file already exists in TrueNAS dataset {target_dataset_id!r}: "
                f"{remote_image_path}; choose a different local filename or dataset"
            )

    vms = _rows(client.call("vm.query", [["name", "=", args.name]]), "vm.query")
    conflicts = [item for item in vms if item.get("name") == args.name]
    if conflicts:
        ids = ", ".join(str(item.get("id", "unknown")) for item in conflicts)
        raise CliFailure(f"VM name {args.name!r} already exists (VM id(s): {ids})")

    virtualization = client.call("vm.supports_virtualization")
    if virtualization is not True:
        if virtualization is False:
            raise CliFailure("TrueNAS reports that hardware virtualization is unavailable")
        raise CliFailure(
            "TrueNAS returned an indeterminate vm.supports_virtualization response; "
            f"expected true or false, got {virtualization!r}"
        )

    choices = client.call("vm.device.nic_attach_choices")
    if isinstance(choices, dict):
        nic_names = set()
        if set(choices) != {"BRIDGE", "MACVLAN"}:
            raise CliFailure(
                "TrueNAS returned an unexpected vm.device.nic_attach_choices object; "
                "expected BRIDGE and MACVLAN arrays"
            )
        for category in ("BRIDGE", "MACVLAN"):
            entries = choices[category]
            if not isinstance(entries, list) or any(not isinstance(item, str) for item in entries):
                raise CliFailure(
                    f"TrueNAS returned an invalid {category} NIC attachment list"
                )
            nic_names.update(entries)
    elif isinstance(choices, list):
        nic_names = set()
        for choice in choices:
            if isinstance(choice, str):
                nic_names.add(choice)
            elif isinstance(choice, dict):
                name = choice.get("name", choice.get("id", choice.get("value")))
                if isinstance(name, str):
                    nic_names.add(name)
                else:
                    raise CliFailure(
                        "TrueNAS returned an unrecognized vm.device.nic_attach_choices entry"
                    )
            else:
                raise CliFailure("TrueNAS returned an unrecognized NIC attachment choice")
    else:
        raise CliFailure(
            "TrueNAS returned an unexpected vm.device.nic_attach_choices shape"
        )
    if args.nic_attach not in nic_names:
        choices_text = ", ".join(sorted(nic_names)) or "none"
        raise CliFailure(
            f"NIC attachment {args.nic_attach!r} is not available on TrueNAS; "
            f"choices: {choices_text}"
        )

    return {
        "name": args.name,
        "local_source_image": str(source),
        "source_size_bytes": source_stat.st_size,
        "pool": args.pool,
        "dataset": target_dataset_id,
        "datasets_to_create": missing_datasets,
        "uploaded_image_path": remote_image_path,
        "zvol": zvol_id,
        "zvol_path": f"/dev/zvol/{zvol_id}",
        "disk_size_gib": args.disk_size_gib,
        "disk_size_bytes": args.disk_size_gib * 1024**3,
        "memory_mib": args.memory_mib,
        "vcpus": args.vcpus,
        "nic_attach": args.nic_attach,
        "bootloader": args.bootloader,
        "autostart": args.autostart,
        "start_after_install": args.start,
    }


def cmd_install(args: Any, runtime: Any) -> int:
    # Validate local syntax before loading secrets or opening a socket.
    _dataset_components(args.dataset, "dataset path")
    _dataset_components(args.zvol_name, "ZVOL name")
    if len(_dataset_components(args.zvol_name, "ZVOL name")) != 1:
        raise CliFailure("--zvol-name must be one dataset component without '/' separators")
    source = Path(args.source_image)
    if not source.name or source.name in {".", ".."}:
        raise CliFailure("--source-image must name a local image file")
    with _create_client(args, runtime) as client:
        plan = _image_preflight(client, args)
        if runtime.dry_run:
            _emit({"result": "planned", **plan}, runtime)
            return 0
        if not runtime.confirm(
            f"Upload {plan['local_source_image']} to dataset {plan['dataset']!r}, "
            f"create ZVOL {plan['zvol']!r} ({plan['disk_size_gib']} GiB), "
            f"and create VM {args.name!r} on TrueNAS?"
        ):
            return 0

        vm_id: int | None = None
        created_datasets: list[str] = []
        uploaded_image_path = plan["uploaded_image_path"]
        stage = "filesystem dataset creation"
        try:
            for dataset_id in plan["datasets_to_create"]:
                created_dataset = client.call(
                    "pool.dataset.create",
                    {"name": dataset_id, "type": "FILESYSTEM"},
                )
                if not isinstance(created_dataset, dict) or created_dataset.get("id") != dataset_id:
                    raise CliFailure(
                        f"TrueNAS returned an unexpected result creating dataset "
                        f"{dataset_id!r}: {created_dataset!r}"
                    )
                created_datasets.append(dataset_id)

            datasets = _dataset_inventory(client)
            target_dataset = datasets.get(plan["dataset"])
            if target_dataset is None or target_dataset.get("type") != "FILESYSTEM":
                raise CliFailure(
                    f"TrueNAS did not report the created target filesystem dataset "
                    f"{plan['dataset']!r}"
                )
            mountpoint = _mountpoint(target_dataset, plan["dataset"])
            uploaded_image_path = str(mountpoint / Path(plan["local_source_image"]).name)
            if Path(uploaded_image_path).name in _directory_entry_names(client, mountpoint):
                raise CliFailure(
                    f"image destination appeared after preflight and already exists: "
                    f"{uploaded_image_path}; refusing to overwrite it"
                )

            stage = "image upload"
            upload_progress = runtime.progress()
            last_percent: int | None = None

            def upload_update(sent: int, total: int) -> None:
                nonlocal last_percent
                percent = min(100, int(sent * 100 / total)) if total else 0
                if percent == last_percent:
                    return
                last_percent = percent
                upload_progress.update(
                    f"Uploading {Path(plan['local_source_image']).name}",
                    current=sent,
                    total=total,
                )

            try:
                upload_job_id = client.upload_job_file(
                    "filesystem.put",
                    [uploaded_image_path, {"mode": 0o644}],
                    plan["local_source_image"],
                    on_progress=upload_update,
                )
            except TrueNASAPIError:
                upload_progress.finish("Image upload failed")
                raise
            upload_progress.finish(f"Image uploaded; waiting for job {upload_job_id}")
            upload_job_progress = runtime.progress()
            try:
                upload_result = client.wait_job(
                    upload_job_id,
                    on_update=_job_progress("filesystem.put", upload_job_progress),
                    timeout_seconds=args.job_timeout_seconds,
                )
            except TrueNASAPIError:
                upload_job_progress.finish("filesystem.put failed")
                raise
            upload_job_progress.finish("filesystem.put completed")
            if upload_result.get("result") is not True:
                raise CliFailure(
                    "filesystem.put completed without a true success result; "
                    f"TrueNAS returned {upload_result.get('result')!r}"
                )
            uploaded_stat = client.call("filesystem.stat", uploaded_image_path)
            if (
                not isinstance(uploaded_stat, dict)
                or uploaded_stat.get("type") != "FILE"
                or uploaded_stat.get("size") != plan["source_size_bytes"]
            ):
                raise CliFailure(
                    "TrueNAS upload verification did not match the local source file "
                    f"size/type: {uploaded_stat!r}"
                )

            stage = "VM creation"
            created = client.call(
                "vm.create",
                {
                    "name": args.name,
                    "description": args.description,
                    "memory": args.memory_mib,
                    "vcpus": args.vcpus,
                    "bootloader": args.bootloader,
                    # v28 requires a non-null UUID in VM responses; an explicit
                    # UUID keeps the create response valid across both schemas.
                    "uuid": str(uuid.uuid4()),
                    # Do not silently add this VM to TrueNAS host boot order.
                    "autostart": args.autostart,
                },
            )
            if not isinstance(created, dict):
                raise CliFailure("TrueNAS returned an unexpected vm.create response")
            vm_id = _vm_id(created.get("id"), "creating the VM")

            stage = "ZVOL creation and disk attachment"
            client.call(
                "vm.device.create",
                {
                    "vm": vm_id,
                    "order": 1,
                    "attributes": {
                        "dtype": "DISK",
                        "path": None,
                        "type": "VIRTIO",
                        "boot": True,
                        "create_zvol": True,
                        "zvol_name": plan["zvol"],
                        "zvol_volsize": plan["disk_size_bytes"],
                    },
                },
            )
            stage = "image conversion into ZVOL"
            converted = _call_job(
                client,
                "vm.device.convert",
                {
                    "source": uploaded_image_path,
                    "destination": plan["zvol_path"],
                },
                runtime=runtime,
            )
            if converted is not True:
                raise CliFailure(
                    "vm.device.convert completed without a true success result; "
                    f"TrueNAS returned {converted!r}"
                )

            stage = "NIC attachment"
            client.call(
                "vm.device.create",
                {
                    "vm": vm_id,
                    "order": 2,
                    "attributes": {
                        "dtype": "NIC",
                        "type": "VIRTIO",
                        "nic_attach": args.nic_attach,
                    },
                },
            )
            if args.start:
                stage = "VM start"
                client.call("vm.start", vm_id, {"overcommit": False})
                _wait_for_state(
                    client,
                    vm_id,
                    target="RUNNING",
                    timeout_seconds=args.start_timeout_seconds,
                    runtime=runtime,
                )
        except (TrueNASAPIError, CliFailure) as exc:
            artifacts = [f"uploaded image path: {uploaded_image_path}"]
            artifacts.append(f"target ZVOL: {plan['zvol']}")
            if created_datasets:
                artifacts.append("created dataset(s): " + ", ".join(created_datasets))
            if vm_id is not None:
                artifacts.append(f"VM id: {vm_id}")
            raise CliFailure(
                f"installation stopped during {stage}: {exc}. Existing artifacts may remain: "
                + "; ".join(artifacts)
            ) from exc

    _emit(
        {
            "result": "installed",
            "vm_id": vm_id,
            **plan,
            "uploaded_image_path": uploaded_image_path,
        },
        runtime,
    )
    return 0


def cmd_pools(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        pools = _pool_inventory(client)
    selected = [
        {
            "name": pool["name"],
            "status": pool["status"],
            "healthy": pool.get("healthy"),
            "warning": pool.get("warning"),
            "size": pool.get("size"),
            "free": pool.get("free"),
            "path": pool.get("path"),
        }
        for pool in pools
    ]
    _emit(selected, runtime)
    return 0


def cmd_datasets(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        if args.pool:
            pools = _pool_inventory(client)
            if not any(pool["name"] == args.pool for pool in pools):
                available = ", ".join(sorted(pool["name"] for pool in pools)) or "none"
                raise CliFailure(
                    f"pool {args.pool!r} is not available; TrueNAS pools: {available}"
                )
        datasets = _dataset_inventory(client)
    rows = _dataset_display_rows(datasets)
    if args.pool:
        rows = [row for row in rows if row["pool"] == args.pool or row["id"].startswith(f"{args.pool}/")]
    _emit(rows, runtime)
    return 0


def cmd_list(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        _emit(client.call("vm.query"), runtime)
    return 0


def cmd_status(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        _emit(_vm_snapshot(client, args.vm_id), runtime)
    return 0


def cmd_start(args: Any, runtime: Any) -> int:
    if runtime.dry_run:
        _emit({"result": "planned", "method": "vm.start", "vm_id": args.vm_id}, runtime)
        return 0
    with _create_client(args, runtime) as client:
        current = _vm_snapshot(client, args.vm_id)
        current_state = _state(current)
        if current_state == "RUNNING":
            _emit({"result": "already-running", "vm": current}, runtime)
            return 0
        if not runtime.confirm(f"Start TrueNAS VM {args.vm_id}?"):
            return 0
        result = client.call("vm.start", args.vm_id, {"overcommit": args.overcommit})
        if not args.no_wait:
            vm = _wait_for_state(
                client,
                args.vm_id,
                target="RUNNING",
                timeout_seconds=args.timeout_seconds,
                runtime=runtime,
            )
        else:
            vm = {"start_request": result, "previous_state": current_state}
    _emit({"result": "start-requested", "vm_id": args.vm_id, "vm": vm}, runtime)
    return 0


def cmd_stop(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        current = _vm_snapshot(client, args.vm_id)
        if _state(current) == "STOPPED":
            _emit({"result": "already-stopped", "vm": current}, runtime)
            return 0
        if runtime.dry_run:
            _emit(
                {"result": "planned", "method": "vm.stop", "vm_id": args.vm_id, "force": args.force},
                runtime,
            )
            return 0
        if not runtime.confirm(
            f"Stop TrueNAS VM {args.vm_id}{' immediately' if args.force else ' gracefully'}?"
        ):
            return 0
        result = _call_job(
            client,
            "vm.stop",
            args.vm_id,
            {"force": args.force},
            runtime=runtime,
        )
    _emit({"result": "stop-completed", "vm_id": args.vm_id, "job_result": result}, runtime)
    return 0


def cmd_delete(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        current = _vm_snapshot(client, args.vm_id)
        current_state = _state(current)
        if current_state != "STOPPED" and not args.force:
            detail = current_state or "unknown because TrueNAS status is null"
            raise CliFailure(
                f"VM {args.vm_id} is not confirmed stopped (state: {detail}); "
                "stop it first or pass --force after reviewing the VM"
            )
        options = {"force": args.force, "zvols": args.delete_zvols}
        if runtime.dry_run:
            _emit(
                {"result": "planned", "method": "vm.delete", "vm_id": args.vm_id, **options},
                runtime,
            )
            return 0
        data_effect = " and its TrueNAS-managed ZVOLs" if args.delete_zvols else ""
        if not runtime.confirm(
            f"Delete TrueNAS VM {args.vm_id}{data_effect}"
            f"{' using force' if args.force else ''}? This cannot be undone."
        ):
            return 0
        result = client.call("vm.delete", args.vm_id, options)
    _emit({"result": "deleted", "vm_id": args.vm_id, "api_result": result}, runtime)
    return 0


def cmd_watch(args: Any, runtime: Any) -> int:
    target = args.until.upper() if args.until else None
    if target is not None and target not in TERMINAL_VM_STATES:
        raise CliFailure(
            f"unsupported --until state {args.until!r}; choose one of: "
            + ", ".join(sorted(TERMINAL_VM_STATES))
        )
    with _create_client(args, runtime) as client:
        vm = _wait_for_state(
            client,
            args.vm_id,
            target=target,
            timeout_seconds=args.timeout_seconds,
            runtime=runtime,
        )
    _emit(vm, runtime)
    return 0


def build_cli():
    registry = CliRegistry(
        IDENTITY,
        prog="install-vm.py",
        description="Create and manage virtual machines on TrueNAS v27/v28.",
        getting_started=(
            "./install-vm.py pools",
            "./install-vm.py datasets tank",
            "./install-vm.py list",
            "./install-vm.py install debian --source-image ./debian.qcow2 --pool tank --dataset vm-images/debian --zvol-name root --disk-size-gib 32 --memory-mib 2048 --vcpus 2 --nic-attach br0",
            "./install-vm.py watch 1 --until RUNNING",
        ),
        logging_logger="truenas.install_vm",
        unexpected_exceptions="report",
        expected_exceptions=(TrueNASAPIError, OSError, ValueError),
        global_options=(
            _option(
                ("--config",),
                "TrueNAS settings TOML (default: truenas.toml or TRUENAS_CONFIG)",
                group="LOCAL CONFIGURATION",
                metavar="FILE",
                default=None,
            ),
        ),
    )

    registry.register(
        VerbSpec(
            "install",
            "NAME",
            "Upload a local disk image to a selected TrueNAS dataset, create a ZVOL at the requested virtual size, convert the image into that ZVOL, and attach it with a NIC to a new VM. Missing dataset path components are created below the selected pool. The source is a virtual disk image such as a Debian cloud QCOW2, not an installer ISO.",
            group=VerbGroup.MODIFICATION.value,
            examples=(
                "./install-vm.py install debian13 --source-image ./debian-13-generic-amd64.qcow2 --pool tank --dataset vm-images/debian13 --zvol-name root --disk-size-gib 32 --memory-mib 2048 --vcpus 2 --nic-attach br0",
                "./install-vm.py install debian12 --source-image ./debian.qcow2 --pool tank --dataset vm-images/debian12 --zvol-name root --disk-size-gib 32 --memory-mib 4096 --vcpus 4 --nic-attach br0 --start",
            ),
            arguments=(
                _argument("name", "new TrueNAS VM display name"),
            ),
            options=(
                _option(("--source-image",), "local source disk image file to upload over HTTPS", group="IMAGE INPUT", metavar="FILE", required=True),
                _option(("--pool",), "existing ONLINE TrueNAS ZFS pool to use", group="STORAGE", metavar="POOL", required=True),
                _option(("--dataset",), "dataset path relative to --pool; missing components are created", group="STORAGE", metavar="DATASET/PATH", required=True),
                _option(("--zvol-name",), "new ZVOL name within the selected dataset", group="STORAGE", metavar="NAME", required=True),
                _option(("--disk-size-gib",), "virtual size for the new ZVOL in GiB; must fit the source image", group="STORAGE", metavar="GIB", type=_positive_int, required=True),
                _option(("--memory-mib",), "guest memory in mebibytes (minimum 20)", group="VM RESOURCES", metavar="MIB", type=_memory_mib, required=True),
                _option(("--vcpus",), "guest virtual CPU count (TrueNAS permits at most 16)", group="VM RESOURCES", metavar="COUNT", type=_vcpus, default=2),
                _option(("--description",), "optional VM description", group="VM SETTINGS", metavar="TEXT", default=""),
                _option(("--bootloader",), "guest firmware mode", group="VM SETTINGS", choices=("UEFI", "UEFI_CSM"), default="UEFI"),
                _option(("--nic-attach",), "existing TrueNAS network interface or bridge", group="VM NETWORK", metavar="NAME", required=True),
                _option(("--autostart",), "start this VM automatically when TrueNAS boots", group="VM SETTINGS", action="store_true", default=False),
                _option(("--start",), "start the VM after installation and wait for RUNNING", group="VM SETTINGS", action="store_true", default=False),
                _option(("--start-timeout-seconds",), "maximum time to wait for RUNNING after --start", group="VM SETTINGS", metavar="SECONDS", type=_positive_finite, default=180.0),
                _option(("--job-timeout-seconds",), "optional maximum time to wait for the uploaded filesystem.put job", group="JOB MONITORING", metavar="SECONDS", type=_positive_finite, default=None),
            ),
            mutating=True,
            dry_run=True,
            include_progress=True,
            handler=cmd_install,
            summary_description="upload a local disk image and create a ZVOL-backed VM",
        )
    )
    registry.register(
        VerbSpec(
            "pools",
            None,
            "list TrueNAS ZFS pools and their current health and capacity facts",
            group=VerbGroup.EXPLORATION.value,
            examples=("./install-vm.py pools",),
            handler=cmd_pools,
            summary_description="query pool.query",
        )
    )
    registry.register(
        VerbSpec(
            "datasets",
            "[POOL]",
            "list datasets and ZVOLs, optionally limited to one pool",
            group=VerbGroup.EXPLORATION.value,
            arguments=(_argument("pool", "pool name to filter by", nargs="?", default=None),),
            examples=("./install-vm.py datasets", "./install-vm.py datasets tank"),
            handler=cmd_datasets,
            summary_description="query pool.dataset.query",
        )
    )
    registry.register(
        VerbSpec(
            "list",
            None,
            "list configured virtual machines and runtime status",
            group=VerbGroup.EXPLORATION.value,
            examples=("./install-vm.py list",),
            handler=cmd_list,
            summary_description="query vm.query",
        )
    )
    registry.register(
        VerbSpec(
            "status",
            "VM_ID",
            "show one VM's configuration and runtime status",
            group=VerbGroup.EXPLORATION.value,
            arguments=(_argument("vm_id", "numeric TrueNAS VM id", type=_positive_int),),
            examples=("./install-vm.py status 1",),
            handler=cmd_status,
            summary_description="query vm.get_instance",
        )
    )
    registry.register(
        VerbSpec(
            "start",
            "VM_ID",
            "Start a VM and report state changes. The command uses vm.start and then follows vm.query notifications until the API reports RUNNING. Use --no-wait to return after the start request is accepted.",
            group=VerbGroup.MODIFICATION.value,
            arguments=(_argument("vm_id", "numeric TrueNAS VM id", type=_positive_int),),
            options=(
                _option(("--overcommit",), "allow TrueNAS to overcommit VM memory", group="START OPTIONS", action="store_true", default=False),
                _option(("--no-wait",), "return as soon as vm.start accepts the request", group="START OPTIONS", action="store_true", default=False),
                _option(("--timeout-seconds",), "maximum time to wait for RUNNING", group="START OPTIONS", metavar="SECONDS", type=_positive_finite, default=180.0),
            ),
            examples=("./install-vm.py start 1", "./install-vm.py start 1 --no-wait"),
            mutating=True,
            dry_run=True,
            include_progress=True,
            handler=cmd_start,
            summary_description="start a VM with event based state reporting",
        )
    )
    registry.register(
        VerbSpec(
            "stop",
            "VM_ID",
            "stop a VM and follow the TrueNAS job to completion",
            group=VerbGroup.MODIFICATION.value,
            arguments=(_argument("vm_id", "numeric TrueNAS VM id", type=_positive_int),),
            options=(
                _option(("--force",), "force power off instead of requesting a graceful stop", group="STOP OPTIONS", action="store_true", default=False),
            ),
            examples=("./install-vm.py stop 1", "./install-vm.py stop 1 --force"),
            mutating=True,
            dry_run=True,
            include_progress=True,
            handler=cmd_stop,
            summary_description="follow vm.stop job progress and completion",
        )
    )
    registry.register(
        VerbSpec(
            "delete",
            "VM_ID",
            "delete a VM configuration, optionally deleting its TrueNAS-managed ZVOLs",
            group=VerbGroup.MODIFICATION.value,
            arguments=(_argument("vm_id", "numeric TrueNAS VM id", type=_positive_int),),
            options=(
                _option(("--delete-zvols",), "also delete ZVOLs managed by this VM", group="DELETE OPTIONS", action="store_true", default=False),
                _option(("--force",), "allow deletion when running or when TrueNAS status is unknown", group="DELETE OPTIONS", action="store_true", default=False),
            ),
            examples=("./install-vm.py delete 1", "./install-vm.py delete 1 --delete-zvols"),
            mutating=True,
            dry_run=True,
            include_progress=False,
            handler=cmd_delete,
            summary_description="delete a VM with explicit ZVOL retention choice",
        )
    )
    registry.register(
        VerbSpec(
            "watch",
            "VM_ID",
            "stream one VM's state changes until interrupted or a target state is reached",
            group=VerbGroup.EXPLORATION.value,
            arguments=(_argument("vm_id", "numeric TrueNAS VM id", type=_positive_int),),
            options=(
                _option(("--until",), "return when state reaches RUNNING, STOPPED, or SUSPENDED", group="STATE MONITORING", choices=tuple(sorted(TERMINAL_VM_STATES)), default=None),
                _option(("--timeout-seconds",), "stop waiting after this many seconds; omitted means wait until Ctrl-C", group="STATE MONITORING", metavar="SECONDS", type=_positive_finite, default=None),
            ),
            examples=("./install-vm.py watch 1", "./install-vm.py watch 1 --until RUNNING --timeout-seconds 180"),
            include_progress=True,
            handler=cmd_watch,
            summary_description="follow vm.query event notifications",
        )
    )
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
