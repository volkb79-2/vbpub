"""11c - the runtime user is NOT uid 1000 (devcontainer reality).

Dev Containers' `updateRemoteUserUID` re-numbers `vscode` to the host user's uid and
re-owns only $HOME, so anything the build owned by uid 1000 outside the home is not
writable at runtime. This test derives a throw-away image with the SAME step
(`updateUID.Dockerfile`, mirrored from devcontainers/cli v0.89.0 - see its header)
for uid/gid 4242, then, as that user and without sudo:

* installs a LOCAL npm tarball (made by `npm pack` here, no registry) into /opt/mdt/npm,
* installs a locally built wheel into the primary venv (`pip --no-index`),
* creates a file in /srv/pwmcp-instance.

Negative control: the same writes as a uid that is NOT in group `mdt` fail with
EACCES - proving the group, not a loosened mode, grants the access.
"""
from __future__ import annotations

import base64
import hashlib
import io
import unittest
import zipfile

import harness

REMAP_UID = 4242
OUTSIDER = "4343:4343"  # no passwd entry, no mdt membership

NPM_PROBE = r"""
set -eu
mkdir -p /tmp/probe/package/bin
cd /tmp/probe/package
printf '%s\n' '{"name":"mdt-rt-probe","version":"1.0.0","bin":{"mdt-rt-probe":"bin/cli.js"}}' > package.json
printf '%s\n' '#!/usr/bin/env node' 'console.log("npm-probe-ok")' > bin/cli.js
chmod +x bin/cli.js
npm pack --silent --offline >/dev/null
npm install -g --offline --no-audit --no-fund --loglevel=error ./mdt-rt-probe-1.0.0.tgz
echo "resolved=$(command -v mdt-rt-probe)"
mdt-rt-probe
echo "group=$(stat -c %G /opt/mdt/npm/lib/node_modules/mdt-rt-probe)"
"""


def tiny_wheel() -> bytes:
    """A valid pure-python wheel built from stdlib only (no build backend, no network)."""
    files = {
        "mdt_rt_probe/__init__.py": b'VALUE = "wheel-ok"\n',
        "mdt_rt_probe-1.0.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: mdt-rt-probe\nVersion: 1.0.0\n",
        "mdt_rt_probe-1.0.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: mdt-cockpit-runtime-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append("mdt_rt_probe-1.0.0.dist-info/RECORD,,")
    files["mdt_rt_probe-1.0.0.dist-info/RECORD"] = ("\n".join(record) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


PIP_PROBE = (
    "set -eu; cat > /tmp/mdt_rt_probe-1.0.0-py3-none-any.whl; "
    "/opt/mdt/venv/bin/python -m pip install --no-index --no-deps --disable-pip-version-check "
    "--quiet /tmp/mdt_rt_probe-1.0.0-py3-none-any.whl; "
    "/opt/mdt/venv/bin/python -c 'import mdt_rt_probe; print(mdt_rt_probe.VALUE)'"
)


class Contract:
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        dockerfile = (harness.TESTS_DIR / "updateUID.Dockerfile").read_text(encoding="utf-8")
        cls._derived = harness.built_image(
            dockerfile,
            build_args={
                "BASE_IMAGE": cls.image.ref,
                "REMOTE_USER": "vscode",
                "NEW_UID": str(REMAP_UID),
                "NEW_GID": str(REMAP_UID),
                "IMAGE_USER": "vscode",
            },
            label=f"{cls.family}-uid",
        )
        cls.remapped = cls._derived.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._derived.__exit__(None, None, None)
        super().tearDownClass()

    def test_the_remap_took_effect_and_group_membership_survived_it(self) -> None:
        result = harness.sh(
            self.remapped,
            "id -u; id -g; id -nG; stat -c '%u:%g' /opt/mdt /opt/mdt/npm /srv /srv/pwmcp-instance",
        )
        self.assertOk(result)
        uid, gid, groups, *owners = result.stdout.splitlines()
        self.assertEqual((uid, gid), (str(REMAP_UID), str(REMAP_UID)))
        self.assertIn("mdt", groups.split(), "the group is what survives the remap")
        for owner in owners:
            self.assertNotEqual(owner.split(":")[0], uid, "ownership by the runtime uid would hide the point of the test")

    def test_npm_installs_a_local_tarball_into_the_mdt_prefix_without_sudo(self) -> None:
        result = harness.sh(self.remapped, NPM_PROBE)
        self.assertOk(result, "npm pack + npm install -g")
        lines = result.stdout.splitlines()
        self.assertIn("resolved=/opt/mdt/npm/bin/mdt-rt-probe", lines)
        self.assertIn("npm-probe-ok", lines)
        self.assertIn("group=mdt", lines, "setgid directories keep new entries in the group")

    def test_pip_installs_a_local_wheel_into_the_primary_venv_without_sudo(self) -> None:
        result = harness.sh(self.remapped, PIP_PROBE, stdin=tiny_wheel())
        self.assertOk(result, "pip install --no-index")
        self.assertEqual(result.stdout.strip().splitlines()[-1], "wheel-ok")

    def test_a_file_can_be_created_in_the_pwmcp_instance(self) -> None:
        result = harness.sh(
            self.remapped,
            "set -eu; touch /srv/pwmcp-instance/mdt-rt-write-test; ls -l /srv/pwmcp-instance/mdt-rt-write-test",
        )
        self.assertOk(result)

    def test_claude_updates_in_place_through_group_mdt_and_stays_the_only_claude(self) -> None:
        """What `claude update` does for an npm-global install, offline: re-install the same
        version from the package's own files over the existing one, as the remapped uid."""
        script = r"""
set -eu
export HOME=/home/vscode CI=1 npm_config_registry=http://127.0.0.1:9 npm_config_fetch_retries=0
before=$(claude --version | head -1)
rm -rf /tmp/cc /tmp/pk && mkdir /tmp/pk && cp -a /opt/mdt/npm/lib/node_modules/@anthropic-ai/claude-code /tmp/cc
(cd /tmp/pk && npm pack /tmp/cc --silent --offline --ignore-scripts >/dev/null)
npm install -g --offline --ignore-scripts --omit=optional --no-audit --no-fund --loglevel=error /tmp/pk/*.tgz >/dev/null
echo "before=$before"
echo "after=$(claude --version | head -1)"
echo "resolved=$(command -v claude)"
echo "all=$(which -a claude | tr '\n' ' ')"
echo "group=$(stat -c %G /opt/mdt/npm/lib/node_modules/@anthropic-ai/claude-code)"
echo "home=$(ls -A ~ | tr '\n' ' ')"
"""
        result = harness.sh(self.remapped, script)
        self.assertOk(result, "offline same-version reinstall of claude")
        lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self.assertEqual(lines["before"], lines["after"])
        self.assertEqual(lines["resolved"], "/opt/mdt/npm/bin/claude")
        self.assertEqual(lines["all"].split(), ["/opt/mdt/npm/bin/claude"], "no shadow copy appeared")
        self.assertEqual(lines["group"], "mdt")
        self.assertNotIn(".local", lines["home"].split(), "the update must not write a native install into the home")

    def test_negative_control_a_uid_outside_mdt_cannot_touch_the_claude_install(self) -> None:
        result = harness.sh(
            self.image.ref,
            "touch /opt/mdt/npm/lib/node_modules/@anthropic-ai/claude-code/mdt-rt-write-test",
            user=OUTSIDER, env={"HOME": "/tmp"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.output, r"Permission denied|EACCES")

    def test_negative_control_a_uid_outside_mdt_gets_eacces_on_the_same_writes(self) -> None:
        """Same three writes, uid not in `mdt`, on the unmodified candidate image."""
        env = {"HOME": "/tmp", "npm_config_cache": "/tmp/npmcache"}
        attempts = {
            "npm": NPM_PROBE,
            "pip": PIP_PROBE,
            "srv": "set -eu; touch /srv/pwmcp-instance/mdt-rt-write-test",
        }
        for label, script in attempts.items():
            with self.subTest(write=label):
                result = harness.sh(
                    self.image.ref, script, user=OUTSIDER, env=env,
                    stdin=tiny_wheel() if label == "pip" else None,
                )
                self.assertNotEqual(result.returncode, 0, f"{label} must be refused:\n{result.output}")
                self.assertRegex(
                    result.output, r"EACCES|Permission denied|Errno 13|PermissionError",
                    f"{label} must fail on permissions, not for another reason:\n{result.output}",
                )


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
