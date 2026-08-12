"""Strict CMRU configuration readers.

There are exactly two configuration documents:

* ``cmru.toml`` lives in a product directory and owns that product's release,
  test, build and publication contract.
* ``cmru.orchestration.toml`` lives at a multi-product repository root and
  names only the product configurations it coordinates, their order, and
  estate cleanup policy.

The two formats intentionally do not overlap.  In particular, an orchestration
file cannot hide product commands and a product file cannot smuggle in an
estate-wide release order.  This makes a product directory portable to a fresh
repository root without an implicit dependency on a former monorepo root.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomllib  # type: ignore[no-redef]

from cmru import exit_codes


# ─── Config dataclasses (S2) ─────────────────────────────────────────────────

@dataclass(frozen=True)
class GitHubS2Config:
    owner: str
    repo: str
    owner_type: str          # "user" | "org"  (V03)
    token: Optional[str]     # environment or project-local gitignored overlay only


@dataclass(frozen=True)
class TargetsConfig:
    host: str                # "github" (v1); interface-backed (S11)
    registry: List[str]      # OCI registries to push to (S11.3)


@dataclass(frozen=True)
class VersionConfig:
    strategy: str            # "scm" | "file:<PATH>" | "counter"
    paths: List[str]         # paths to watch for change detection (S12.2)
    bump: str                # "conventional" | "patch"


@dataclass(frozen=True)
class InstallerWheel:
    """A bundled wheel entry in the [installer] config (§3.1)."""
    path: str                # glob inside release bundle, e.g. "vendor/cmru-*.whl"
    distribution: str        # pip distribution name, e.g. "cmru"


@dataclass(frozen=True)
class InstallerConfig:
    """[project.installer] — transactional installer config (§3.1).

    Replaces the retired [getsh] section (V-rule: surviving [getsh] key is exit 2).
    """
    install_dir_system: str          # system-scope root, e.g. "/opt/<name>"
    install_dir_user: str            # leaf under $XDG_DATA_HOME/<name>
    asset_suffix: str                # e.g. ".tar.xz"
    entrypoint: Optional[str]        # project adapter, relative to release root; may be absent
    required_commands: List[str]     # checked pre-network
    preserve: List[str]              # paths preserved under <root>/shared/
    manifest_name: str               # default "manifest.json"
    signature_name: str              # default "manifest.json.minisig"
    wheels: List[InstallerWheel]     # bundled wheels to install into venv


@dataclass(frozen=True)
class VariantConfig:
    """A named per-interpreter artifact variant (S-REL.6).

    Multi-variant bundle/tarball projects publish N variants under ONE release tag as
    distinct assets named ``<tag>-<name><suffix>`` (e.g. a version-locked ``py39`` and
    ``py311`` bundle). The operator selects one at install time (get.py ``--variant``).
    An empty variant list ⇒ today's exact single-asset behaviour.
    """
    name: str                    # variant id, used verbatim in the asset filename
    build_arg: Optional[str]     # per-variant build arg the project's build step consumes
    label: Optional[str]         # human description shown by the installer's variant prompt


@dataclass(frozen=True)
class ProjectS2Config:
    name: str
    template_revision: Optional[int]
    prefix: str              # git tag prefix, e.g. "tls-edge-v"
    artifacts: List[str]     # complete declared output inventory
    scm_dist: Optional[str]  # python dist name (wheel type only)
    version: Optional[VersionConfig]
    installer: Optional[InstallerConfig]
    steps: Mapping[str, list]
    # Source-first release history defaults to this project-relative document.
    # ``[project.release] changelog = false`` is the explicit opt-out.
    changelog: Optional[str] = "CHANGES.md"
    variants: List[VariantConfig] = field(default_factory=list)  # empty ⇒ single-asset (S-REL.6)
    description: str = ""
    project_root: Optional[Path] = None
    env: Mapping[str, str] = field(default_factory=dict)
    build_metadata: Mapping[str, str] = field(default_factory=dict)
    artifact_dirs: List[str] = field(default_factory=list)
    build_step: str = ""


@dataclass(frozen=True)
class OrchestrationConfig:
    project_order: List[str]
    default_projects: List[str]
    default_steps: List[str]
    execution_mode: str
    project_configs: Mapping[str, Path] = field(default_factory=dict)
    dependencies: Mapping[str, List[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class CleanupS2Config:
    max_age_days: Optional[int]
    release_tag_prefixes: List[str] = field(default_factory=list)
    keep_release_tags: List[str] = field(default_factory=list)
    ghcr_packages: List[str] = field(default_factory=list)
    ghcr_delete_packages: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ForgeConfig:
    """Parsed cmru.toml (S2 schema)."""
    github: GitHubS2Config
    targets: TargetsConfig
    orchestration: Optional[OrchestrationConfig]
    cleanup: Optional[CleanupS2Config]
    projects: Mapping[str, ProjectS2Config]
    repo_root: Path          # directory containing the config file
    env: Mapping[str, str] = field(default_factory=dict)


# ─── Parsing ─────────────────────────────────────────────────────────────────

def _require(d: dict, key: str, section: str) -> object:
    val = d.get(key)
    if val is None:
        print(f"[ERROR] {section}.{key} is required", flush=True)
        raise SystemExit(exit_codes.CONFIG_ERROR)
    return val


def _error(message: str) -> "None":
    print(f"[ERROR] {message}", flush=True)
    raise SystemExit(exit_codes.CONFIG_ERROR)


def _reject_unknown(raw: dict, known: set[str], where: str) -> None:
    unknown = sorted(set(raw) - known)
    if unknown:
        _error(f"{where}: unknown keys {unknown}")


def _require_table(raw: dict, key: str, where: str) -> dict:
    value = _require(raw, key, where)
    if not isinstance(value, dict):
        _error(f"{where}.{key} must be a table")
    return value


def _string_list(value: object, where: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        _error(f"{where} must be a list of non-empty strings")
    return list(value)


def _parse_version(raw: dict, project_name: str) -> VersionConfig:
    where = f"project.{project_name}.version"
    _reject_unknown(raw, {"strategy", "bump", "paths", "base_version", "file"}, where)
    strategy = str(_require(raw, "strategy", where)).strip()
    if not (
        strategy in {"scm", "counter", "none"}
        or strategy.startswith("file:") and len(strategy) > len("file:")
        or strategy.startswith("external:") and len(strategy) > len("external:")
    ):
        _error(
            f"{where}.strategy must be scm, counter, none, "
            "file:<PATH>, or external:<ENV_VAR>"
        )
    bump_value = _require(raw, "bump", where)
    if not isinstance(bump_value, str) or not bump_value.strip():
        _error(f"{where}.bump must be a non-empty string")
    bump = bump_value.strip()
    if bump not in ("conventional", "patch"):
        _error(f"{where}.bump must be 'conventional' or 'patch'")
    paths = raw.get("paths") or []
    if not isinstance(paths, list):
        _error(f"{where}.paths must be a list")
    return VersionConfig(strategy=strategy, paths=[str(p) for p in paths], bump=bump)


_VALID_ARTIFACTS = {"wheel", "tarball", "oci-image", "bundle"}


def _parse_artifacts(name: str, raw: dict) -> List[str]:
    """Read the canonical project artifact inventory.

    ``artifact`` is retired with the strict S2 contract. Artifact names describe
    released outputs; they never synthesize commands or select an implicit runner.
    """
    items = _require(raw, "artifacts", f"project.{name}")
    if not isinstance(items, list):
        _error(f"project.{name}.artifacts must be a list")
    artifacts = [str(i).strip() for i in items if str(i).strip()]
    if not artifacts:
        _error(f"project.{name}.artifacts must not be empty")
    unknown = [a for a in artifacts if a not in _VALID_ARTIFACTS]
    if unknown:
        _error(f"project.{name}.artifacts: unknown {unknown}; valid: {sorted(_VALID_ARTIFACTS)}")
    return artifacts


def _parse_installer(name: str, raw: dict) -> InstallerConfig:
    """Parse [project.installer] — fail-fast, unknown keys rejected (V09)."""
    _KNOWN_INSTALLER_KEYS = {
        "install_dir_system", "install_dir_user", "asset_suffix", "entrypoint",
        "required_commands", "preserve", "manifest_name", "signature_name", "wheels",
    }
    unknown = [k for k in raw if k not in _KNOWN_INSTALLER_KEYS]
    if unknown:
        print(f"[ERROR] project.{name}.installer: unknown keys {sorted(unknown)} (V09)")
        raise SystemExit(exit_codes.CONFIG_ERROR)

    install_dir_system = str(
        _require(raw, "install_dir_system", f"project.{name}.installer")
    )
    install_dir_user = str(
        _require(raw, "install_dir_user", f"project.{name}.installer")
    )
    asset_suffix = str(raw.get("asset_suffix") or ".tar.xz")
    entrypoint: Optional[str] = raw.get("entrypoint") or None
    if entrypoint is not None:
        entrypoint = str(entrypoint)

    required_commands_raw = raw.get("required_commands") or []
    if not isinstance(required_commands_raw, list):
        print(f"[ERROR] project.{name}.installer.required_commands must be a list")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    required_commands = [str(c) for c in required_commands_raw]

    preserve_raw = raw.get("preserve") or []
    if not isinstance(preserve_raw, list):
        print(f"[ERROR] project.{name}.installer.preserve must be a list")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    preserve = [str(p) for p in preserve_raw]

    manifest_name = str(raw.get("manifest_name") or "manifest.json")
    signature_name = str(raw.get("signature_name") or "manifest.json.minisig")

    wheels_raw = raw.get("wheels") or []
    if not isinstance(wheels_raw, list):
        print(f"[ERROR] project.{name}.installer.wheels must be an array of tables")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    wheels: List[InstallerWheel] = []
    _KNOWN_WHEEL_KEYS = {"path", "distribution"}
    for i, w in enumerate(wheels_raw):
        if not isinstance(w, dict):
            print(f"[ERROR] project.{name}.installer.wheels[{i}] must be a table")
            raise SystemExit(exit_codes.CONFIG_ERROR)
        unknown_w = [k for k in w if k not in _KNOWN_WHEEL_KEYS]
        if unknown_w:
            print(
                f"[ERROR] project.{name}.installer.wheels[{i}]: "
                f"unknown keys {sorted(unknown_w)} (V09)"
            )
            raise SystemExit(exit_codes.CONFIG_ERROR)
        wpath = str(_require(w, "path", f"project.{name}.installer.wheels[{i}]"))
        wdist = str(_require(w, "distribution", f"project.{name}.installer.wheels[{i}]"))
        wheels.append(InstallerWheel(path=wpath, distribution=wdist))

    return InstallerConfig(
        install_dir_system=install_dir_system,
        install_dir_user=install_dir_user,
        asset_suffix=asset_suffix,
        entrypoint=entrypoint,
        required_commands=required_commands,
        preserve=preserve,
        manifest_name=manifest_name,
        signature_name=signature_name,
        wheels=wheels,
    )


# A variant name is used verbatim inside a release-asset filename, so keep it to a
# filesystem/URL-safe token (letters, digits, dot, dash, underscore) — V22.
_VARIANT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _parse_variants(name: str, raw: dict) -> List[VariantConfig]:
    """Parse ``[[project.variants]]`` — fail-fast, unknown keys rejected (V09/V22).

    Returns [] when no variants are declared (the single-asset path). Each variant needs a
    unique, filename-safe ``name``; ``build_arg`` and ``label`` are optional.
    """
    items = raw.get("variants")
    if items is None:
        return []
    if not isinstance(items, list):
        print(f"[ERROR] project.{name}.variants must be an array of tables")
        raise SystemExit(exit_codes.CONFIG_ERROR)

    _KNOWN_VARIANT_KEYS = {"name", "build_arg", "label"}
    variants: List[VariantConfig] = []
    seen: set[str] = set()
    for i, v in enumerate(items):
        if not isinstance(v, dict):
            print(f"[ERROR] project.{name}.variants[{i}] must be a table")
            raise SystemExit(exit_codes.CONFIG_ERROR)
        unknown = [k for k in v if k not in _KNOWN_VARIANT_KEYS]
        if unknown:
            print(f"[ERROR] project.{name}.variants[{i}]: unknown keys {sorted(unknown)} (V09)")
            raise SystemExit(exit_codes.CONFIG_ERROR)
        vname = str(_require(v, "name", f"project.{name}.variants[{i}]"))
        if not _VARIANT_NAME_RE.match(vname):
            print(
                f"[ERROR] project.{name}.variants[{i}].name {vname!r} is invalid (V22): "
                "use only letters, digits, '.', '-', '_' (it goes into the asset filename)"
            )
            raise SystemExit(exit_codes.CONFIG_ERROR)
        if vname in seen:
            print(f"[ERROR] project.{name}.variants: duplicate name {vname!r} (V22)")
            raise SystemExit(exit_codes.CONFIG_ERROR)
        seen.add(vname)
        build_arg = v.get("build_arg")
        label = v.get("label")
        variants.append(VariantConfig(
            name=vname,
            build_arg=str(build_arg) if build_arg is not None else None,
            label=str(label) if label is not None else None,
        ))
    return variants


def _read_toml(path: Path, expected_name: str) -> dict:
    if path.name != expected_name:
        _error(f"expected {expected_name}, got {path.name}")
    if not path.is_file():
        _error(f"config file not found: {path}")
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if not isinstance(raw, dict):
        _error(f"{path}: TOML document must be a table")
    return raw


def _scalar_env(raw: object, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _error(f"{where} must be a table")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key or not isinstance(value, (str, int, float, bool)):
            _error(f"{where} must contain string keys and scalar values")
        result[key] = str(value)
    return result


def _github(raw: object, config_path: Path) -> GitHubS2Config:
    if not isinstance(raw, dict):
        _error("[github] is required")
    _reject_unknown(raw, {"owner", "repo", "owner_type"}, "github")
    owner = _require(raw, "owner", "github")
    repo = _require(raw, "repo", "github")
    owner_type = _require(raw, "owner_type", "github")
    if not isinstance(owner, str) or not owner.strip() or not isinstance(repo, str) or not repo.strip():
        _error("github.owner and github.repo must be non-empty strings")
    if owner_type not in {"user", "org"}:
        _error("github.owner_type must be 'user' or 'org'")
    token = _resolve_project_token(config_path)
    return GitHubS2Config(owner=owner.strip(), repo=repo.strip(), owner_type=owner_type, token=token or None)


def _resolve_project_token(config_path: Path) -> str:
    """Read an explicit process credential or this project's ignored overlay.

    A committed credential is rejected by ``_github``.  There is deliberately no
    secondary config source that could turn a stale repository value into an
    unnoticed publication authority.
    """
    for env_name in ("GITHUB_PUSH_PAT", "GITHUB_TOKEN"):
        value = (os.getenv(env_name) or "").strip()
        if value:
            return value
    secret_path = config_path.with_name("cmru.secret.toml")
    if secret_path.is_file():
        with secret_path.open("rb") as handle:
            secret = tomllib.load(handle)
        if not isinstance(secret, dict) or set(secret) != {"github"} or not isinstance(secret["github"], dict):
            _error(f"{secret_path}: expected only [github]")
        token = secret["github"].get("token")
        if not isinstance(token, str) or not token.strip():
            _error(f"{secret_path}: github.token must be a non-empty string")
        return token.strip()
    return ""


def _targets(raw: object) -> TargetsConfig:
    if not isinstance(raw, dict):
        _error("[targets] is required")
    _reject_unknown(raw, {"host", "registry"}, "targets")
    host = _require(raw, "host", "targets")
    registry = _require(raw, "registry", "targets")
    if not isinstance(host, str) or not host.strip():
        _error("targets.host must be a non-empty string")
    return TargetsConfig(host=host.strip(), registry=_string_list(registry, "targets.registry"))


def _validate_runner_steps(raw_steps: object) -> dict[str, dict]:
    """Validate the one runner grammar shared by direct and orchestrated runs."""
    if not isinstance(raw_steps, dict) or not raw_steps:
        _error("[steps] is required and must be a non-empty table")
    known_step_keys = {
        "commands", "bake_set_prefix", "bake_set_vars", "no_cache_env", "clean_dirs",
        "required_env", "login", "env", "env_command", "quiet",
    }
    result: dict[str, dict] = {}
    for step_name, step in raw_steps.items():
        where = f"steps.{step_name}"
        if not isinstance(step_name, str) or not step_name or not isinstance(step, dict):
            _error("[steps.<name>] entries must be non-empty named tables")
        _reject_unknown(step, known_step_keys, where)
        commands = step.get("commands")
        if not isinstance(commands, list) or not commands:
            _error(f"{where}.commands must be a non-empty list")
        for index, command in enumerate(commands):
            command_where = f"{where}.commands[{index}]"
            if not isinstance(command, dict):
                _error(f"{command_where} must be a table")
            _reject_unknown(command, {"label", "argv", "cwd"}, command_where)
            label = _require(command, "label", command_where)
            cwd = _require(command, "cwd", command_where)
            if not isinstance(label, str) or not label.strip() or not isinstance(cwd, str) or not cwd.strip():
                _error(f"{command_where}.label and .cwd must be non-empty strings")
            _string_list(_require(command, "argv", command_where), f"{command_where}.argv")
        for key in ("bake_set_vars", "clean_dirs", "required_env", "env_command"):
            if key in step:
                _string_list(step[key], f"{where}.{key}")
        for key in ("bake_set_prefix", "no_cache_env"):
            if key in step and (not isinstance(step[key], str) or not step[key].strip()):
                _error(f"{where}.{key} must be a non-empty string")
        if not isinstance(step.get("quiet"), bool):
            _error(f"{where}.quiet must be explicitly true or false")
        _scalar_env(step.get("env", {}), f"{where}.env")
        login = step.get("login")
        if login is not None:
            if not isinstance(login, dict):
                _error(f"{where}.login must be a table")
            _reject_unknown(login, {"registry", "username_env", "token_env", "required"}, f"{where}.login")
            for key in ("registry", "username_env", "token_env"):
                value = _require(login, key, f"{where}.login")
                if not isinstance(value, str) or not value.strip():
                    _error(f"{where}.login.{key} must be a non-empty string")
            if not isinstance(login.get("required"), bool):
                _error(f"{where}.login.required must be true or false")
        result[step_name] = step
    return result


def _parse_project_document(config_path: Path) -> tuple[ProjectS2Config, GitHubS2Config, TargetsConfig]:
    raw = _read_toml(config_path, "cmru.toml")
    _reject_unknown(
        raw,
        {"schema_version", "github", "targets", "env", "build_metadata", "project", "steps", "project_metadata"},
        "cmru.toml",
    )
    if raw.get("schema_version") != 1:
        _error("cmru.toml.schema_version must be exactly 1")
    github = _github(raw.get("github"), config_path)
    targets = _targets(raw.get("targets"))
    env = _scalar_env(raw.get("env", {}), "env")
    metadata = _scalar_env(raw.get("build_metadata", {}), "build_metadata")
    if set(metadata) - {"date_env", "date_format"}:
        _error("build_metadata only permits date_env and date_format")
    if "project_metadata" in raw and not isinstance(raw["project_metadata"], dict):
        _error("[project_metadata] must be a table")
    project_raw = raw.get("project")
    if not isinstance(project_raw, dict):
        _error("[project] is required")
    _reject_unknown(
        project_raw,
        {"id", "description", "template_revision", "prefix", "artifacts", "scm_dist", "version",
         "release", "installer", "variants"},
        "project",
    )
    name = _require(project_raw, "id", "project")
    description = _require(project_raw, "description", "project")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        _error("project.id must be a lowercase project identifier")
    if not isinstance(description, str) or not description.strip():
        _error("project.description must be a non-empty string")

    prefix = _require(project_raw, "prefix", "project")
    if not isinstance(prefix, str) or not prefix.strip():
        _error("project.prefix must be a non-empty string")
    artifacts = _parse_artifacts(name, project_raw)
    template_revision = project_raw.get("template_revision")
    if template_revision is not None and (
        not isinstance(template_revision, int) or template_revision < 1
    ):
        _error("project.template_revision must be a positive integer")
    scm_dist = project_raw.get("scm_dist")
    if scm_dist is not None and (not isinstance(scm_dist, str) or not scm_dist.strip()):
        _error("project.scm_dist must be a non-empty string when present")
    version_raw = _require_table(project_raw, "version", "project")
    version = _parse_version(version_raw, name)

    release = project_raw.get("release")
    if not isinstance(release, dict):
        _error("project.release is required and must be a table")
    _reject_unknown(
        release, {"git_tag", "build_step", "commit_generated", "changelog", "artifact_dirs"},
        "project.release",
    )
    if not isinstance(release.get("git_tag"), bool):
        _error("project.release.git_tag is required and must be true or false")
    build_step = release.get("build_step")
    if not isinstance(build_step, str) or not build_step.strip():
        _error("project.release.build_step is required and must name one declared step")
    if "commit_generated" in release:
        _string_list(release["commit_generated"], "project.release.commit_generated")
    artifact_dirs = _string_list(release.get("artifact_dirs", []), "project.release.artifact_dirs")
    for artifact_dir in artifact_dirs:
        candidate = Path(artifact_dir)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.name in ("", "."):
            _error("project.release.artifact_dirs must contain project-relative directories")
    changelog: Optional[str] = "CHANGES.md"
    if "changelog" in release:
        value = release["changelog"]
        if value is False:
            changelog = None
        elif not isinstance(value, str) or not value.strip():
            _error("project.release.changelog must be a non-empty string or false")
        else:
            candidate = Path(value)
            if candidate.is_absolute() or ".." in candidate.parts or candidate.name in ("", "."):
                _error("project.release.changelog must be project-relative")
            changelog = value.strip()

    installer: Optional[InstallerConfig] = None
    if "installer" in project_raw:
        installer = _parse_installer(name, project_raw["installer"])
    variants = _parse_variants(name, project_raw)
    steps = _validate_runner_steps(raw.get("steps"))
    required_steps = {"run-tests", "push"}
    missing_steps = sorted(required_steps - set(steps))
    if missing_steps:
        _error(
            "[steps] must explicitly declare every release phase: "
            f"missing {', '.join(missing_steps)}"
        )
    if build_step not in steps:
        _error(f"project.release.build_step={build_step!r} is not declared in [steps]")
    return (
        ProjectS2Config(
            name=name,
            template_revision=template_revision,
            prefix=prefix.strip(),
            artifacts=artifacts,
            scm_dist=scm_dist.strip() if isinstance(scm_dist, str) else None,
            version=version,
            installer=installer,
            steps=steps,
            changelog=changelog,
            variants=variants,
            description=description.strip(),
            project_root=config_path.parent.resolve(),
            env=env,
            build_metadata=metadata,
            artifact_dirs=artifact_dirs,
            build_step=build_step,
        ),
        github,
        targets,
    )


def _parse_cleanup(raw: object) -> CleanupS2Config:
    if not isinstance(raw, dict):
        _error("[cleanup] is required in cmru.orchestration.toml")
    _reject_unknown(raw, {"max_age_days", "release_tag_prefixes", "keep_release_tags", "ghcr_packages", "ghcr_delete_packages"}, "cleanup")
    if "max_age_days" in raw and (not isinstance(raw["max_age_days"], int) or raw["max_age_days"] <= 0):
        _error("cleanup.max_age_days must be a positive integer")
    required = ("release_tag_prefixes", "keep_release_tags", "ghcr_packages", "ghcr_delete_packages")
    for key in required:
        _string_list(_require(raw, key, "cleanup"), f"cleanup.{key}")
    return CleanupS2Config(
        max_age_days=raw.get("max_age_days"),
        release_tag_prefixes=list(raw["release_tag_prefixes"]),
        keep_release_tags=list(raw["keep_release_tags"]),
        ghcr_packages=list(raw["ghcr_packages"]),
        ghcr_delete_packages=list(raw["ghcr_delete_packages"]),
    )


def _load_project_config(config_path: Path) -> ForgeConfig:
    project, github, targets = _parse_project_document(config_path)
    orchestration = OrchestrationConfig(
        project_order=[project.name], default_projects=[project.name],
        default_steps=["run-tests", "build", "push"], execution_mode="project-first",
        project_configs={project.name: config_path}, dependencies={project.name: []},
    )
    return ForgeConfig(
        github=github, targets=targets, orchestration=orchestration, cleanup=None,
        projects={project.name: project}, repo_root=config_path.parent.resolve(), env=project.env,
    )


def _load_orchestration_config(config_path: Path) -> ForgeConfig:
    raw = _read_toml(config_path, "cmru.orchestration.toml")
    _reject_unknown(raw, {"schema_version", "orchestration", "cleanup"}, "cmru.orchestration.toml")
    if raw.get("schema_version") != 1:
        _error("cmru.orchestration.toml.schema_version must be exactly 1")
    orch_raw = raw.get("orchestration")
    if not isinstance(orch_raw, dict):
        _error("[orchestration] is required")
    _reject_unknown(
        orch_raw,
        {"project_order", "default_projects", "default_steps", "execution_mode", "auth_project", "project"},
        "orchestration",
    )
    for key in ("project_order", "default_projects", "default_steps"):
        _string_list(_require(orch_raw, key, "orchestration"), f"orchestration.{key}")
    execution_mode = _require(orch_raw, "execution_mode", "orchestration")
    if execution_mode not in {"project-first", "step-first"}:
        _error("orchestration.execution_mode must be 'project-first' or 'step-first'")
    entries = orch_raw.get("project")
    if not isinstance(entries, dict) or not entries:
        _error("[orchestration.project.<id>] entries are required")
    docs: dict[str, ProjectS2Config] = {}
    paths: dict[str, Path] = {}
    dependencies: dict[str, List[str]] = {}
    github: Optional[GitHubS2Config] = None
    github_by_project: dict[str, GitHubS2Config] = {}
    targets: Optional[TargetsConfig] = None
    shared_env: Optional[Mapping[str, str]] = None
    for project_id, entry in entries.items():
        where = f"orchestration.project.{project_id}"
        if not isinstance(entry, dict):
            _error(f"[{where}] must be a table")
        _reject_unknown(entry, {"config", "depends_on"}, where)
        config_value = _require(entry, "config", where)
        if not isinstance(config_value, str) or not config_value.strip():
            _error(f"{where}.config must be a non-empty project-relative path")
        config_rel = Path(config_value)
        if config_rel.is_absolute() or ".." in config_rel.parts or config_rel.name != "cmru.toml":
            _error(f"{where}.config must be a project-relative path ending in cmru.toml")
        dependencies[project_id] = _string_list(entry.get("depends_on", []), f"{where}.depends_on")
        project_path = (config_path.parent / config_rel).resolve()
        project, project_github, project_targets = _parse_project_document(project_path)
        if project.name != project_id:
            _error(f"{where}.config declares project.id={project.name!r}, expected {project_id!r}")
        try:
            project_path.parent.relative_to(config_path.parent.resolve())
        except ValueError:
            _error(f"{where}.config resolves outside the orchestration root")
        docs[project_id] = project
        paths[project_id] = project_path
        github_by_project[project_id] = project_github
        if github is None:
            github = project_github
            targets = project_targets
            shared_env = {}
        elif (project_github.owner, project_github.repo, project_github.owner_type) != (github.owner, github.repo, github.owner_type):
            _error("one orchestration run currently requires every project cmru.toml to name the same GitHub release repository")
        elif project_targets != targets:
            _error("one orchestration run currently requires every project cmru.toml to name identical [targets]")
    known = set(docs)
    auth_project = _require(orch_raw, "auth_project", "orchestration")
    if not isinstance(auth_project, str) or auth_project not in known:
        _error("orchestration.auth_project must name one declared project credential contract")
    github = github_by_project[auth_project]
    for field_name, values in (("project_order", orch_raw["project_order"]), ("default_projects", orch_raw["default_projects"])):
        unknown = sorted(set(values) - known)
        if unknown:
            _error(f"orchestration.{field_name} names unknown project(s): {unknown}")
    for project_id, deps in dependencies.items():
        unknown = sorted(set(deps) - known)
        if unknown:
            _error(f"orchestration.project.{project_id}.depends_on names unknown project(s): {unknown}")
        if project_id in deps:
            _error(f"orchestration.project.{project_id}.depends_on must not include itself")
    positions = {project_id: index for index, project_id in enumerate(orch_raw["project_order"])}
    if len(positions) != len(orch_raw["project_order"]):
        _error("orchestration.project_order must not contain duplicate projects")
    for project_id, deps in dependencies.items():
        if project_id not in positions:
            _error(f"orchestration.project_order must include declared project {project_id!r}")
        for dependency in deps:
            if dependency not in positions:
                _error(
                    f"orchestration.project_order must include dependency {dependency!r} "
                    f"of {project_id!r}"
                )
            if positions[dependency] >= positions[project_id]:
                _error(
                    f"orchestration.project.{project_id}.depends_on requires {dependency!r} "
                    "to appear earlier in orchestration.project_order"
                )
    assert github is not None and targets is not None
    return ForgeConfig(
        github=github, targets=targets,
        orchestration=OrchestrationConfig(
            project_order=list(orch_raw["project_order"]),
            default_projects=list(orch_raw["default_projects"]),
            default_steps=list(orch_raw["default_steps"]), execution_mode=str(execution_mode),
            project_configs=paths, dependencies=dependencies,
        ),
        cleanup=_parse_cleanup(raw.get("cleanup")), projects=docs,
        repo_root=config_path.parent.resolve(), env=shared_env or {},
    )


def load_forge_config(config_path: Path, *, require_orchestration: bool = False) -> ForgeConfig:
    """Load exactly one of the two strict CMRU config documents."""
    config_path = config_path.expanduser().resolve()
    if config_path.name == "cmru.toml":
        config = _load_project_config(config_path)
    elif config_path.name == "cmru.orchestration.toml":
        config = _load_orchestration_config(config_path)
    else:
        _error("CMRU configuration must be named cmru.toml or cmru.orchestration.toml")
    if require_orchestration and config_path.name != "cmru.orchestration.toml":
        _error("this estate-level verb requires cmru.orchestration.toml")
    return config
