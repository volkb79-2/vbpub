"""get.py emitter — render templates/get.py.tmpl for a project.

CLI: cmru get-py --project <name> [--config <toml>] [--output <file>]

The emitted get.py is a self-contained Python 3 transactional installer that handles
install, update, rollback, status, scope (system/user), bundled-wheel venv, SHA256 +
minisign-manifest verification, private GitHub asset auth, and the project-adapter
invocation contract (Seam 1). It ships INSIDE the release artifact.

Template variables use [[VARNAME]] syntax. All placeholders must be replaced;
unmatched [[...]] keys trigger a warning.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple


_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "get.py.tmpl"


def _py_str_list(items: List[str]) -> str:
    """Render a Python list-of-strings literal."""
    if not items:
        return "[]"
    inner = ", ".join(f'"{s}"' for s in items)
    return f"[{inner}]"


def _py_wheel_specs(wheel_specs: List[Tuple[str, str]]) -> str:
    """Render WHEEL_SPECS as a Python list-of-tuple literal."""
    if not wheel_specs:
        return "[]"
    parts = [f'("{glob}", "{dist}")' for glob, dist in wheel_specs]
    return "[" + ", ".join(parts) + "]"


def render_get_py(
    *,
    project_name: str,
    repo_owner: str,
    repo_name: str,
    tag_prefix: str,
    asset_suffix: str = ".tar.xz",
    install_dir_system: str,
    install_dir_user: str,
    entrypoint: str = "",
    required_commands: Optional[List[str]] = None,
    preserve_paths: Optional[List[str]] = None,
    wheel_specs: Optional[List[Tuple[str, str]]] = None,
    manifest_name: str = "manifest.json",
    signature_name: str = "manifest.json.minisig",
    template_path: Path = _TEMPLATE_PATH,
) -> str:
    """Render the get.py template for a project.

    All [[VARNAME]] placeholders are replaced with the provided values.
    Returns the rendered script as a string. Emits a warning for any
    unreplaced [[...]] placeholders.
    """
    template = template_path.read_text(encoding="utf-8")

    cmds = required_commands or []
    preserve = preserve_paths or []
    wheels = wheel_specs or []

    # Comment string for the docstring header: ", cmd1, cmd2"
    if cmds:
        required_commands_comment = ", " + ", ".join(cmds)
    else:
        required_commands_comment = ""

    replacements = {
        "[[PROJECT_NAME]]":              project_name,
        "[[REPO_OWNER]]":                repo_owner,
        "[[REPO_NAME]]":                 repo_name,
        "[[TAG_PREFIX]]":                tag_prefix,
        "[[ASSET_SUFFIX]]":              asset_suffix,
        "[[INSTALL_DIR_SYSTEM]]":        install_dir_system,
        "[[INSTALL_DIR_USER]]":          install_dir_user,
        "[[ENTRYPOINT]]":                entrypoint,
        "[[REQUIRED_COMMANDS_LIST]]":    _py_str_list(cmds),
        "[[REQUIRED_COMMANDS_STR]]":     ", ".join(cmds) if cmds else "(none)",
        "[[REQUIRED_COMMANDS_COMMENT]]": required_commands_comment,
        "[[PRESERVE_PATHS_LIST]]":       _py_str_list(preserve),
        "[[WHEEL_SPECS_LIST]]":          _py_wheel_specs(wheels),
        "[[MANIFEST_NAME]]":             manifest_name,
        "[[SIGNATURE_NAME]]":            signature_name,
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    remaining = re.findall(r"\[\[[A-Z_]+\]\]", result)
    if remaining:
        unique = sorted(set(remaining))
        print(f"[WARN] get.py.tmpl: unreplaced placeholders: {unique}", file=sys.stderr)

    return result


def render_from_config(project_name: str, config_path: Path) -> str:
    """Render get.py for a project using cmru.toml config (reads [installer] section)."""
    from cmru.config import load_forge_config
    config = load_forge_config(config_path)
    proj = config.projects.get(project_name)
    if not proj:
        raise ValueError(f"Project '{project_name}' not found in config")
    if not proj.installer:
        raise ValueError(
            f"Project '{project_name}' has no [project.{project_name}.installer] section"
        )

    ins = proj.installer
    wheel_specs: List[Tuple[str, str]] = [
        (w.path, w.distribution) for w in ins.wheels
    ]

    return render_get_py(
        project_name=project_name,
        repo_owner=config.github.owner,
        repo_name=config.github.repo,
        tag_prefix=proj.prefix,
        asset_suffix=ins.asset_suffix,
        install_dir_system=ins.install_dir_system,
        install_dir_user=ins.install_dir_user,
        entrypoint=ins.entrypoint or "",
        required_commands=ins.required_commands or None,
        preserve_paths=ins.preserve or None,
        wheel_specs=wheel_specs or None,
        manifest_name=ins.manifest_name,
        signature_name=ins.signature_name,
    )


def getpy_main(argv: Optional[list] = None) -> None:
    """Entry point for ``cmru get-py``."""
    import argparse
    parser = argparse.ArgumentParser(description="Emit get.py for a project")
    parser.add_argument("--project", required=True)
    parser.add_argument("--config", help="Path to cmru.toml")
    parser.add_argument("--output", help="Write to file instead of stdout")
    args = parser.parse_args(argv)

    if not args.config:
        print("[ERROR] --config is required for cmru get-py", file=sys.stderr)
        sys.exit(2)

    script = render_from_config(args.project, Path(args.config).expanduser().resolve())

    if args.output:
        out = Path(args.output)
        out.write_text(script, encoding="utf-8")
        out.chmod(0o755)
        print(f"[INFO] Written to {out}")
    else:
        sys.stdout.write(script)
