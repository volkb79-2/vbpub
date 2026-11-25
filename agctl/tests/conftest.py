"""Test services for agctl: an installed-distribution stand-in, and nothing else.

``agctl.cli.identity`` reads the installed ``agctl`` distribution and, by design, never invents
a literal version, so a source-mode gate run needs the same stand-in a real install provides.
This publishes a minimal ``.dist-info`` (the pattern fleetd's conftest established) instead of
weakening the refusal: a source tree that was never built or installed still refuses loudly.
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

WORKING_VERSION = "0.1.0"


def _publish_metadata() -> None:
    try:
        version("agctl")
    except PackageNotFoundError:
        pass
    else:
        return
    root = Path(tempfile.mkdtemp(prefix="agctl-test-dist-"))
    info = root / f"agctl-{WORKING_VERSION}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: agctl\nVersion: {WORKING_VERSION}\n", encoding="utf-8"
    )
    (info / "RECORD").write_text("", encoding="utf-8")
    sys.path.insert(0, str(root))
    atexit.register(shutil.rmtree, root, True)


_publish_metadata()
