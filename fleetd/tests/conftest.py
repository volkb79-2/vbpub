"""Test-run environment for fleetd: the CLI's own distribution metadata and the review plugin.

Two things a source-mode gate run needs and a real install provides:

* ``CliIdentity.resolve`` (``fleetd.cli.identity``) reads the installed ``fleetd``
  distribution and, by design, never invents a literal version. The registered
  gate runs the tests from ``src`` without installing the wheel, so this module
  publishes a minimal ``.dist-info`` — on ``sys.path`` and on ``PYTHONPATH``, so
  a ``fleet`` subprocess a test spawns resolves it too — as the stand-in for the
  install. A real install is never shadowed, and the package stores no literal:
  ``fleetd.__version__`` and the CLI both read the same resolver.
* The cli-extended pytest plugin links every reviewed catalog row
  (``docs/cli-review.toml``) to a collected, correctly marked test.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# The working version of an unreleased tree. Metadata and the CLI identity agree by
# construction because both read this same value; it is deliberately NOT derived from a
# git tag, because the gate's checkout may carry none.
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

# AC-22: with a review catalog configured, the cli-extended plugin fails collection unless
# every active reviewed case links a collected, correctly marked test. A focused local run of
# one test file needs `--cli-case-partial`; the gate runs the whole suite and stays strict.
pytest_plugins = ["cli_extended.pytest_plugin"]
