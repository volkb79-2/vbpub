"""cmru unified config schema (S2) — strict parser for cmru.toml, used by ``cmru get``.

The orchestrator (``cli.py:load_config``) reads the same ``cmru.toml`` via a more
lenient loader that maps it onto the runner model; this module is the strict S2 reader
(full validation) consumed by getpy. Both read one file: cmru.toml, and both accept the
same artifact schema (``artifacts = [...]`` list, ``oci`` → ``oci-image`` alias).

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
class DelegatedConfig:
    sign: bool               # cosign (S7)
    sbom: bool               # syft + grype (S7)
    changelog: bool          # git-cliff (S7)
    nfpm: bool               # nfpm deb/rpm (S7)


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
    prefix: str              # git tag prefix, e.g. "tls-edge-v"
    artifact: str            # "wheel" | "oci" | "tarball" | "bundle"
    cwd: str                 # build working directory
    scm_dist: Optional[str]  # python dist name (wheel type only)
    version: Optional[VersionConfig]
    publish: Optional[PublishConfig]
    resolve: Optional[ResolveConfig]
    installer: Optional[InstallerConfig]
    delegated: Optional[DelegatedConfig]
    steps: Mapping[str, list]
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


def _parse_version(raw: dict, project_name: str) -> VersionConfig:
    strategy = str(_require(raw, "strategy", f"project.{project_name}.version"))
    bump = str(raw.get("bump") or "conventional")
    if bump not in ("conventional", "patch"):
        print(f"[ERROR] project.{project_name}.version.bump must be 'conventional' or 'patch'")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    paths = raw.get("paths") or []
    if not isinstance(paths, list):
        raise SystemExit(exit_codes.CONFIG_ERROR)
    return VersionConfig(strategy=strategy, paths=[str(p) for p in paths], bump=bump)


_ARTIFACT_ALIASES = {"oci": "oci-image"}
_VALID_ARTIFACTS = {"wheel", "tarball", "oci-image", "bundle"}


def _parse_artifacts(name: str, raw: dict) -> List[str]:
    """Resolve a project's artifact profiles, mirroring cli.py's orchestrator loader.

    Accepts the canonical ``artifacts = [...]`` list (with the legacy singular
    ``artifact`` as a fallback) and applies the ``oci`` → ``oci-image`` alias so both
    readers stay in lock-step against one cmru.toml."""
    items = raw.get("artifacts")
    if items is None:
        single = str(raw.get("artifact") or "").strip()
        items = [single] if single else []
    if not isinstance(items, list):
        print(f"[ERROR] project.{name}.artifacts must be a list")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    artifacts = [
        _ARTIFACT_ALIASES.get(str(i).strip(), str(i).strip())
        for i in items if str(i).strip()
    ]
    if not artifacts:
        print(f"[ERROR] project.{name}.artifacts is required (or legacy 'artifact')")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    unknown = [a for a in artifacts if a not in _VALID_ARTIFACTS]
    if unknown:
        print(f"[ERROR] project.{name}.artifacts: unknown {unknown}; "
              f"valid: {sorted(_VALID_ARTIFACTS)} (alias: 'oci'→'oci-image')")
        raise SystemExit(exit_codes.CONFIG_ERROR)
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


def _parse_project(name: str, raw: dict, config_dir: Path) -> ProjectS2Config:
    # V09: reject the retired [getsh] key
    if "getsh" in raw:
        print(
            f"[ERROR] project.{name}.getsh is no longer supported (V09). "
            "Migrate to [project.<name>.installer] — see docs/SPEC.md S2."
        )
        raise SystemExit(exit_codes.CONFIG_ERROR)

    prefix = str(_require(raw, "prefix", f"project.{name}"))
    artifact = _parse_artifacts(name, raw)[0]   # primary profile (getpy doesn't use it)
    cwd = str(_require(raw, "cwd", f"project.{name}"))
    scm_dist = raw.get("scm_dist")

    version: Optional[VersionConfig] = None
    if "version" in raw:
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

    delegated: Optional[DelegatedConfig] = None
    if "delegated" in raw:
        d = raw["delegated"]
        delegated = DelegatedConfig(
            sign=bool(d.get("sign", False)),
            sbom=bool(d.get("sbom", False)),
            changelog=bool(d.get("changelog", False)),
            nfpm=bool(d.get("nfpm", False)),
        )

    steps = raw.get("steps") or {}
    if not isinstance(steps, dict):
        print(f"[ERROR] project.{name}.steps must be a table")
        raise SystemExit(exit_codes.CONFIG_ERROR)

    variants = _parse_variants(name, raw)

    return ProjectS2Config(
        name=name,
        prefix=prefix,
        artifact=artifact,
        cwd=cwd,
        scm_dist=str(scm_dist) if scm_dist else None,
        version=version,
        publish=publish,
        resolve=resolve,
        installer=installer,
        delegated=delegated,
        steps=steps,
        variants=variants,
    )


def load_forge_config(config_path: Path) -> ForgeConfig:
    """Parse a cmru.toml file (S2 schema). Exits with code 2 on config errors."""
    if not config_path.exists():
        print(f"[ERROR] Config file not found: {config_path}")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    # [github]
    gh_raw = raw.get("github")
    if not gh_raw or not isinstance(gh_raw, dict):
        print("[ERROR] [github] section is required")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    owner = str(_require(gh_raw, "owner", "github"))
    repo = str(_require(gh_raw, "repo", "github"))
    owner_type = str(_require(gh_raw, "owner_type", "github"))
    if owner_type not in ("user", "org"):
        print("[ERROR] github.owner_type must be 'user' or 'org'")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    token = gh_raw.get("token") or None

    github = GitHubS2Config(owner=owner, repo=repo, owner_type=owner_type, token=token)

    # [targets]
    tgt_raw = raw.get("targets") or {}
    host = str(tgt_raw.get("host") or "github")
    registry = tgt_raw.get("registry") or []
    if not isinstance(registry, list):
        print("[ERROR] targets.registry must be a list")
        raise SystemExit(exit_codes.CONFIG_ERROR)
    targets = TargetsConfig(host=host, registry=[str(r) for r in registry])

    # [orchestration] (optional for non-orchestrator use)
    orch_raw = raw.get("orchestration")
    orchestration: Optional[OrchestrationConfig] = None
    if orch_raw and isinstance(orch_raw, dict):
        orchestration = OrchestrationConfig(
            project_order=list(orch_raw.get("project_order") or []),
            default_projects=list(orch_raw.get("default_projects") or []),
            default_steps=list(orch_raw.get("default_steps") or []),
            execution_mode=str(orch_raw.get("execution_mode") or "step-first"),
        )

    # [cleanup]
    cleanup_raw = raw.get("cleanup")
    cleanup: Optional[CleanupS2Config] = None
    if cleanup_raw and isinstance(cleanup_raw, dict):
        cleanup = CleanupS2Config(max_age_days=cleanup_raw.get("max_age_days"))

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

    return ForgeConfig(
        github=github,
        targets=targets,
        orchestration=orchestration,
        cleanup=cleanup,
        projects=projects,
        repo_root=config_path.parent,
    )
