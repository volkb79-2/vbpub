#!/usr/bin/env python3
import runpy
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir))

# cli-extended comes from exactly one place. bootstrap-remote.py downloads the
# released, sha256-verified wheel beside this script, and a pure-Python wheel
# is importable from sys.path (zipimport), so the target needs no pip. With no
# wheel here, an installed cli_extended is used. There is deliberately no
# repository-source fallback: the library revision that runs is a released one.
wheels = sorted(script_dir.glob("cli_extended-*.whl"))
if len(wheels) > 1:
    print(
        "[ERROR] debian-install-v2: more than one cli-extended wheel beside the "
        "entrypoint; keep exactly one: " + ", ".join(wheel.name for wheel in wheels),
        file=sys.stderr,
    )
    raise SystemExit(2)
if wheels:
    sys.path.insert(0, str(wheels[0]))

try:
    import cli_extended  # noqa: F401
except ImportError:
    print(
        "[ERROR] debian-install-v2: cli-extended is not installed; run via "
        "bootstrap-remote.py or install the cli-extended wheel",
        file=sys.stderr,
    )
    raise SystemExit(2)

runpy.run_module("debian_install_v2.bootstrap", run_name="__main__")
