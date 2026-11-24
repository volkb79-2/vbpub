"""The CIU hook must prefer checkout source over an installed tls_edge_cli."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from _render import ROOT, copy_tracked_stack


def test_hook_imports_checkout_source_before_pythonpath(tmp_path) -> None:
    checkout = tmp_path / "checkout"
    stack = copy_tracked_stack(checkout)
    (checkout / "src").mkdir()
    shutil.copytree(ROOT / "src" / "tls_edge_cli", checkout / "src" / "tls_edge_cli")

    decoy = tmp_path / "installed" / "tls_edge_cli"
    decoy.mkdir(parents=True)
    (decoy / "__init__.py").write_text("", encoding="utf-8")
    (decoy / "config.py").write_text(
        "class ConfigError(ValueError): pass\n"
        "def load_site(_path): return {}, {}, {'tls_edge': {}}\n"
        "def migrate(overlay): return overlay\n"
        "def check_override(*_args): raise ConfigError('decoy imported')\n"
        "def validate_config(*_args, **_kwargs): raise ConfigError('decoy imported')\n",
        encoding="utf-8",
    )

    script = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("validate_edge_hook", sys.argv[1])
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
module_path = pathlib.Path(sys.modules["tls_edge_cli.config"].__file__).resolve()
checkout_src = pathlib.Path(sys.argv[2]).resolve()
assert module_path.is_relative_to(checkout_src), (module_path, checkout_src)
print("ok")
"""
    environ = dict(os.environ)
    environ["PYTHONPATH"] = str(decoy.parent)
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(stack / "hooks" / "validate_edge.py"),
            str(checkout / "src"),
        ],
        capture_output=True,
        text=True,
        env=environ,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok", result.stdout + result.stderr
