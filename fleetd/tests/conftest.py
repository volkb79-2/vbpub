"""Test-run environment for fleetd: give a source-mode run the metadata the CLI resolves its version from.

``fleetd.cli`` resolves the CLI identity with ``CliIdentity.resolve``, which
reads the installed ``fleetd`` distribution (and by design never invents a
literal fallback). The registered gate runs the tests from ``src`` without
installing fleetd (the wheel would need its own cli-extended dependency), so
without this the CLI refuses to start with exit 3 and every command test fails
for an environment reason instead of a product one.

A minimal ``.dist-info`` in a throwaway directory on ``sys.path`` -- and on
``PYTHONPATH``, so a ``fleet`` subprocess a test spawns resolves the same
version -- stands in for the install. A real install (or an editable one) is
left alone: this must never shadow an authoritative version source.

The stand-in version is the package's own ``0.1.0`` working version, so a
source-run reports exactly what the installed wheel would for an unreleased
tree. It is deliberately NOT read from a git tag: the gate's checkout may have
none.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

WORKING_VERSION = "0.1.0"


def _publish_fleetd_metadata() -> None:
    try:
        version("fleetd")
    except PackageNotFoundError:
        pass
    else:
        return
    root = Path(tempfile.mkdtemp(prefix="fleetd-test-dist-"))
    info = root / f"fleetd-{WORKING_VERSION}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: fleetd\nVersion: {WORKING_VERSION}\n",
        encoding="utf-8",
    )
    (info / "RECORD").write_text("", encoding="utf-8")
    sys.path.insert(0, str(root))
    entries = [str(root)]
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        entries.append(inherited)
    os.environ["PYTHONPATH"] = os.pathsep.join(entries)
    atexit.register(shutil.rmtree, root, True)


_publish_fleetd_metadata()
