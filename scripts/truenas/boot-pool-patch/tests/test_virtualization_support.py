from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "shared" / "virtualization_support.py"


def load_module():
    package = types.ModuleType("truenas_pylibvirt")
    utils = types.ModuleType("truenas_pylibvirt.utils")
    utils.kvm_supported = lambda: False
    package.utils = utils
    spec = importlib.util.spec_from_file_location(
        "test_virtualization_support", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "truenas_pylibvirt": package,
            "truenas_pylibvirt.utils": utils,
        },
    ):
        spec.loader.exec_module(module)
    return module


support = load_module()


class VirtualizationSupportTests(unittest.TestCase):
    def test_prefers_kvm_without_requiring_qemu_lookup(self) -> None:
        with (
            patch.object(support, "kvm_supported", return_value=True),
            patch.object(support.shutil, "which") as which,
        ):
            self.assertEqual(support.virtualization_backend(), "KVM")
        which.assert_not_called()

    def test_selects_tcg_only_when_architecture_binary_exists(self) -> None:
        with (
            patch.object(support, "kvm_supported", return_value=False),
            patch.object(support.platform, "machine", return_value="x86_64"),
            patch.object(
                support.shutil, "which", return_value="/usr/bin/qemu-system-x86_64"
            ) as which,
        ):
            self.assertEqual(support.virtualization_backend(), "TCG")
        which.assert_called_once_with("qemu-system-x86_64")

    def test_refuses_to_advertise_virtualization_without_either_backend(self) -> None:
        with (
            patch.object(support, "kvm_supported", return_value=False),
            patch.object(support.platform, "machine", return_value="aarch64"),
            patch.object(support.shutil, "which", return_value=None) as which,
        ):
            self.assertIsNone(support.virtualization_backend())
        which.assert_called_once_with("qemu-system-aarch64")


if __name__ == "__main__":
    unittest.main()
