"""get.py emitter — render templates/get.py.tmpl for a project.

CLI: cmru get-py [all|project[,project...]] [--config <toml>] [--output <file>]

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
from typing import Dict, List, Optional, Tuple

from cmru.config_names import ORCHESTRATION_CONFIG_FILENAME, PROJECT_CONFIG_FILENAME


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


def _py_lit(value: Optional[str]) -> str:
    """Render a Python string literal (or ``None``) with the essentials escaped."""
    if value is None:
        return "None"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _py_variants(variants: List[Dict[str, Optional[str]]]) -> str:
    """Render VARIANTS as a Python list-of-dict literal ([{"name","label"}, ...])."""
    if not variants:
        return "[]"
    parts = [
        '{"name": %s, "label": %s}' % (_py_lit(v["name"]), _py_lit(v.get("label")))
        for v in variants
    ]
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
    variants: Optional[List[Dict[str, Optional[str]]]] = None,
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
    variant_list = variants or []

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
        "[[VARIANTS_LIST]]":             _py_variants(variant_list),
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
    variants: List[Dict[str, Optional[str]]] = [
        {"name": v.name, "label": v.label} for v in proj.variants
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
        variants=variants or None,
    )


def getpy_main(argv: Optional[list] = None) -> None:
    """Entry point for ``cmru get-py``."""
    from cmru.cli_support import CMRUArgumentParser, TargetSelectionError, select_target_names
    parser = CMRUArgumentParser(description="Emit get.py for registered projects")
    parser.add_argument(
        "target", nargs="?", metavar="[all|PROJECT[,PROJECT...]]",
        help="Project target; omitted uses the current project or estate default",
    )
    parser.add_argument(
        "--config", help=f"Path to {PROJECT_CONFIG_FILENAME} or {ORCHESTRATION_CONFIG_FILENAME}"
    )
    parser.add_argument("--output", help="Write to file instead of stdout")
    parser.add_argument("--output-dir", help="Write one named installer per selected project")
    args = parser.parse_args(argv)
    from cmru.cli import _resolve_config, load_config
    from cmru.config import resolve_invocation_context

    cfg_path = _resolve_config(args.config)
    loaded = load_config(cfg_path)
    configs, project_order = loaded[1], loaded[2]
    if args.target is None and cfg_path.name == PROJECT_CONFIG_FILENAME and len(configs) == 1:
        context_project = next(iter(configs))
        estate_scope = False
    elif args.target is None:
        context = resolve_invocation_context(cfg_path)
        context_project = context.project_name
        estate_scope = context.scope == "estate"
    else:
        context_project = None
        estate_scope = False
    try:
        names = select_target_names(
            args.target, configs, project_order,
            context_project=context_project,
            estate_scope=estate_scope,
        )
    except TargetSelectionError as exc:
        parser.error(str(exc))
    if args.output and len(names) != 1:
        parser.error("--output FILE is only valid for one project; use --output-dir for multiple projects")
    if args.output and args.output_dir:
        parser.error("--output and --output-dir are mutually exclusive")
    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            out = output_dir / f"{name}-get.py"
            out.write_text(render_from_config(name, cfg_path), encoding="utf-8")
            out.chmod(0o755)
            print(f"[INFO] Written to {out}")
        return
    scripts = {name: render_from_config(name, cfg_path) for name in names}
    if args.output:
        out = Path(args.output)
        out.write_text(next(iter(scripts.values())), encoding="utf-8")
        out.chmod(0o755)
        print(f"[INFO] Written to {out}")
    elif len(scripts) == 1:
        sys.stdout.write(next(iter(scripts.values())))
    else:
        for name, script in scripts.items():
            print(f"===== get.py Project: {name.upper()} =====")
            sys.stdout.write(script)
            if not script.endswith("\n"):
                sys.stdout.write("\n")
