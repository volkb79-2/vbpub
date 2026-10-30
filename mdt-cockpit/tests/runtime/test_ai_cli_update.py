"""Update paths of the AI CLIs that can be exercised offline (R14, R15).

`claude update` has its own scenarios (test_remapped_uid.py). Here: opencode.

`opencode upgrade` cannot be used as shipped. Its platform package (`opencode-linux-*`,
installed directly because the `opencode-ai` meta-package can pick an incompatible libc
variant) is not the `opencode-ai` package it looks for, so it cannot tell how it was
installed and asks "Install anyways?", waiting for a terminal; and `opencode upgrade -m npm`
installs the meta-package, which npm 11 refuses to finish (measured: "Upgrade failed for npm
(exit code 1)"). The documented update is therefore the one the image itself uses:
`npm install -g <platform package>@latest`, run here offline as a same-version reinstall from
the package's own files.
"""
from __future__ import annotations

import unittest

import harness

OFFLINE_REINSTALL = r"""
set -eu
export HOME=/home/vscode CI=1 npm_config_registry=http://127.0.0.1:9 npm_config_fetch_retries=0
package=$(basename "$(readlink -f "$(command -v opencode)" | xargs dirname | xargs dirname)")
before=$(opencode --version)
rm -rf /tmp/oc /tmp/pk && mkdir /tmp/pk && cp -a "/opt/mdt/npm/lib/node_modules/$package" /tmp/oc
(cd /tmp/pk && npm pack /tmp/oc --silent --offline --ignore-scripts >/dev/null)
npm install -g --offline --ignore-scripts --no-audit --no-fund --loglevel=error /tmp/pk/*.tgz >/dev/null
echo "package=$package"
echo "before=$before"
echo "after=$(opencode --version)"
echo "resolved=$(command -v opencode)"
echo "group=$(stat -c %G "/opt/mdt/npm/lib/node_modules/$package")"
"""


class Contract:
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.derived = harness.built_image(
            (harness.TESTS_DIR / "updateUID.Dockerfile").read_text(encoding="utf-8"),
            build_args={"BASE_IMAGE": cls.image.ref, "REMOTE_USER": "vscode", "NEW_UID": "4242",
                        "NEW_GID": "4242", "IMAGE_USER": "vscode"},
            label=f"{cls.family}-ocuid",
        )
        cls.remapped = cls.derived.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.derived.__exit__(None, None, None)
        super().tearDownClass()

    def test_opencode_updates_in_place_through_its_platform_package(self) -> None:
        result = harness.sh(self.remapped, OFFLINE_REINSTALL)
        self.assertOk(result, "offline reinstall of the opencode platform package")
        lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self.assertRegex(lines["package"], r"^opencode-linux-")
        self.assertEqual(lines["before"], lines["after"])
        self.assertEqual(lines["resolved"], "/opt/mdt/npm/bin/opencode")
        self.assertEqual(lines["group"], "mdt")


class NativeContract(Contract, harness.NativeCase):
    pass


class VscContract(Contract, harness.VscCase):
    pass


if __name__ == "__main__":
    unittest.main()
