"""get.py emitter — render templates/get.py.tmpl for a project.

CLI: cmru get-py --project <name>

The emitted get.py is a self-contained Python 3 installer that handles install,
update, version pin, and config preservation. It ships INSIDE the release artifact
so 'project update' works out of the box without re-fetching from GitHub.

Template variables use [[VARNAME]] syntax.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional


_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "get.py.tmpl"


def _render_preserve_func(preserve_files: list[str] | None, env_prefix: str) -> str:
    if not preserve_files:
        return (
            "def preserve_config_files(project_home: Path) -> list:\n"
            "    return []\n"
            "\n"
            "\n"
            "def restore_config_files(project_home: Path, backed_up: list) -> None:\n"
            "    pass"
        )
    paths_repr = "\n".join(f'        "{f}",' for f in preserve_files)
    return (
        "def preserve_config_files(project_home: Path) -> list:\n"
        "    configs = [\n"
        f"{paths_repr}\n"
        "    ]\n"
        "    backed_up = []\n"
        "    for rel in configs:\n"
        "        cfg = project_home / rel\n"
        "        if cfg.exists():\n"
        "            bak = cfg.with_name(cfg.name + '.pre-update')\n"
        "            shutil.copy2(cfg, bak)\n"
        "            backed_up.append(cfg)\n"
        "            info(f'Backed up: {cfg.name} \\u2192 {bak.name}')\n"
        "    return backed_up\n"
        "\n"
        "\n"
        "def restore_config_files(project_home: Path, backed_up: list) -> None:\n"
        "    for cfg in backed_up:\n"
        "        bak = cfg.with_name(cfg.name + '.pre-update')\n"
        "        if not bak.exists():\n"
        "            continue\n"
        "        if not cfg.exists():\n"
        "            bak.rename(cfg)\n"
        "            info(f'Restored: {cfg.name}')\n"
        "        else:\n"
        "            bak.unlink()"
    )


def render_get_py(
    *,
    project_name: str,
    repo_owner: str,
    repo_name: str,
    tag_prefix: str,
    env_prefix: str,
    install_dir_default: str,
    subdir: str,
    wrapper_bin: str,
    wrapper_script_rel: str = "scripts/wrapper.sh",
    asset_suffix: str = ".tar.xz",
    required_deps: list[str] | None = None,
    preserve_files: list[str] | None = None,
    next_steps: list[str] | None = None,
    template_path: Path = _TEMPLATE_PATH,
) -> str:
    """Render the get.py template for a project.

    All [[VARNAME]] placeholders are replaced with the provided values.
    Returns the rendered script as a string.
    """
    template = template_path.read_text(encoding="utf-8")

    # Python list literal for required deps
    if required_deps:
        deps_list = "[" + ", ".join(f'"{d}"' for d in required_deps) + "]"
        deps_comment = f", {', '.join(required_deps)}"
    else:
        deps_list = "[]"
        deps_comment = ""

    # next_steps_msg — python print() calls indented 4 spaces
    if next_steps:
        steps_lines = "\n".join(f'    print("    {s}")' for s in next_steps)
    else:
        steps_lines = f'    print("    {project_name} --help")'

    preserve_func = _render_preserve_func(preserve_files, env_prefix)

    replacements = {
        "[[PROJECT_NAME]]":          project_name,
        "[[REPO_OWNER]]":            repo_owner,
        "[[REPO_NAME]]":             repo_name,
        "[[TAG_PREFIX]]":            tag_prefix,
        "[[ENV_PREFIX]]":            env_prefix,
        "[[INSTALL_DIR_DEFAULT]]":   install_dir_default,
        "[[SUBDIR]]":                subdir,
        "[[WRAPPER_BIN]]":           wrapper_bin,
        "[[WRAPPER_SCRIPT_REL]]":    wrapper_script_rel,
        "[[ASSET_SUFFIX]]":          asset_suffix,
        "[[REQUIRED_DEPS_LIST]]":    deps_list,
        "[[REQUIRED_DEPS_COMMENT]]": deps_comment,
        "[[PRESERVE_FILES_FUNC]]":   preserve_func,
        "[[NEXT_STEPS_MSG]]":        steps_lines,
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
    """Render get.py for a project using cmru.toml config."""
    from cmru.config import load_forge_config
    config = load_forge_config(config_path)
    proj = config.projects.get(project_name)
    if not proj:
        raise ValueError(f"Project '{project_name}' not found in config")
    if not proj.getsh:
        raise ValueError(
            f"Project '{project_name}' has no [project.{project_name}.getsh] section"
        )

    tag_prefix = proj.prefix
    env_prefix = tag_prefix.rstrip("-v").upper().replace("-", "_")
    subdir = project_name

    return render_get_py(
        project_name=project_name,
        repo_owner=config.github.owner,
        repo_name=config.github.repo,
        tag_prefix=tag_prefix,
        env_prefix=env_prefix,
        install_dir_default=proj.getsh.install_dir,
        subdir=subdir,
        wrapper_bin=f"/usr/local/bin/{project_name}",
        preserve_files=proj.getsh.preserve,
        required_deps=proj.getsh.deps or None,
        next_steps=proj.getsh.next_steps or None,
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
