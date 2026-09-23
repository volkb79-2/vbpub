"""`cmru init` — guided scaffolding of cmru contracts (S10.1 verb).

Mechanical, validation-first: every generated file is parsed with the REAL
loaders (load_forge_config) in a tempdir BEFORE anything is written; an
existing target file is never overwritten. Templates ship inside the wheel
(`cmru/templates/`) so a plain `pip install cmru` carries them.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from importlib import resources
from pathlib import Path

from cmru import exit_codes
from cmru.cli_support import write_config_diagnostic

_ID_RE = re.compile(r"[a-z][a-z0-9-]*")
_GIT_OWNER_REPO_RE = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$")
_TEMPLATE_DIR = "templates"
_ARTIFACT_TYPES = ("wheel", "tarball", "bundle", "oci-image")


def _init_error(message: str) -> "NoReturn":
    write_config_diagnostic(message)
    raise SystemExit(exit_codes.CONFIG_ERROR)


def _required_prompt(message: str, default: str = "") -> str:
    value = _prompt(message, default).strip()
    if not value:
        _init_error(f"init: {message.rstrip(':')} is required")
    return value


def _yes_no(value: str, message: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"y", "yes", "n", "no"}:
        _init_error(f"init: {message} must be yes or no")
    return normalized in {"y", "yes"}


def _artifact_selection(value: str) -> list[str]:
    raw = value.strip().lower()
    if raw == "all":
        return list(_ARTIFACT_TYPES)
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts):
        _init_error("init: artifact types cannot contain an empty item")
    if len(set(parts)) != len(parts):
        _init_error("init: artifact types cannot contain duplicates")
    unknown = [part for part in parts if part not in _ARTIFACT_TYPES]
    if unknown:
        _init_error(
            "init: artifact types must be comma-separated values from "
            + ", ".join(_ARTIFACT_TYPES)
            + " or all"
        )
    return parts


def _template(name: str) -> str:
    return (
        resources.files("cmru").joinpath(f"{_TEMPLATE_DIR}/{name}").read_text(
            encoding="utf-8"
        )
    )


def _git_owner_repo(root: Path) -> tuple[str, str]:
    """Best-effort owner/repo from `origin`; empty strings when undetectable."""
    import subprocess

    try:
        url = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        url = ""
    match = _GIT_OWNER_REPO_RE.search(url)
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _prompt(message: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{message}{suffix}: ").strip()
    return answer or default


def _ask_project(root: Path, interactive: bool) -> dict:
    default_id = root.name.lower().replace("_", "-")
    default_id = _ID_RE.match(default_id) and default_id or "my-project"
    if interactive:
        project_id = _prompt("Project id (lowercase)", default_id)
    else:
        project_id = default_id
    if not _ID_RE.fullmatch(project_id):
        _init_error(f"init: project id {project_id!r} must match {_ID_RE.pattern}")
    description = (
        _prompt(f"Description for {project_id}", f"The {project_id} project.")
        if interactive
        else f"The {project_id} project."
    )
    if interactive:
        project_type = _required_prompt("Project type (python|generic)").lower()
    else:
        project_type = "python"
    if project_type not in {"python", "generic"}:
        _init_error("init: project type must be python or generic")
    artifact_selection = _prompt(
        "Artifact types (comma-separated wheel|tarball|bundle|oci-image, all, or generic)", ""
    ) if interactive else "wheel"
    generic_commands = project_type == "generic"
    if artifact_selection.strip().lower() == "generic":
        artifact_selection = _required_prompt(
            "Generic artifact types (comma-separated wheel|tarball|bundle|oci-image or all)"
        )
        generic_commands = True
    artifacts = _artifact_selection(artifact_selection)
    if not artifacts:
        _init_error("init: at least one artifact type is required")
    if any(item != "wheel" for item in artifacts):
        generic_commands = True
    if interactive:
        git_tag = _yes_no(
            _required_prompt("CMRU creates release tags? (yes/no)"),
            "release tag policy",
        )
        if not generic_commands:
            build_argv = ["python3", "-m", "cmru.handlers", "wheel-build", "--cwd", "."]
            push_argv = [
                "python3", "-m", "cmru.handlers", "wheel-publish", "--prefix", project_id,
                "--cwd", ".", "--notes-env", f"{project_id.upper().replace('-', '_')}_RELEASE_NOTES",
            ]
        else:
            build_argv = shlex.split(_required_prompt("Build command (shell words; required)"))
            push_argv = shlex.split(_required_prompt("Publish command (shell words; required)"))
    else:
        _init_error("init: adoption decisions must be collected interactively")
    return {
        "id": project_id,
        "description": description,
        "config": f"{project_id}/cmru.toml",
        "artifacts": artifacts,
        "git_tag": git_tag,
        "build_argv": build_argv,
        "push_argv": push_argv,
    }


def _expected_template_revision() -> int:
    """The revision `cmru standards` demands — kept in ONE place there."""
    from cmru.standards import PROJECT_TEMPLATE_REVISION

    return PROJECT_TEMPLATE_REVISION


def render_project_toml(
    *, project_id: str, description: str, owner: str, repo: str, owner_type: str,
    generated_by: str, centralized: bool = False, artifacts: list[str] | None = None,
    artifact_type: str | None = None,
    build_argv: list[str] | None = None, push_argv: list[str] | None = None,
    git_tag: bool = True,
) -> str:
    text = _template("project-wheel.toml")
    if not centralized:
        text = text.replace(
            "schema_version = 1\n",
            "schema_version = 1\n\n[github]\n"
            f'owner = "{owner}"\nrepo = "{repo}"\nowner_type = "{owner_type}"\n\n'
            "[targets]\nhost = \"github\"\nregistry = [\"ghcr.io\"]\n",
            1,
        )
    text = text.replace("template_revision = 4",
                        f"template_revision = {_expected_template_revision()}")
    notes_key = f"{project_id.upper().replace('-', '_')}_RELEASE_NOTES"
    selected_artifacts = artifacts or ([artifact_type] if artifact_type else ["wheel"])
    for token, value in (
        ("@@OWNER@@", owner),
        ("@@REPO@@", repo),
        ("@@OWNER_TYPE@@", owner_type),
        ("@@PROJECT_ID@@", project_id),
        ("@@DESCRIPTION@@", description),
        ("@@SCM_DIST@@", project_id),
        ("@@NOTES_ENV_KEY@@", notes_key),
        ("@@GENERATED_BY@@", generated_by),
        ("artifacts = [\"wheel\"]", "artifacts = " + json.dumps(selected_artifacts)),
        ("git_tag = true", f"git_tag = {'true' if git_tag else 'false'}"),
    ):
        text = text.replace(token, value)
    if centralized:
        text = re.sub(r"\n\[github\].*?\n\[targets\].*?\n\n", "\n", text, flags=re.S)
    if build_argv is not None:
        default = 'argv = ["python3", "-m", "cmru.handlers", "wheel-build", "--cwd", "."]'
        text = text.replace(default, "argv = " + json.dumps(build_argv), 1)
    if push_argv is not None:
        text = re.sub(
            r'argv = \["python3", "-m", "cmru\.handlers", "wheel-publish".*?\],',
            "argv = " + json.dumps(push_argv) + ",", text, count=1,
        )
    return text


def render_orchestration_toml(
    projects: list[dict], *, owner: str, repo: str, generated_by: str,
    owner_type: str = "user",
) -> str:
    text = _template("orchestration.toml")
    order = ", ".join(f'"{p["id"]}"' for p in projects)
    entries = "\n".join(
        f'[orchestration.project.{p["id"]}]\nconfig = "{p["config"]}"\ndepends_on = []'
        for p in projects
    )
    # A single project needs no orchestration file at all (the bare cmru.toml
    # loads standalone); the header line below documents that choice.
    text = text.replace("[@@PROJECT_ENTRIES_HEADER@@]", "")
    for token, value in (
        ("@@PROJECT_ORDER@@", order),
        ("@@PROJECT_ENTRIES@@", entries.rstrip()),
        ("@@OWNER@@", owner),
        ("@@REPO@@", repo),
        ("@@OWNER_TYPE@@", owner_type),
        ("@@GENERATED_BY@@", generated_by),
    ):
        text = text.replace(token, value)
    text = re.sub(r"\n\[@@PROJECT_ENTRIES_HEADER@@\]\n", "\n", text)
    return text


def collect_plan(argv: list[str], root: Path) -> dict:
    """Parse `cmru init` options and collect the complete adoption plan."""
    if any(a == "--project" or a.startswith("--project=") for a in argv):
        _init_error("init: --project was removed; run `cmru init` and use the adoption wizard")
    interactive = True

    def flag(name: str) -> str | None:
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(name + "="):
                return a.split("=", 1)[1]
        return None

    layout = flag("--layout")
    path_flag = flag("--root") or flag("--path")
    owner_flag, repo_flag = flag("--owner"), flag("--repo")

    root = Path(path_flag or root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        _init_error(f"init: adoption path is not an existing directory: {root}")
    git_owner, git_repo = _git_owner_repo(root)
    if owner_flag is None:
        owner = _required_prompt("GitHub owner", git_owner)
    else:
        owner = owner_flag.strip()
        if not owner:
            _init_error("init: GitHub owner is required")
    if repo_flag is None:
        repo = _required_prompt("GitHub repository", git_repo)
    else:
        repo = repo_flag.strip()
        if not repo:
            _init_error("init: GitHub repository is required")
    owner_type_flag = flag("--owner-type")
    owner_type = owner_type_flag or _required_prompt("GitHub owner type (user|org)")
    if owner_type not in {"user", "org"}:
        _init_error("init: GitHub owner type must be user or org")

    if layout is None and interactive:
        print("Layout:\n  1) single project (one cmru.toml, no orchestration file)\n"
              "  2) monorepo (cmru.orchestration.toml coordinating several projects)")
        layout = _prompt("Choose", "1")
    layout = {"1": "single", "2": "monorepo"}.get(str(layout), str(layout))
    if layout not in ("single", "monorepo"):
        _init_error(f"init: unknown layout {layout!r} (single|monorepo)")

    projects: list[dict] = []
    if layout == "single":
        project_path = Path(_prompt("Project folder", str(root))) if interactive else root
        if not project_path.is_absolute():
            project_path = (root / project_path).resolve()
        project_path = project_path.expanduser().resolve()
        if not project_path.is_dir():
            _init_error(f"init: project folder is not an existing directory: {project_path}")
        try:
            project_path.relative_to(root)
        except ValueError:
            _init_error(f"init: project folder escapes CMRU root: {project_path}")
        project = _ask_project(project_path, interactive)
        root = project_path
        project["folder"] = project_path
        project["config"] = "cmru.toml"
        projects.append(project)
    else:
        raw = _prompt("Project folders, comma-separated", root.name)
        for folder_text in [s.strip() for s in raw.split(",") if s.strip()]:
            folder = Path(folder_text)
            if not folder.is_absolute():
                folder = (root / folder).resolve()
            if not folder.is_dir():
                _init_error(f"init: project folder is not an existing directory: {folder}")
            project = _ask_project(folder, interactive)
            project["folder"] = folder
            try:
                project["config"] = folder.relative_to(root).joinpath("cmru.toml").as_posix()
            except ValueError:
                _init_error(f"init: project folder escapes CMRU root: {folder}")
            if project["config"] == "cmru.toml":
                project["config"] = "cmru.toml"
            projects.append(project)

    for p in projects:
        p.setdefault("description", f"The {p['id']} project.")
        p.setdefault("config", f"{p['id']}/cmru.toml")
        p.setdefault("artifacts", ["wheel"])
        p.setdefault("build_argv", ["python3", "-m", "cmru.handlers", "wheel-build", "--cwd", "."])
        p.setdefault("push_argv", ["python3", "-m", "cmru.handlers", "wheel-publish", "--prefix", p["id"], "--cwd", ".", "--notes-env", f"{p['id'].upper().replace('-', '_')}_RELEASE_NOTES"])
    return {
        "layout": layout, "root": root, "projects": projects,
        "owner": owner, "repo": repo, "owner_type": owner_type,
    }


def build_files(plan: dict, root: Path) -> list[tuple[Path, str]]:
    """Render every planned file. Raises SystemExit listing existing targets
    before writing anything."""
    generated_by = f"cmru init on {root.name}"
    files: list[tuple[Path, str]] = []
    for p in plan["projects"]:
        rel = Path(p["config"])
        if plan["layout"] == "single":
            rel = Path("cmru.toml")
            file_root = Path(p.get("folder", root))
        else:
            file_root = root
        files.append((
            file_root / rel if plan["layout"] == "single" else root / rel,
            render_project_toml(
                project_id=p["id"], description=p["description"],
                owner=plan["owner"], repo=plan["repo"],
                owner_type=plan["owner_type"], generated_by=generated_by,
                centralized=plan["layout"] == "monorepo",
                artifacts=p.get("artifacts", ["wheel"]),
                build_argv=p.get("build_argv"), push_argv=p.get("push_argv"),
                git_tag=p.get("git_tag", True),
            ),
        ))
    if plan["layout"] == "monorepo":
        files.append((root / "cmru.orchestration.toml",
                      render_orchestration_toml(plan["projects"], owner=plan["owner"],
                                                repo=plan["repo"],
                                                generated_by=generated_by,
                                                owner_type=plan["owner_type"])))
    existing = [str(path.relative_to(root)) for path, _ in files if path.exists()]
    if existing:
        _init_error(
            "init: refusing to overwrite existing file(s): " + ", ".join(existing)
            + " — delete them first or pick different paths."
        )
    return files


def validate(files: list[tuple[Path, str]], root: Path) -> None:
    """Parse every generated contract with the REAL loader from a throwaway
    dir tree — generation bugs die here, not in a consumer's first run."""
    import tempfile

    from cmru.config import load_forge_config

    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        for path, content in files:
            target = temp_root / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        last_name = ""
        for path, _ in files:
            name = path.name
            last_name = name
            if name == "cmru.orchestration.toml" or len(files) == 1:
                load_forge_config(
                    temp_root / path.relative_to(root),
                    require_orchestration=(name == "cmru.orchestration.toml"),
                )
        if last_name == "cmru.orchestration.toml":
            # The generated contracts must ALSO pass cmru's own conformance
            # gate (review finding: template drifted from standards).
            import subprocess as _sp

            res = _sp.run(
                [sys.executable, "-m", "cmru.cli", "standards", "--config",
                 str(temp_root / "cmru.orchestration.toml")],
                capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
            )
            if res.returncode != 0:
                _init_error(
                    "init: generated contracts fail `cmru standards`:\n"
                    + (res.stdout + res.stderr)[-2000:]
                )


def init_main(argv: list[str]) -> int:
    plan = collect_plan(list(argv), Path.cwd())
    root = Path(plan["root"])
    files = build_files(plan, root)
    print("\nCMRU init preview:")
    for path, content in files:
        print(f"--- {path.relative_to(root)} ---")
        print(content, end="" if content.endswith("\n") else "\n")
    validate(files, root)
    if not _yes_no(
        _required_prompt("Write these validated files? (yes/no)"),
        "write confirmation",
    ):
        _init_error("init: adoption cancelled before writing")
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(root)}")
    print(
        "\nNext steps:\n"
        "  1. Fill in real values where placeholders remain (owner/repo,\n"
        "     release-notes env).\n"
        "  2. Put credentials in the gitignored cmru.secret.toml.\n"
        "  3. Dry-run the estate graph: `cmru dependencies` then `cmru run`.\n"
    )
    return 0
