"""`cmru init` — guided scaffolding of cmru contracts (S10.1 verb).

Mechanical, validation-first: every generated file is parsed with the REAL
loaders (load_forge_config) in a tempdir BEFORE anything is written; an
existing target file is never overwritten. Templates ship inside the wheel
(`cmru/templates/`) so a plain `pip install cmru` carries them.
"""

from __future__ import annotations

import json
import io
import re
import shlex
from importlib import resources
from pathlib import Path

from cmru import exit_codes
from cmru.cli_support import write_config_diagnostic
from cli_extended import CliFailure, OptionSpec, VerbGroup, VerbSpec
from cmru.cli_support import INTERACTIVE_EXTRA, cmru_registry

_ID_RE = re.compile(r"[a-z][a-z0-9-]*")
_GIT_OWNER_REPO_RE = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$")
_TEMPLATE_DIR = "templates"
_ARTIFACT_TYPES = ("wheel", "tarball", "bundle", "oci-image")
#: Project-level facts a ``monorepo`` layout cannot take from flags (they would
#: apply to every project); each project is asked for them instead.
_PROJECT_FACT_KEYS = (
    "project_id", "description", "project_type", "artifacts", "release_tags",
    "build_command", "publish_command", "project_folder",
)


def _init_error(message: str) -> "NoReturn":
    write_config_diagnostic(message)
    raise SystemExit(exit_codes.CONFIG_ERROR)


class _NoPrompts:
    """The prompt driver for a run whose facts are already complete.

    Questions that carry a default (project id, description, folder) take it;
    a question without one is a fact that should have come from a flag.
    """

    def text(self, message: str, *, default: str | None = None, required: bool = True) -> str:
        if default is None:
            raise CliFailure(
                f"init: {message.rstrip(':')} is required; pass its option or run from a terminal",
                exit_code=exit_codes.CONFIG_ERROR,
            )
        return default

    def select(self, message: str, choices, *, default: str | None = None) -> str:
        return self.text(message, default=default)

    def confirm(self, message: str, *, default: bool = False) -> bool:
        raise CliFailure(
            f"init: {message.rstrip('?')} is required; pass its option or run from a terminal",
            exit_code=exit_codes.CONFIG_ERROR,
        )


def _given(options: dict, key: str, ask):
    """The flag's value when given, otherwise the answer to ``ask()``."""
    value = options.get(key)
    return value if value is not None else ask()


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


def _split_command(text: str, label: str) -> list[str]:
    try:
        words = shlex.split(text)
    except ValueError as exc:
        _init_error(f"init: {label} is not valid shell words: {exc}")
    if not words:
        _init_error(f"init: {label} is required")
    return words


def _ask_project(root: Path, options: dict, prompts) -> dict:
    """One project's facts: a given option wins, otherwise ``prompts`` asks."""
    default_id = root.name.lower().replace("_", "-")
    default_id = _ID_RE.match(default_id) and default_id or "my-project"
    project_id = _given(options, "project_id", lambda: prompts.text(
        "Project id (lowercase)", default=default_id))
    if not _ID_RE.fullmatch(project_id):
        _init_error(f"init: project id {project_id!r} must match {_ID_RE.pattern}")
    description = _given(options, "description", lambda: prompts.text(
        f"Description for {project_id}", default=f"The {project_id} project."))
    project_type = _given(options, "project_type", lambda: prompts.select(
        "Project type", ("python", "generic")))
    if project_type not in {"python", "generic"}:
        _init_error("init: project type must be python or generic")
    artifact_selection = _given(options, "artifacts", lambda: prompts.text(
        "Artifact types (comma-separated wheel|tarball|bundle|oci-image, all, or generic)"))
    generic_commands = project_type == "generic"
    if artifact_selection.strip().lower() == "generic":
        artifact_selection = prompts.text(
            "Generic artifact types (comma-separated wheel|tarball|bundle|oci-image or all)")
        generic_commands = True
    artifacts = _artifact_selection(artifact_selection)
    if any(item != "wheel" for item in artifacts):
        generic_commands = True
    release_tags = options.get("release_tags")
    git_tag = (
        release_tags == "yes" if release_tags is not None
        else prompts.confirm("CMRU creates release tags?")
    )
    if not generic_commands:
        build_argv = ["cmru", "handler", "wheel-build", "--cwd", "."]
        push_argv = [
            "cmru", "handler", "wheel-publish", "--prefix", project_id,
            "--cwd", ".", "--notes-env", f"{project_id.upper().replace('-', '_')}_RELEASE_NOTES",
        ]
    else:
        build_argv = _split_command(_given(options, "build_command", lambda: prompts.text(
            "Build command (shell words; required)")), "build command")
        push_argv = _split_command(_given(options, "publish_command", lambda: prompts.text(
            "Publish command (shell words; required)")), "publish command")
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
    text = text.replace("template_revision = 5",
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
        default = 'argv = ["cmru", "handler", "wheel-build", "--cwd", "."]'
        text = text.replace(default, "argv = " + json.dumps(build_argv), 1)
    if push_argv is not None:
        text = re.sub(
            r'argv = \["cmru", "handler", "wheel-publish".*?\],',
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


def _missing_facts(options: dict, git_owner: str, git_repo: str) -> list[str]:
    """The facts with no option and no safe default, by option name.

    ``init`` is non-interactive exactly when this is empty (redesign B12). A
    monorepo is never complete from options: every project asks for its own
    facts.
    """
    missing: list[str] = []
    if options.get("owner") is None and not git_owner:
        missing.append("--owner")
    if options.get("repo") is None and not git_repo:
        missing.append("--repo")
    if options.get("owner_type") is None:
        missing.append("--owner-type")
    if options.get("layout") != "single":
        missing.append("--layout single")
        return missing
    for key, flag in (
        ("project_type", "--kind"),
        ("artifacts", "--artifacts"),
        ("release_tags", "--release-tags"),
    ):
        if options.get(key) is None:
            missing.append(flag)
    artifacts = (options.get("artifacts") or "").strip().lower()
    if artifacts == "generic":
        missing.append("--artifacts")
    needs_commands = (
        options.get("project_type") == "generic"
        or artifacts == "generic"
        or any(part.strip() not in {"", "wheel"} for part in artifacts.split(","))
    )
    if needs_commands:
        for key, flag in (("build_command", "--build-command"), ("publish_command", "--publish-command")):
            if options.get(key) is None:
                missing.append(flag)
    return missing


def collect_plan(options: dict, root: Path, prompts=None) -> dict:
    """Collect the adoption plan from already parsed CLI options.

    ``prompts`` is the runtime prompt API (``runtime.prompts``); it is asked
    only for facts the options leave open.
    """
    options = dict(options)
    if options.get("layout") is None and any(
        options.get(key) is not None for key in _PROJECT_FACT_KEYS
    ):
        options["layout"] = "single"  # a per-project option implies one project
    path_flag = options.get("root")
    owner_flag, repo_flag = options.get("owner"), options.get("repo")

    root = Path(path_flag or root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        _init_error(f"init: adoption path is not an existing directory: {root}")
    git_owner, git_repo = _git_owner_repo(root)
    # Non-interactive when the facts are complete (redesign B12): the runtime
    # prompt API is consulted only while a fact is still missing.
    if not _missing_facts(options, git_owner, git_repo) or prompts is None:
        prompts = _NoPrompts()
    if owner_flag is None:
        owner = prompts.text("GitHub owner", default=git_owner or None).strip()
    else:
        owner = owner_flag.strip()
    if not owner:
        _init_error("init: GitHub owner is required")
    if repo_flag is None:
        repo = prompts.text("GitHub repository", default=git_repo or None).strip()
    else:
        repo = repo_flag.strip()
    if not repo:
        _init_error("init: GitHub repository is required")
    owner_type = _given(options, "owner_type", lambda: prompts.select(
        "GitHub owner type", ("user", "org")))
    if owner_type not in {"user", "org"}:
        _init_error("init: GitHub owner type must be user or org")

    layout = _given(options, "layout", lambda: prompts.select(
        "Layout (single: one cmru.toml, no orchestration file; monorepo: "
        "cmru.orchestration.toml coordinating several projects)",
        ("single", "monorepo"), default="single"))
    if layout not in ("single", "monorepo"):
        _init_error(f"init: unknown layout {layout!r} (single|monorepo)")
    if layout == "monorepo" and any(options.get(key) is not None for key in _PROJECT_FACT_KEYS):
        _init_error(
            "init: per-project options (--id, --kind, --artifacts, ...) apply to "
            "--layout single; a monorepo asks for each project's facts"
        )

    projects: list[dict] = []
    if layout == "single":
        project_path = Path(_given(options, "project_folder", lambda: prompts.text(
            "Project folder", default=str(root))))
        if not project_path.is_absolute():
            project_path = (root / project_path).resolve()
        project_path = project_path.expanduser().resolve()
        if not project_path.is_dir():
            _init_error(f"init: project folder is not an existing directory: {project_path}")
        try:
            project_path.relative_to(root)
        except ValueError:
            _init_error(f"init: project folder escapes CMRU root: {project_path}")
        project = _ask_project(project_path, options, prompts)
        root = project_path
        project["folder"] = project_path
        project["config"] = "cmru.toml"
        projects.append(project)
    else:
        raw = prompts.text("Project folders, comma-separated", default=root.name)
        for folder_text in [s.strip() for s in raw.split(",") if s.strip()]:
            folder = Path(folder_text)
            if not folder.is_absolute():
                folder = (root / folder).resolve()
            if not folder.is_dir():
                _init_error(f"init: project folder is not an existing directory: {folder}")
            project = _ask_project(folder, {}, prompts)
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
        p.setdefault("build_argv", ["cmru", "handler", "wheel-build", "--cwd", "."])
        p.setdefault("push_argv", ["cmru", "handler", "wheel-publish", "--prefix", p["id"], "--cwd", ".", "--notes-env", f"{p['id'].upper().replace('-', '_')}_RELEASE_NOTES"])
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
            from cmru.standards import standards_cli

            stdout = io.StringIO()
            stderr = io.StringIO()
            result = standards_cli().run(
                argv=["--config", str(temp_root / "cmru.orchestration.toml")],
                stdout=stdout,
                stderr=stderr,
            )
            if result != 0:
                _init_error(
                    "init: generated contracts fail `cmru standards`:\n"
                    + (stdout.getvalue() + stderr.getvalue())[-2000:]
                )


def init_cli():
    registry = cmru_registry(
        "cmru init",
        "Guided scaffolding. Asks only for facts the options leave open (a complete "
        "set of options runs without a terminal); generated contracts are validated "
        "with the real loaders before anything is written; existing files are never overwritten.",
        single_command=True,
        no_args_action=True,
    )
    registry.register(VerbSpec(
        "init",
        description="Adopt a folder by creating a validated project or monorepo contract.",
        group=VerbGroup.MODIFICATION.value,
        mutating=True,
        dry_run=True,
        interactive=True,
        include_confirmation=True,
        options=(
            OptionSpec(("--root",), "adoption directory (default: current directory)", metavar="PATH", parser_kwargs={"default": None}),
            OptionSpec(("--layout",), "contract layout; a monorepo always asks for each project's facts", metavar="LAYOUT", parser_kwargs={"choices": ("single", "monorepo"), "default": None}),
            OptionSpec(("--owner",), "GitHub owner (default: detected from origin)", metavar="OWNER", parser_kwargs={"default": None}),
            OptionSpec(("--repo",), "GitHub repository (default: detected from origin)", metavar="REPO", parser_kwargs={"default": None}),
            OptionSpec(("--owner-type",), "GitHub owner type", metavar="TYPE", parser_kwargs={"choices": ("user", "org"), "default": None}),
            OptionSpec(("--folder",), "single layout: the project folder inside --root (default: --root itself)", metavar="PATH", parser_kwargs={"dest": "project_folder", "default": None}),
            OptionSpec(("--id",), "single layout: project id, lowercase (default: derived from the folder name)", metavar="ID", parser_kwargs={"dest": "project_id", "default": None}),
            OptionSpec(("--description",), "single layout: one-line project description (default: 'The <id> project.')", metavar="TEXT", parser_kwargs={"default": None}),
            OptionSpec(("--kind",), "single layout: python (wheel commands generated) or generic (you give the commands)", metavar="KIND", parser_kwargs={"dest": "project_type", "choices": ("python", "generic"), "default": None}),
            OptionSpec(("--artifacts",), "single layout: comma-separated wheel|tarball|bundle|oci-image, or all", metavar="LIST", parser_kwargs={"default": None}),
            OptionSpec(("--release-tags",), "single layout: whether CMRU creates release tags", metavar="yes|no", parser_kwargs={"choices": ("yes", "no"), "default": None}),
            OptionSpec(("--build-command",), "single layout: build command as shell words (needed for generic projects or non-wheel artifacts)", metavar="COMMAND", parser_kwargs={"default": None}),
            OptionSpec(("--publish-command",), "single layout: publish command as shell words (needed for generic projects or non-wheel artifacts)", metavar="COMMAND", parser_kwargs={"default": None}),
        ),
        include_json=False,
        include_progress=False,
        handler=_run_init,
    ))
    return registry.build()


def init_main(argv: list[str] | None = None) -> int:
    return init_cli().run(argv=argv, interactive_extra=INTERACTIVE_EXTRA)


def _run_init(args, runtime) -> None:
    plan = collect_plan(vars(args), Path.cwd(), runtime.prompts)
    root = Path(plan["root"])
    files = build_files(plan, root)
    print("\nCMRU init preview:")
    for path, content in files:
        print(f"--- {path.relative_to(root)} ---")
        print(content, end="" if content.endswith("\n") else "\n")
    validate(files, root)
    if runtime.dry_run:
        print("[DRY RUN] Validated plan only; no files were written.")
        return
    if not runtime.confirm("Write these validated files?"):
        return  # a declined confirmation is a clean "done" (exit 0, redesign E)
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(root)}")
    print(
        "\nNext steps:\n"
        "  1. Fill in real values where placeholders remain (owner/repo,\n"
        "     release-notes env).\n"
        "  2. Put credentials in the gitignored cmru.secret.toml.\n"
        "  3. Check the estate graph with `cmru dependencies`, then preview the\n"
        "     steps with `cmru run --dry-run` (a bare `cmru run` really runs them).\n"
    )
