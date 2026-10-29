"""Run tnt's real Textual app on a loopback FakeServer inside a tmux pane."""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

# The gate normally provides src/ through PYTHONPATH. Keep this executable
# helper self-contained if tmux starts it from a different environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tnt.profile import ConfigPaths
from tnt.session import open_session
from tnt.testing import FakeServer
from tnt.tui.app import TntApp


HOST_NAME = "qa-archive-host-eu-west-production-2026-cluster"


def _run(scratch: Path) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    with FakeServer() as server:
        profile = server.profile(name=HOST_NAME)
        session = open_session(profile, secret=server.secret(), allow_loopback_ws=True)
        app = TntApp(
            session,
            host_paths=ConfigPaths(scratch / "tnt.toml", scratch / "tnt.secrets.toml"),
        )
        try:
            app.run()
        finally:
            app.session_controller.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    args = parser.parse_args()
    status = 1
    try:
        _run(args.scratch)
    except BaseException:
        traceback.print_exc()
    else:
        status = 0
    finally:
        args.status.write_text(f"{status}\n", encoding="ascii")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
