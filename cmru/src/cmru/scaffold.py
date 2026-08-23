"""`cmru init` — guided scaffolding of cmru contracts (S10.1 verb).

Mechanical, validation-first: every generated file is parsed with the REAL
loaders (load_forge_config) in a tempdir BEFORE anything is written; an
existing target file is never overwritten. Templates ship inside the wheel
(`cmru/templates/`) so a plain `pip install cmru` carries them.
"""

from __future__ import annotations

import re
import sys
from importlib import resources
from pathlib import Path

_ID_RE = re.compile(r"[a-z][a-z0-9-]*")
_GIT_OWNER_REPO_RE = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$")
_TEMPLATE_DIR = "templates"


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
        raise SystemExit(f"init: project id {project_id!r} must match {_ID_RE.pattern}")
    description = (
        _prompt(f"Description for {project_id}", f"The {project_id} project.")
        if interactive
        else f"The {project_id} project."
    )
    return {
        "id": project_id,
        "description": description,
        "config": f"{project_id}/cmru.toml",
    }


def _expected_template_revision() -> int:
    """The revision `cmru standards` demands — kept in ONE place there."""
    from cmru.standards import PROJECT_TEMPLATE_REVISION

    return PROJECT_TEMPLATE_REVISION


def render_project_toml(
    *, project_id: str, description: str, owner: str, repo: str, owner_type: str,
    generated_by: str,
) -> str:
    text = _template("project-wheel.toml")
    text = text.replace("template_revision = 4",
                        f"template_revision = {_expected_template_revision()}")
    notes_key = f"{project_id.upper().replace('-', '_')}_RELEASE_NOTES"
    for token, value in (
        ("@@OWNER@@", owner),
        ("@@REPO@@", repo),
        ("@@OWNER_TYPE@@", owner_type),
        ("@@PROJECT_ID@@", project_id),
        ("@@DESCRIPTION@@", description),
        ("@@SCM_DIST@@", project_id),
        ("@@NOTES_ENV_KEY@@", notes_key),
        ("@@GENERATED_BY@@", generated_by),
    ):
        text = text.replace(token, value)
    return text


def render_orchestration_toml(
    projects: list[dict], *, owner: str, repo: str, generated_by: str
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
        ("@@GENERATED_BY@@", generated_by),
    ):
        text = text.replace(token, value)
    text = re.sub(r"\n\[@@PROJECT_ENTRIES_HEADER@@\]\n", "\n", text)
    return text


def collect_plan(argv: list[str], root: Path) -> dict:
    """Parse `cmru init` args + prompts into a full plan. Non-interactive when
    any of --layout/--project is supplied."""
    interactive = not any(a.startswith("--layout") or a.startswith("--project") for a in argv)

    def flag(name: str) -> str | None:
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith(name + "="):
                return a.split("=", 1)[1]
        return None

    def flags(name: str) -> list[str]:
        """All occurrences of a repeatable flag (e.g. --project A --project B)."""
        found: list[str] = []
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                found.append(argv[i + 1])
            elif a.startswith(name + "="):
                found.append(a.split("=", 1)[1])
        return found

    layout = flag("--layout")
    project_flag = flag("--project")
    owner_flag, repo_flag = flag("--owner"), flag("--repo")

    git_owner, git_repo = _git_owner_repo(root)
    repo = repo_flag or git_repo or root.name
    owner = owner_flag or git_owner
    if interactive and not owner:
        owner = _prompt("GitHub owner", owner or "your-github-owner")
    owner_type = "user"

    if layout is None and interactive:
        print("Layout:\n  1) single project (one cmru.toml, no orchestration file)\n"
              "  2) monorepo (cmru.orchestration.toml coordinating several projects)")
        layout = _prompt("Choose", "1")
    layout = {"1": "single", "2": "monorepo"}.get(str(layout), str(layout))
    if layout not in ("single", "monorepo"):
        raise SystemExit(f"init: unknown layout {layout!r} (single|monorepo)")

    repeated_projects = flags("--project")
    if repeated_projects and project_flag is None:
        project_flag = repeated_projects[0] if len(repeated_projects) == 1 else None

    projects: list[dict] = []
    if layout == "single":
        if project_flag:
            projects.append({"id": project_flag})
        else:
            projects.append(_ask_project(root, interactive))
    else:
        ids: list[str] = repeated_projects or ([project_flag] if project_flag else [])
        if interactive:
            raw = _prompt(
                "Project ids, comma-separated (Enter = this repo's name)", ""
            )
            ids = [s.strip() for s in raw.split(",") if s.strip()] or ids
        if not ids:
            ids = [root.name.lower().replace("_", "-")]
        for pid in ids:
            if not _ID_RE.fullmatch(pid):
                raise SystemExit(f"init: project id {pid!r} must match {_ID_RE.pattern}")
            projects.append({"id": pid, "config": f"{pid}/cmru.toml",
                             "description": f"The {pid} project."})

    for p in projects:
        p.setdefault("description", f"The {p['id']} project.")
        p.setdefault("config", f"{p['id']}/cmru.toml")
    return {
        "layout": layout, "projects": projects,
        "owner": owner or "your-github-owner", "repo": repo, "owner_type": owner_type,
    }


def build_files(plan: dict, root: Path) -> list[tuple[Path, str]]:
    """Render every planned file. Raises SystemExit listing existing targets
    before writing anything."""
    generated_by = f"cmru init on {root.name}"
    files: list[tuple[Path, str]] = []
    for p in plan["projects"]:
        rel = Path(p["config"])
        files.append((
            root / rel,
            render_project_toml(
                project_id=p["id"], description=p["description"],
                owner=plan["owner"], repo=plan["repo"],
                owner_type=plan["owner_type"], generated_by=generated_by,
            ),
        ))
    if plan["layout"] == "monorepo":
        files.append((root / "cmru.orchestration.toml",
                      render_orchestration_toml(plan["projects"], owner=plan["owner"],
                                                repo=plan["repo"],
                                                generated_by=generated_by)))
    existing = [str(path.relative_to(root)) for path, _ in files if path.exists()]
    if existing:
        raise SystemExit(
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
        for path, _ in files:
            name = path.name
            load_forge_config(temp_root / path.relative_to(root),
                              require_orchestration=(name == "cmru.orchestration.toml"))
        if name == "cmru.orchestration.toml":
            # The generated contracts must ALSO pass cmru's own conformance
            # gate (review finding: template drifted from standards).
            import subprocess as _sp

            res = _sp.run(
                [sys.executable, "-m", "cmru.cli", "standards", "--config",
                 str(temp_root / "cmru.orchestration.toml")],
                capture_output=True, text=True, cwd=str(Path(__file__).parent.parent),
            )
            if res.returncode != 0:
                raise SystemExit(
                    "init: generated contracts fail `cmru standards`:\n"
                    + (res.stdout + res.stderr)[-2000:]
                )


def init_main(argv: list[str]) -> int:
    root = Path.cwd()
    plan = collect_plan(list(argv), root)
    files = build_files(plan, root)
    validate(files, root)
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
