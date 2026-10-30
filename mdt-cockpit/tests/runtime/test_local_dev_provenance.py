"""11g - local-dev provenance: what the image says it contains is what it contains.

Applies to images built with `build-push.py --build --local-dev`. A release image
(`net.volkb79.mdt.build-mode` = `release` or absent) has no local artifacts, so these
tests are skipped for it with that reason - never silently passed.

For a local-dev image: the labels list the local artifacts, the image manifest repeats
them, the wheel versions and the PWMCP release actually installed equal the recorded
versions, and the labels equal - entry for entry - the manifest named by
`MDT_RUNTIME_LOCAL_DEV_MANIFEST`. That variable is REQUIRED for a local-dev image (its
absence fails the lane) and unused for a release image. That an image is a release image is
itself asserted (a present, valid label) by test_image_contract, so a missing label can
never turn these tests off.
"""
from __future__ import annotations

import json
import os
import tomllib
import unittest
from pathlib import Path

import harness

PWMCP_NAMES = ("pwmcp-installer", "pwmcp-bundle")


class Contract:
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.local_dev = cls.image.build_mode == "local-dev"
        cls.artifacts: list[dict] = []
        if cls.local_dev:
            cls.artifacts = json.loads(cls.image.labels.get(harness.LOCAL_ARTIFACTS_LABEL) or "null") or []

    def setUp(self) -> None:
        if not self.local_dev:
            self.skipTest(
                f"{self.image.ref} is a {self.image.build_mode!r} image: it carries no local artifacts"
            )

    def test_labels_list_every_local_artifact_with_the_provenance_fields(self) -> None:
        self.assertTrue(self.artifacts, "a local-dev image must record at least one local artifact")
        for entry in self.artifacts:
            self.assertEqual(sorted(entry), ["name", "path", "sha256", "version"])
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(os.path.isabs(entry["path"]))

    def test_labels_equal_the_manifest_the_image_was_built_from(self) -> None:
        manifest = os.environ.get(harness.ENV_LOCAL_MANIFEST, "").strip()
        if not manifest:
            # A local-dev image's labels are only evidence against an independent record, so
            # without the manifest this check cannot pass - and must not be skipped.
            self.fail(
                f"{harness.ENV_LOCAL_MANIFEST} is not set. A local-dev image is only verified "
                "against the manifest it was built from: run the lane with "
                f"{harness.ENV_LOCAL_MANIFEST}=$PWD/local-dev-artifacts.toml (the gitignored file "
                "passed to `build-push.py --build --local-dev`)."
            )
        document = tomllib.loads(Path(manifest).read_text(encoding="utf-8"))
        expected = {item["name"]: item for item in document["artifact"]}
        recorded = {item["name"]: item for item in self.artifacts}
        self.assertEqual(sorted(recorded), sorted(expected))
        for name, item in expected.items():
            with self.subTest(artifact=name):
                self.assertEqual(
                    {key: recorded[name][key] for key in ("version", "sha256", "path")},
                    {key: item[key] for key in ("version", "sha256", "path")},
                )

    def test_installed_wheel_versions_equal_the_recorded_versions(self) -> None:
        wheels = [item for item in self.artifacts if item["name"] not in PWMCP_NAMES]
        if not wheels:
            self.skipTest("no local wheel recorded")
        script = "; ".join(
            f"printf '%s|%s\\n' '{item['name']}' \"$(/opt/mdt/venv/bin/python -c "
            f"'import importlib.metadata as m; print(m.version(\"{item['name']}\"))')\""
            for item in wheels
        )
        result = self.sh(script)
        self.assertOk(result)
        installed = dict(line.split("|", 1) for line in result.stdout.splitlines() if "|" in line)
        for item in wheels:
            with self.subTest(wheel=item["name"]):
                self.assertEqual(installed.get(item["name"]), item["version"])

    def test_the_installed_wheel_files_are_the_recorded_content_not_just_the_recorded_version(self) -> None:
        """A stale wheel of the SAME version passes a version comparison. Compare content:
        the sha256 of each wheel file the build installed (first-party-wheels.json, written
        by the build from the very files pip received) must equal the sha256 the label
        records, and the files pip unpacked must still match the wheel's own RECORD."""
        wheels = [item for item in self.artifacts if item["name"] not in PWMCP_NAMES]
        if not wheels:
            self.skipTest("no local wheel recorded")
        result = self.sh("cat /usr/local/share/mdt-cockpit/first-party-wheels.json")
        self.assertOk(result)
        installed = json.loads(result.stdout)
        self.assertEqual(installed["build_mode"], "local-dev")
        by_name = {item["name"]: item for item in installed["wheels"]}
        for item in wheels:
            with self.subTest(wheel=item["name"]):
                entry = by_name.get(item["name"].replace("_", "-"))
                self.assertIsNotNone(entry, f"{item['name']} missing from first-party-wheels.json")
                self.assertEqual(entry["source"], "local-dev")
                self.assertEqual(entry["sha256"], item["sha256"], "installed wheel content differs from the label")
        verify = (
            "import hashlib, base64, importlib.metadata as m, sys\n"
            "bad = []\n"
            "for name in sys.argv[1:]:\n"
            "    dist = m.distribution(name)\n"
            "    for f in dist.files or []:\n"
            "        if f.hash is None: continue\n"
            "        data = f.locate().read_bytes()\n"
            "        digest = base64.urlsafe_b64encode(hashlib.new(f.hash.mode, data).digest()).rstrip(b'=').decode()\n"
            "        if digest != f.hash.value: bad.append(f'{name}:{f}')\n"
            "print('RECORD-MISMATCH' if bad else 'RECORD-OK', *bad)\n"
        )
        names = " ".join(f"'{item['name']}'" for item in wheels)
        check = self.sh(f"/opt/mdt/venv/bin/python -c \"$VERIFY\" {names}", env={"VERIFY": verify})
        self.assertOk(check)
        self.assertEqual(check.stdout.strip(), "RECORD-OK", check.output)

    def test_installed_pwmcp_release_equals_the_recorded_version(self) -> None:
        pwmcp = {item["name"]: item for item in self.artifacts if item["name"] in PWMCP_NAMES}
        if "pwmcp-bundle" not in pwmcp:
            self.skipTest("no local PWMCP bundle recorded")
        version = pwmcp["pwmcp-bundle"]["version"]
        result = self.sh(
            "python -c \"import json; s=json.load(open('/srv/pwmcp-instance/state.json')); "
            "print(s['current']['tag'])\"; readlink /srv/pwmcp-instance/current"
        )
        self.assertOk(result)
        tag, current = result.stdout.splitlines()
        self.assertEqual(tag, f"pwmcp-v{version}")
        self.assertIn(f"pwmcp-v{version}", current)

    def test_the_image_manifest_records_the_build_mode_and_every_hash(self) -> None:
        result = self.sh("cat /usr/local/share/mdt-cockpit/manifest.md")
        self.assertOk(result)
        self.assertIn("Build mode: `local-dev`", result.stdout)
        for item in self.artifacts:
            with self.subTest(artifact=item["name"]):
                self.assertIn(item["sha256"], result.stdout)
                self.assertIn(item["version"], result.stdout)


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
