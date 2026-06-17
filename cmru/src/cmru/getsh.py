"""get.sh emitter — render templates/get.sh.tmpl for a project (S6).

CLI: cmru get-sh --project <name>

The emitted get.sh is differentiator #4: a per-project bootstrap that handles
install, update, version pin, and config preservation.

Template variables use [[VARNAME]] syntax; rendering is simple str.replace().
See templates/get.sh.tmpl for the full variable list.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional


_TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "templates" / "get.sh.tmpl"


def _indent(lines: list[str], spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(f"{pad}{line}" for line in lines)


def render_get_sh(
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
    additional_env_docs: str = "",
    template_path: Path = _TEMPLATE_PATH,
) -> str:
    """Render the get.sh template for a project (S6.1).

    All [[VARNAME]] placeholders in the template are replaced with the
    provided values. Returns the rendered script as a string.
    """
    template = template_path.read_text(encoding="utf-8")

    # Build preserve_files_block (shell for-loop)
    if preserve_files:
        lines = ["for cfg in \\"]
        for f in preserve_files:
            lines.append(f'    "${{[[ENV_PREFIX]]_HOME}}/{f}" \\')
        lines.append("; do")
        lines.append('    if [[ -f "$cfg" ]]; then')
        lines.append('        bak="${cfg}.pre-update"')
        lines.append('        cp -p "$cfg" "$bak"')
        lines.append('        BACKED_UP+=("$cfg")')
        lines.append('        info "Backed up: $cfg → ${bak##*/}"')
        lines.append("    fi")
        lines.append("done")
        preserve_block = "\n".join(f"    {ln}" for ln in lines)
    else:
        preserve_block = "    # No config files to preserve for this project."

    # Build next_steps_msg (shell echo lines)
    if next_steps:
        steps_lines = [f'    echo "    {step}"' for step in next_steps]
        next_steps_msg = "\n".join(steps_lines)
    else:
        next_steps_msg = f'    echo "    {project_name} --help"'

    # Build required_deps string
    deps_str = " ".join(required_deps) if required_deps else ""

    replacements = {
        "[[PROJECT_NAME]]": project_name,
        "[[REPO_OWNER]]": repo_owner,
        "[[REPO_NAME]]": repo_name,
        "[[TAG_PREFIX]]": tag_prefix,
        "[[ENV_PREFIX]]": env_prefix,
        "[[INSTALL_DIR_DEFAULT]]": install_dir_default,
        "[[SUBDIR]]": subdir,
        "[[WRAPPER_BIN]]": wrapper_bin,
        "[[WRAPPER_SCRIPT_REL]]": wrapper_script_rel,
        "[[ASSET_SUFFIX]]": asset_suffix,
        "[[REQUIRED_DEPS]]": deps_str,
        "[[PRESERVE_FILES_BLOCK]]": preserve_block,
        "[[NEXT_STEPS_MSG]]": next_steps_msg,
        "[[ADDITIONAL_ENV_DOCS]]": additional_env_docs,
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    # Warn if any unreplaced placeholders remain
    remaining = re.findall(r"\[\[[A-Z_]+\]\]", result)
    if remaining:
        unique = sorted(set(remaining))
        print(f"[WARN] get.sh.tmpl: unreplaced placeholders: {unique}", file=sys.stderr)

    return result


def render_from_config(project_name: str, config_path: Path) -> str:
    """Render get.sh for a project using cmru.toml config."""
    from cmru.config import load_forge_config
    config = load_forge_config(config_path)
    proj = config.projects.get(project_name)
    if not proj:
        raise ValueError(f"Project '{project_name}' not found in config")
    if not proj.getsh:
        raise ValueError(f"Project '{project_name}' has no [project.{project_name}.getsh] section")

    tag_prefix = proj.prefix
    env_prefix = tag_prefix.rstrip("-v").upper().replace("-", "_")
    subdir = project_name

    return render_get_sh(
        project_name=project_name,
        repo_owner=config.github.owner,
        repo_name=config.github.repo,
        tag_prefix=tag_prefix,
        env_prefix=env_prefix,
        install_dir_default=proj.getsh.install_dir,
        subdir=subdir,
        wrapper_bin=f"/usr/local/bin/{project_name}",
        preserve_files=proj.getsh.preserve,
    )


def getsh_main(argv: Optional[list] = None) -> None:
    """Entry point for ``cmru get-sh``."""
    import argparse
    parser = argparse.ArgumentParser(description="Emit get.sh for a project (S6)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--config", help="Path to cmru.toml")
    parser.add_argument("--output", help="Write to file instead of stdout")
    args = parser.parse_args(argv)

    if args.config:
        script = render_from_config(args.project, Path(args.config).expanduser().resolve())
    else:
        # Minimal render using env vars / defaults for standalone use
        print("[ERROR] --config is required for cmru get-sh", file=sys.stderr)
        sys.exit(2)

    if args.output:
        out = Path(args.output)
        out.write_text(script, encoding="utf-8")
        out.chmod(0o755)
        print(f"[INFO] Written to {out}")
    else:
        sys.stdout.write(script)
