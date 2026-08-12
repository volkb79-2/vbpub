"""cmru unified config schema (S2) — the one strict reader for ``cmru.toml``.

Every CMRU verb validates this file through this module before it interprets a value.
There is no compatibility parser: retired keys, misspellings, and partial sections are
configuration errors rather than facts silently supplied by defaults.

S2 top-level tables: [github], [orchestration], [targets], [cleanup], [project.<name>]
See docs/SPEC.md S2 for the full schema.
"""
from __future__ import annotations

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
    token: Optional[str]     # may come from env GITHUB_TOKEN


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
class PublishConfig:
    source: str              # glob for artifact file(s)
    latest_json: bool        # whether to emit latest.json


@dataclass(frozen=True)
class ResolveConfig:
    asset_glob: str          # glob to match asset in release


@dataclass(frozen=True)
class InstallerWheel:
    """A bundled wheel entry in the [installer] config (§3.1)."""
    path: str                # glob inside release bundle, e.g. "vendor/cmru-*.whl"
    distribution: str        # pip distribution name, e.g. "cmru"


@dataclass(frozen=True)
class InstallerConfig:
    """[project.<name>.installer] — transactional installer config (§3.1).

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
    artifact: str            # "wheel" | "oci" | "tarball" | "bundle"
    cwd: str                 # build working directory
    scm_dist: Optional[str]  # python dist name (wheel type only)
    version: Optional[VersionConfig]
    publish: Optional[PublishConfig]
    resolve: Optional[ResolveConfig]
    installer: Optional[InstallerConfig]
    steps: Mapping[str, list]
    # Source-first release history defaults to this project-relative document.
    # ``[project.X.release] changelog = false`` is the explicit opt-out.
    changelog: Optional[str] = "CHANGES.md"
    variants: List[VariantConfig] = field(default_factory=list)  # empty ⇒ single-asset (S-REL.6)


@dataclass(frozen=True)
class OrchestrationConfig:
    project_order: List[str]
    default_projects: List[str]
    default_steps: List[str]
    execution_mode: str


@dataclass(frozen=True)
class CleanupS2Config:
    max_age_days: Optional[int]


@dataclass(frozen=True)
class ForgeConfig:
    """Parsed cmru.toml (S2 schema)."""
    github: GitHubS2Config
    targets: TargetsConfig
    orchestration: Optional[OrchestrationConfig]
    cleanup: Optional[CleanupS2Config]
    projects: Mapping[str, ProjectS2Config]
    repo_root: Path          # directory containing the config file


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
    bump = str(raw.get("bump") or "conventional")
    if bump not in ("conventional", "patch"):
        _error(f"{where}.bump must be 'conventional' or 'patch'")
    paths = raw.get("paths") or []
    if not isinstance(paths, list):
        _error(f"{where}.paths must be a list")
    return VersionConfig(strategy=strategy, paths=[str(p) for p in paths], bump=bump)


_VALID_ARTIFACTS = {"wheel", "tarball", "oci-image", "bundle"}


def _parse_artifacts(name: str, raw: dict) -> List[str]:
    """Read the canonical project artifact profile list.

    ``artifact`` and the ``oci`` spelling were retired with the strict S2 contract.
    Their presence is rejected by the project-key validator before this function runs.
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
    """Parse [project.<name>.installer] — fail-fast, unknown keys rejected (V09)."""
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
    """Parse ``[[project.<name>.variants]]`` — fail-fast, unknown keys rejected (V09/V22).

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


def _validate_commands(name: str, step_name: str, raw: object) -> None:
    where = f"project.{name}.steps.{step_name}"
    if not isinstance(raw, dict):
        _error(f"{where} must be a table")
    _reject_unknown(raw, {"commands"}, where)
    commands = _require(raw, "commands", where)
    if not isinstance(commands, list) or not commands:
        _error(f"{where}.commands must be a non-empty list")
    for index, command in enumerate(commands):
        command_where = f"{where}.commands[{index}]"
        if not isinstance(command, dict):
            _error(f"{command_where} must be a table")
        _reject_unknown(command, {"label", "argv", "cwd"}, command_where)
        label = _require(command, "label", command_where)
        cwd = _require(command, "cwd", command_where)
        if not isinstance(label, str) or not label.strip():
            _error(f"{command_where}.label must be a non-empty string")
        if not isinstance(cwd, str) or not cwd.strip():
            _error(f"{command_where}.cwd must be a non-empty string")
        _string_list(_require(command, "argv", command_where), f"{command_where}.argv")


def _validate_project_shape(name: str, raw: dict) -> None:
    """Validate every CMRU-owned project key before a consumer maps it.

    Keep this separate from the dataclass conversion below: ``cli.load_config`` has
    a richer execution model than ``get`` but MUST see the exact same accepted input.
    """
    where = f"project.{name}"
    _reject_unknown(
        raw,
        {
            "prefix", "artifacts", "scm_dist", "cwd", "version", "steps", "release",
            "installer", "variants", "oci", "publish", "resolve", "env",
            "template_revision",
        },
        where,
    )
    for key in ("prefix", "cwd"):
        value = _require(raw, key, where)
        if not isinstance(value, str) or not value.strip():
            _error(f"{where}.{key} must be a non-empty string")
    _parse_artifacts(name, raw)
    if "template_revision" in raw and (
        not isinstance(raw["template_revision"], int) or raw["template_revision"] < 1
    ):
        _error(f"{where}.template_revision must be a positive integer")
    if "scm_dist" in raw and (not isinstance(raw["scm_dist"], str) or not raw["scm_dist"].strip()):
        _error(f"{where}.scm_dist must be a non-empty string")

    version = _require_table(raw, "version", where)
    _parse_version(version, name)

    if "env" in raw and not isinstance(raw["env"], dict):
        _error(f"{where}.env must be a table")

    steps = raw.get("steps", {})
    if not isinstance(steps, dict):
        _error(f"{where}.steps must be a table")
    for step_name, step in steps.items():
        if not isinstance(step_name, str) or not step_name:
            _error(f"{where}.steps has an invalid step name")
        _validate_commands(name, step_name, step)

    release = raw.get("release")
    if release is not None:
        if not isinstance(release, dict):
            _error(f"{where}.release must be a table")
        _reject_unknown(release, {"git_tag", "commit_generated", "changelog"}, f"{where}.release")
        if "git_tag" in release and not isinstance(release["git_tag"], bool):
            _error(f"{where}.release.git_tag must be a boolean")
        if "commit_generated" in release:
            _string_list(release["commit_generated"], f"{where}.release.commit_generated")

    installer = raw.get("installer")
    if installer is not None and not isinstance(installer, dict):
        _error(f"{where}.installer must be a table")

    oci = raw.get("oci")
    if "oci-image" in _parse_artifacts(name, raw) and oci is None:
        _error(f"{where}.oci is required for an oci-image artifact")
    if oci is not None:
        if not isinstance(oci, dict):
            _error(f"{where}.oci must be a table")
        _reject_unknown(
            oci, {"bake_file", "target", "repack", "repack_target_size", "repack_compression"},
            f"{where}.oci",
        )
        for key in ("bake_file", "target", "repack"):
            if key not in oci:
                _error(f"{where}.oci.{key} is required")
        for key in ("bake_file", "target"):
            if not isinstance(oci[key], str) or not oci[key].strip():
                _error(f"{where}.oci.{key} must be a non-empty string")
        if not isinstance(oci["repack"], bool):
            _error(f"{where}.oci.repack must be a boolean")

    publish = raw.get("publish")
    if publish is not None:
        if not isinstance(publish, dict):
            _error(f"{where}.publish must be a table")
        _reject_unknown(publish, {"source", "latest_json"}, f"{where}.publish")
    resolve = raw.get("resolve")
    if resolve is not None:
        if not isinstance(resolve, dict):
            _error(f"{where}.resolve must be a table")
        _reject_unknown(resolve, {"asset_glob"}, f"{where}.resolve")


def _parse_project(name: str, raw: dict, config_dir: Path) -> ProjectS2Config:
    _validate_project_shape(name, raw)

    prefix = str(_require(raw, "prefix", f"project.{name}"))
    artifact = _parse_artifacts(name, raw)[0]   # primary profile (getpy doesn't use it)
    cwd = str(_require(raw, "cwd", f"project.{name}"))
    scm_dist = raw.get("scm_dist")

    version = _parse_version(raw["version"], name)

    publish: Optional[PublishConfig] = None
    if "publish" in raw:
        p = raw["publish"]
        publish = PublishConfig(
            source=str(p.get("source") or ""),
            latest_json=bool(p.get("latest_json", True)),
        )

    resolve: Optional[ResolveConfig] = None
    if "resolve" in raw:
        r = raw["resolve"]
        resolve = ResolveConfig(asset_glob=str(r.get("asset_glob") or "*"))

    installer: Optional[InstallerConfig] = None
    if "installer" in raw:
        installer = _parse_installer(name, raw["installer"])

    changelog: Optional[str] = "CHANGES.md"
    release = raw.get("release")
    if release is not None:
        if not isinstance(release, dict):
            print(f"[ERROR] project.{name}.release must be a table")
            raise SystemExit(exit_codes.CONFIG_ERROR)
        if "changelog" in release:
            value = release["changelog"]
            if value is False:
                changelog = None
            elif not isinstance(value, str) or not value.strip():
                print(
                    f"[ERROR] project.{name}.release.changelog must be a non-empty string or false"
                )
                raise SystemExit(exit_codes.CONFIG_ERROR)
            else:
                candidate = Path(value)
                if (candidate.is_absolute() or ".." in candidate.parts
                        or candidate.name in ("", ".")):
                    print(f"[ERROR] project.{name}.release.changelog must be project-relative")
                    raise SystemExit(exit_codes.CONFIG_ERROR)
                changelog = value.strip()

    steps = raw.get("steps") or {}

    variants = _parse_variants(name, raw)

    return ProjectS2Config(
        name=name,
        template_revision=raw.get("template_revision"),
        prefix=prefix,
        artifact=artifact,
        cwd=cwd,
        scm_dist=str(scm_dist) if scm_dist else None,
        version=version,
        publish=publish,
        resolve=resolve,
        installer=installer,
        steps=steps,
        changelog=changelog,
        variants=variants,
    )


def load_forge_config(config_path: Path, *, require_orchestration: bool = False) -> ForgeConfig:
    """Parse a cmru.toml file.

    ``get`` only needs project publication metadata, while orchestration verbs need
    an explicit release plan and cleanup policy.  The caller states which contract
    it needs; neither path invents missing values.
    """
    if not config_path.exists():
        print(f"[ERROR] Config file not found: {config_path}")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    _reject_unknown(raw, {"github", "targets", "orchestration", "cleanup", "env", "project"}, "cmru.toml")

    # [github]
    gh_raw = raw.get("github")
    if not gh_raw or not isinstance(gh_raw, dict):
        _error("[github] section is required")
    _reject_unknown(gh_raw, {"owner", "repo", "owner_type", "token"}, "github")
    owner = str(_require(gh_raw, "owner", "github"))
    repo = str(_require(gh_raw, "repo", "github"))
    owner_type = str(_require(gh_raw, "owner_type", "github"))
    if not owner.strip() or not repo.strip():
        _error("github.owner and github.repo must be non-empty strings")
    if owner_type not in ("user", "org"):
        _error("github.owner_type must be 'user' or 'org'")
    token = gh_raw.get("token") or None

    github = GitHubS2Config(owner=owner, repo=repo, owner_type=owner_type, token=token)

    # [targets]
    tgt_raw = raw.get("targets")
    if not isinstance(tgt_raw, dict):
        _error("[targets] section is required")
    _reject_unknown(tgt_raw, {"host", "registry"}, "targets")
    host = _require(tgt_raw, "host", "targets")
    if not isinstance(host, str) or not host.strip():
        _error("targets.host must be a non-empty string")
    registry = _require(tgt_raw, "registry", "targets")
    if not isinstance(registry, list) or not all(isinstance(item, str) and item.strip() for item in registry):
        _error("targets.registry must be a list of non-empty strings")
    targets = TargetsConfig(host=host, registry=list(registry))

    # [orchestration] (optional for non-orchestrator use)
    orch_raw = raw.get("orchestration")
    orchestration: Optional[OrchestrationConfig] = None
    if orch_raw is not None:
        if not isinstance(orch_raw, dict):
            _error("[orchestration] must be a table")
        _reject_unknown(
            orch_raw,
            {"project_order", "default_projects", "default_steps", "execution_mode", "step_project_order"},
            "orchestration",
        )
        for key in ("project_order", "default_projects", "default_steps"):
            _string_list(_require(orch_raw, key, "orchestration"), f"orchestration.{key}")
        execution_mode = _require(orch_raw, "execution_mode", "orchestration")
        if execution_mode not in {"project-first", "step-first"}:
            _error("orchestration.execution_mode must be 'project-first' or 'step-first'")
        step_project_order = orch_raw.get("step_project_order", {})
        if not isinstance(step_project_order, dict):
            _error("orchestration.step_project_order must be a table")
        for step, names in step_project_order.items():
            if not isinstance(step, str) or not step:
                _error("orchestration.step_project_order has an invalid step name")
            _string_list(names, f"orchestration.step_project_order.{step}")
        orchestration = OrchestrationConfig(
            project_order=list(orch_raw["project_order"]),
            default_projects=list(orch_raw["default_projects"]),
            default_steps=list(orch_raw["default_steps"]),
            execution_mode=str(execution_mode),
        )

    # [cleanup]
    cleanup_raw = raw.get("cleanup")
    cleanup: Optional[CleanupS2Config] = None
    if cleanup_raw is not None:
        if not isinstance(cleanup_raw, dict):
            _error("[cleanup] must be a table")
        _reject_unknown(
            cleanup_raw,
            {"max_age_days", "release_tag_prefixes", "keep_release_tags", "ghcr_packages", "ghcr_delete_packages"},
            "cleanup",
        )
        for key in ("release_tag_prefixes", "keep_release_tags", "ghcr_packages", "ghcr_delete_packages"):
            if key in cleanup_raw:
                _string_list(cleanup_raw[key], f"cleanup.{key}")
        if "max_age_days" in cleanup_raw and (
            not isinstance(cleanup_raw["max_age_days"], int) or cleanup_raw["max_age_days"] <= 0
        ):
            _error("cleanup.max_age_days must be a positive integer")
        cleanup = CleanupS2Config(max_age_days=cleanup_raw.get("max_age_days"))

    env_raw = raw.get("env", {})
    if not isinstance(env_raw, dict):
        _error("[env] must be a table")
    for key, value in env_raw.items():
        if not isinstance(key, str) or not key or not isinstance(value, (str, int, float, bool)):
            _error("[env] must contain string keys and scalar values")

    # [project.*]
    projects_raw = raw.get("project") or {}
    if not isinstance(projects_raw, dict):
        print("[ERROR] [project] must be a table of project configs")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    projects: dict[str, ProjectS2Config] = {}
    seen_prefixes: set[str] = set()
    for proj_name, proj_raw in projects_raw.items():
        if not isinstance(proj_raw, dict):
            print(f"[ERROR] project.{proj_name} must be a table")
            raise SystemExit(exit_codes.CONFIG_ERROR)
        proj = _parse_project(proj_name, proj_raw, config_path.parent)
        if proj.prefix in seen_prefixes:
            print(f"[ERROR] project.{proj_name}.prefix '{proj.prefix}' is not unique (V05)")
            raise SystemExit(exit_codes.CONFIG_ERROR)
        seen_prefixes.add(proj.prefix)
        projects[proj_name] = proj

    if not projects:
        _error("[project] must declare at least one project")

    if orchestration is not None:
        known_projects = set(projects)
        for field_name, names in (
            ("project_order", orchestration.project_order),
            ("default_projects", orchestration.default_projects),
        ):
            unknown = sorted(set(names) - known_projects)
            if unknown:
                _error(f"orchestration.{field_name} names unknown project(s): {unknown}")
        step_orders = orch_raw.get("step_project_order", {}) if isinstance(orch_raw, dict) else {}
        for step, names in step_orders.items():
            unknown = sorted(set(names) - known_projects)
            if unknown:
                _error(f"orchestration.step_project_order.{step} names unknown project(s): {unknown}")

    if require_orchestration:
        if orchestration is None:
            _error("[orchestration] is required for this CMRU verb")
        if cleanup is None:
            _error("[cleanup] is required for this CMRU verb")
        cleanup_raw = raw["cleanup"]
        for key in (
            "release_tag_prefixes", "keep_release_tags", "ghcr_packages", "ghcr_delete_packages",
        ):
            if key not in cleanup_raw:
                _error(f"cleanup.{key} is required for this CMRU verb")

    return ForgeConfig(
        github=github,
        targets=targets,
        orchestration=orchestration,
        cleanup=cleanup,
        projects=projects,
        repo_root=config_path.parent,
    )
