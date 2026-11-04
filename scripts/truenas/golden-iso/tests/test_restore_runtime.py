"""Regression coverage for the unattended restore's repeat-boot guard."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


RUNTIME_PATH = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "truenas-restore.py"
)
SPEC = importlib.util.spec_from_file_location("truenas_restore_runtime", RUNTIME_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


def installer_with_gpt_match(receipt: dict[str, Any] | None) -> Any:
    installer = object.__new__(RUNTIME.Installer)
    installer.manifest = {
        "raw_image_sha256": "a" * 64,
        "raw_image_size_bytes": 16 * 1024**3,
        "disk_guid": "11111111-2222-3333-4444-555555555555",
        "partitions": [
            {"number": 2, "unique_guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        ],
    }
    installer._matches_golden_gpt = lambda _device: True
    installer._read_restore_receipt = lambda _device: receipt
    installer.notifications = []
    installer.notify = lambda title, detail: installer.notifications.append(
        (title, detail)
    )
    return installer


def test_matching_gpt_without_image_receipt_does_not_skip_restore() -> None:
    installer = installer_with_gpt_match(None)

    assert installer._already_installed("/dev/vda") is False
    assert installer.notifications[0][0] == "restore receipt missing or mismatched"
    assert "GPT identity alone does not prove" in installer.notifications[0][1]


def test_matching_gpt_and_image_receipt_can_skip_repeat_restore() -> None:
    installer = installer_with_gpt_match(None)
    receipt = installer._restore_receipt()
    installer._read_restore_receipt = lambda _device: receipt

    assert installer._already_installed("/dev/vda") is True
    assert installer.notifications[0][0] == "restore receipt matched"


def test_receipt_for_another_image_does_not_skip_restore() -> None:
    installer = installer_with_gpt_match({"raw_image_sha256": "b" * 64})

    assert installer._already_installed("/dev/vda") is False
    assert installer.notifications[0][0] == "restore receipt missing or mismatched"


def test_efi_partition_discovery_requests_tree_output(monkeypatch: Any) -> None:
    installer = object.__new__(RUNTIME.Installer)
    efi_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    installer.manifest = {
        "raw_image_sha256": "a" * 64,
        "raw_image_size_bytes": 16 * 1024**3,
        "disk_guid": "11111111-2222-3333-4444-555555555555",
        "partitions": [
            {
                "number": 2,
                "unique_guid": efi_guid,
            }
        ],
    }
    efi_type = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
    tree_result = {
        "blockdevices": [
            {
                "path": "/dev/vda",
                "type": "disk",
                "children": [
                    {"path": "/dev/vda1", "type": "part", "partn": 1},
                    {
                        "path": "/dev/vda2",
                        "type": "part",
                        "partn": 2,
                        "parttype": efi_type,
                        "partuuid": efi_guid,
                    },
                    {"path": "/dev/vda3", "type": "part", "partn": 3},
                ],
            }
        ]
    }
    flat_result = {
        "blockdevices": [
            {"path": "/dev/vda", "type": "disk"},
            {"path": "/dev/vda1", "type": "part", "partn": 1},
            {
                "path": "/dev/vda2",
                "type": "part",
                "partn": 2,
                "parttype": efi_type,
                "partuuid": efi_guid,
            },
            {"path": "/dev/vda3", "type": "part", "partn": 3},
        ]
    }
    commands: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(argv)
        output = tree_result if "--tree" in argv else flat_result
        return SimpleNamespace(stdout=json.dumps(output))

    monkeypatch.setattr(RUNTIME, "run", fake_run)

    assert installer._efi_partition_device("/dev/vda") == "/dev/vda2"
    assert commands and "--tree" in commands[0]
