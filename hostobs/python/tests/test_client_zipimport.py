#!/usr/bin/env python3
"""zipimport smoke test: `hostobs_client` imported from a zip decodes a block and talks over a socketpair.

A subprocess (`python3 -I`, `sys.path` = the zip only) proves that no module needs `__file__` or a
real directory. The frames it consumes are built here with the reference encoder.

    python3 -I -m unittest discover -s python/tests -p 'test_client_zipimport.py' -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_probe import block_frame, hello_result, json_bytes, raw_frame  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1] / "hostobs_client"

CHILD = r"""
import json, socket, struct, sys
sys.path.insert(0, sys.argv[1])  # -I: nothing else of this repository is on the path
import hostobs_client
from hostobs_client import Connection, decode_block, require_profiling
from hostobs_client.mark import build_args

loader = type(hostobs_client.__spec__.loader).__name__
block = decode_block(bytearray(bytes.fromhex(sys.argv[2])), None)
column = block.column("cpu.usage_usec")
a, b = socket.socketpair()
b.sendall(bytes.fromhex(sys.argv[3]))
conn = Connection(a, "observe", client="zip-smoke", timeout_s=5)
result = conn.request("status", {})
conn.close()
b.close()
state = require_profiling("observe", False, environ={})
print(json.dumps({
    "loader": loader,
    "has_file": hasattr(hostobs_client, "__file__") and ".zip" in str(hostobs_client.__file__),
    "n_rows": block.header.n_rows,
    "first": column.measured(0),
    "epoch": conn.epoch,
    "result": result,
    "state": state.name,
    "mark_keys": sorted(build_args("point", "x")),
}))
"""


class ZipImport(unittest.TestCase):
    def test_package_imports_from_a_zip_and_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "hostobs_client.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for path in sorted(PACKAGE.glob("*.py")):
                    zf.write(path, f"hostobs_client/{path.name}")
            script = (
                b"HOB\x01\x01\x00\x02\x00"
                + raw_frame(2, 1, json_bytes({"ok": True, "result": hello_result(epoch=7)}))
                + raw_frame(2, 2, json_bytes({"ok": True, "result": {"up": True}}))
            )
            run = subprocess.run(
                [sys.executable, "-I", "-c", CHILD, str(archive), block_frame(1, True).hex(), script.hex()],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            seen = json.loads(run.stdout)
        self.assertEqual(seen["loader"], "zipimporter")
        self.assertTrue(seen["has_file"], "the package really came from the zip")
        self.assertEqual(
            {k: seen[k] for k in ("n_rows", "first", "epoch", "result", "state")},
            {"n_rows": 3, "first": 1_000_000_000, "epoch": 7, "result": {"up": True}, "state": "unconfigured"},
        )
        self.assertIn("kind", seen["mark_keys"])


if __name__ == "__main__":
    unittest.main()
