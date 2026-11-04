"""MDT-TESTHOST-IO: HOST_ROLE and measured (iobench) dev.slice limits.  Hermetic: fakes only."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "mdt-container-io-caps.lib.sh"
HELPER = ROOT / "scripts" / "mdt_iobench_caps.py"
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("mdt_iobench_caps_t", HELPER)
caps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(caps)

DISK = {"devno": "254:0", "model": "QEMU", "serial": "vda-1", "size_bytes": 1000, "wwn": ""}


def result(**over) -> dict:
    doc = {
        "schema": 1,
        "run": {"status": "complete"},
        "quality": {"plausible": True},
        "identity": {"disk": dict(DISK)},
        "proposals": {"caps": {"dev_aggregate": {
            "RIOPS": {"value": 8000, "status": "proven", "rule": "R5"},
            "WIOPS": {"value": 4000, "status": "unproven", "reasons": ["worst_from_single_run"], "rule": "R5"},
            "RBPS": {"value": 0, "status": "absent", "rule": "R5"},
            "WBPS": {"value": 262144000, "status": "proven", "rule": "R5"},
        }}},
    }
    doc.update(over)
    return doc


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, doc) -> Path:
        path = self.tmp / "result.json"
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return path


class ReaderTests(Base):
    live = {"model": "QEMU", "serial": "vda-1", "size_bytes": "1000"}

    def load(self, doc, devno="254:0", identity=None):
        return caps.load_caps(self.write(doc), devno, self.live if identity is None else identity)

    def test_proven_and_unproven_values_are_used_absent_ones_skipped(self) -> None:
        values, notes = self.load(result())
        self.assertEqual(values, {"RIOPS": 8000, "WIOPS": 4000, "WBPS": 262144000})
        self.assertIn("worst_from_single_run", notes["WIOPS"])

    def test_refusals(self) -> None:
        cases = {
            "absent": (self.tmp / "nope.json", "no benchmark result"),
            "corrupt": ("{not json", "not a result document"),
            "newer schema": (result(schema=2), "schema 2"),
            "incomplete": (result(run={"status": "aborted"}), "not complete"),
            "implausible": (result(quality={"plausible": False}), "implausible"),
            "no caps (matrix-only)": (result(proposals={"writeback": {}}), "iobench calibrate"),
        }
        for name, (doc, text) in cases.items():
            with self.subTest(name):
                path = doc if isinstance(doc, Path) else self.write(doc)
                with self.assertRaisesRegex(caps.NotUsable, text):
                    caps.load_caps(path, "254:0", self.live)

    def test_identity_must_match_and_devno_alone_is_not_enough(self) -> None:
        with self.assertRaisesRegex(caps.NotUsable, "root disk is 8:0"):
            self.load(result(), devno="8:0")
        with self.assertRaisesRegex(caps.NotUsable, "serial"):
            self.load(result(), identity={"serial": "other"})
        with self.assertRaisesRegex(caps.NotUsable, "device number only"):
            self.load(result(), identity={})
        with self.assertRaisesRegex(caps.NotUsable, "could not be derived"):
            caps.load_caps(self.write(result()), None, self.live)

    def test_cli_prints_key_value_lines_and_exit_codes(self) -> None:
        path = self.write(result())
        ok = subprocess.run([sys.executable, str(HELPER), "--result", str(path), "--devno", "254:0", "--serial", "vda-1"],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(ok.stdout.split(), ["RIOPS=8000", "WIOPS=4000", "WBPS=262144000"])
        bad = subprocess.run([sys.executable, str(HELPER), "--result", str(path), "--devno", "8:0", "--serial", "vda-1"],
                             capture_output=True, text=True)
        self.assertEqual((bad.returncode, bad.stdout), (3, ""))


class ShellResolutionTests(Base):
    def bash(self, body: str, env: str = "", doc=None) -> subprocess.CompletedProcess:
        result_path = self.write(doc if doc is not None else result())
        script = f'''
log() {{ echo "$*"; }}
. "{LIB}"
_mdt_root_disk_node() {{ echo /dev/vda; }}
_mdt_root_disk_majmin() {{ echo 254:0; }}
_mdt_io_disk_identity() {{ printf 'model=QEMU\\nserial=vda-1\\nsize_bytes=1000\\n'; }}
export MDT_IOBENCH_CAPS_HELPER="{HELPER}" IOBENCH_RESULT="{result_path}"
{env}
{body}
'''
        return subprocess.run(["bash", "-c", script], text=True, capture_output=True)

    def test_shared_prod_and_unset_keep_the_static_fallback(self) -> None:
        for role in ("", "HOST_ROLE=shared-prod"):
            with self.subTest(role=role):
                r = self.bash('_mdt_resolve_dev_io_caps; echo "rc=$? ri=$DEV_RIOPS wi=$DEV_WIOPS src=$DEV_IO_SRC"', role)
                self.assertIn("rc=0 ri=500 wi=500", r.stdout, r.stderr)
                self.assertNotIn("iobench", r.stdout)

    def test_dedicated_dev_uses_the_measured_caps_not_the_static_values(self) -> None:
        r = self.bash('_mdt_resolve_dev_io_caps; echo "rc=$? r=$DEV_RBPS w=$DEV_WBPS ri=$DEV_RIOPS wi=$DEV_WIOPS $DEV_IO_SRC"',
                      "HOST_ROLE=dedicated-dev DEV_IO_RBPS_MAX=300M")
        self.assertIn("rc=0 r=314572800 w=262144000 ri=8000 wi=4000", r.stdout, r.stdout + r.stderr)
        self.assertIn("RBPS=pinned WBPS=iobench RIOPS=iobench WIOPS=iobench", r.stdout)

    def test_dedicated_dev_pin_wins_over_measurement_per_value(self) -> None:
        r = self.bash('_mdt_resolve_dev_io_caps; echo "ri=$DEV_RIOPS"', "HOST_ROLE=dedicated-dev DEV_IO_RIOPS_MAX=1000")
        self.assertIn("ri=1000", r.stdout)

    def test_dedicated_dev_without_pin_or_measurement_is_unresolved_with_remedy(self) -> None:
        r = self.bash('_mdt_resolve_dev_io_caps; echo "rc=$? un=$MDT_DEDICATED_UNRESOLVED ri=[$DEV_RIOPS]"',
                      "HOST_ROLE=dedicated-dev", doc=result(identity={"disk": {**DISK, "devno": "8:0"}}))
        self.assertIn("rc=1", r.stdout)
        self.assertIn("un=RBPS WBPS RIOPS WIOPS ri=[]", r.stdout)
        self.assertIn("NO static fallback", r.stdout)
        self.assertIn("iobench calibrate", r.stdout)

    def test_dedicated_dev_does_not_apply_static_values_even_when_set(self) -> None:
        r = self.bash('_mdt_resolve_static_io; echo "rc=$? ri=[$MDT_STATIC_RIOPS]"',
                      "HOST_ROLE=dedicated-dev DEV_STATIC_RIOPS=500", doc="{broken")
        self.assertIn("rc=1 ri=[]", r.stdout)

    def test_invalid_role_is_loud_and_conservative(self) -> None:
        r = self.bash('_mdt_resolve_static_io; echo "rc=$? ri=$MDT_STATIC_RIOPS"', "HOST_ROLE=dedicated")
        self.assertIn("rc=1 ri=500", r.stdout)
        self.assertIn("HOST_ROLE='dedicated' is not", r.stdout)

    def test_the_pin_notice_is_silent_on_a_dedicated_host(self) -> None:
        r = self.bash("_mdt_io_pin_notice", "HOST_ROLE=dedicated-dev")
        self.assertEqual(r.stdout.strip(), "")

    def test_reconcile_refuses_to_apply_unresolved_dedicated_limits(self) -> None:
        text = (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text()
        guard = text.index("MDT_DEDICATED_UNRESOLVED")
        self.assertLess(text.index("_mdt_resolve_dev_io_caps || MDT_RC=1"), guard)
        self.assertLess(guard, text.index("_mdt_io_props"))
        install = (ROOT / "install.sh").read_text()
        self.assertIn("mdt_iobench_caps.py", install)


class WizardAndExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("wiz_testhost", ROOT / "mdt-host-setup-wizard.py")
        cls.wiz = importlib.util.module_from_spec(spec)
        sys.modules["wiz_testhost"] = cls.wiz
        spec.loader.exec_module(cls.wiz)

    def test_role_validator(self) -> None:
        for ok in ("", "shared-prod", "dedicated-dev"):
            self.assertIsNone(self.wiz.validate_host_role(ok))
        self.assertIn("not a host role", self.wiz.validate_host_role("dedicated"))

    def test_example_declares_the_role_empty_and_runtime_optional(self) -> None:
        example = (ROOT / "host-setup.env.example").read_text()
        self.assertIn("\nHOST_ROLE=\n", example)
        self.assertIn("# Allowed HOST_ROLE:", example)
        self.assertIn("HOST_ROLE|", (ROOT / "runtime-optional-keys.re").read_text())

    def test_measured_caps_loader_reports_why_when_unusable(self) -> None:
        values, notes, why = self.wiz.load_measured_caps(Path("/nonexistent/result.json"), runner=lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
        self.assertEqual((values, notes), ({}, {}))
        self.assertTrue(why)


if __name__ == "__main__":
    unittest.main()
