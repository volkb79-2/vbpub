#!/usr/bin/env python3
"""
CIU engine - Clean rewrite.

Test-Driven Development approach per CONFIG/DEPLOY refactor plan.

Implementation phases:
- Phase 1-2: Render and parse TOML templates
- Phase 3: Deep merge (key-level, no suffix)
- Phase 4: Auto-generate build metadata only
- Phase 5: Render docker-compose templates

Design Principles:
1. Deep merge (key-level) is DEFAULT - no _merge suffix
2. Jinja2 templates render once into runtime TOML (no *_template expansion)
3. Hooks receive full resolved config - no access restrictions
4. Simple and explicit - behavior documented in code
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import logging
import os
import re
import secrets
import stat
import shutil
import socket
import subprocess
import sys
import tomllib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .config_constants import (
    GLOBAL_CONFIG_DEFAULTS,
    GLOBAL_CONFIG_OVERRIDES,
    GLOBAL_CONFIG_RENDERED,
    STACK_CONFIG_DEFAULTS,
    STACK_CONFIG_OVERRIDES,
    STACK_CONFIG_RENDERED,
    DOCKER_COMPOSE_OUTPUT,
)
from .cli_utils import get_cli_version
from .workspace_env import (
    WorkspaceEnvError,
    bootstrap_workspace_env,
    generate_ciu_env,
    ensure_workspace_network,
    resolve_env_root,
    detect_standalone_root,
)


# Global logger instance (configured after parsing config)
logger = logging.getLogger(__name__)


def check_runtime_dependencies() -> None:
    """
    Validate that required runtime dependencies are installed.
    """
    # Allow tests to bypass dependency checking
    if os.getenv('SKIP_DEPENDENCY_CHECK') == '1':
        return

    print("[INFO] Validating runtime dependencies...", flush=True)
    missing_deps = []
    warnings = []

    # Check Python built-ins (should always work)
    try:
        import tomllib  # noqa: F401
        import pathlib  # noqa: F401
        import subprocess  # noqa: F401
        import json  # noqa: F401
        import hashlib  # noqa: F401
    except ImportError as e:
        missing_deps.append(('python-stdlib', f'Python standard library ({e.name})',
                             'Upgrade Python to 3.11+'))

    # Check docker
    try:
        result = subprocess.run(
            ['docker', '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result and result.returncode != 0:
            missing_deps.append(('docker', 'Docker Engine', 'https://docs.docker.com/engine/install/'))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing_deps.append(('docker', 'Docker Engine', 'https://docs.docker.com/engine/install/'))

    # Check docker compose (v2 CLI)
    try:
        result = subprocess.run(
            ['docker', 'compose', 'version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result and result.returncode != 0:
            missing_deps.append(('docker compose', 'Docker Compose v2', 'https://docs.docker.com/compose/install/'))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing_deps.append(('docker compose', 'Docker Compose v2', 'https://docs.docker.com/compose/install/'))

    # Check hvac (Vault client) - WARNING only
    try:
        import hvac  # noqa: F401 - Import check only
    except ImportError:
        warnings.append(('hvac', 'Vault client library', 'pip install hvac',
                'Required for ASK_VAULT: and GEN_TO_VAULT: secret directives'))

    # Check jinja2 (template rendering) - CRITICAL
    try:
        import jinja2  # noqa: F401 - Import check only
    except ImportError:
        missing_deps.append(('jinja2', 'Jinja2 template engine', 'pip install jinja2'))

    # Check tomli_w (TOML writing) - CRITICAL
    try:
        import tomli_w  # noqa: F401 - Import check only
    except ImportError:
        missing_deps.append(('tomli_w', 'TOML writer library', 'pip install tomli_w'))

    # Report missing dependencies (CRITICAL)
    if missing_deps:
        print("[ERROR] Missing required dependencies:", flush=True)
        for cmd, name, install_info in missing_deps:
            print(f"  ❌ {name} ({cmd})", flush=True)
            print(f"     Install: {install_info}", flush=True)
        print("\n[ERROR] Cannot continue without required dependencies", flush=True)
        sys.exit(1)

    # Report warnings (OPTIONAL)
    if warnings:
        print("[WARN] Optional dependencies missing:", flush=True)
        for cmd, name, install_cmd, note in warnings:
            print(f"  ⚠️  {name} ({cmd})", flush=True)
            print(f"     Install: {install_cmd}", flush=True)
            print(f"     Note: {note}", flush=True)
        print("", flush=True)  # Blank line after warnings


def configure_logging(log_level: str = "INFO") -> None:
    """
    Configure logging module with specified level.
    """
    # Map string to logging level
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR
    }

    level = level_map.get(log_level.upper(), logging.INFO)

    # Configure root logger
    logging.basicConfig(
        level=level,
        format='[%(levelname)s] %(message)s',
        force=True  # Reconfigure if already configured
    )

    # Set module logger level
    logger.setLevel(level)

    if level == logging.DEBUG:
        logger.info(f"Logging configured: {log_level.upper()} (comprehensive tracing enabled)")
    else:
        logger.info(f"Logging configured: {log_level.upper()}")



def parse_arguments(argv: Optional[list] = None) -> argparse.Namespace:
    """
    Parse command-line arguments for CIU.

    Supports arguments:
    1. -d, --dir <path> - Working directory (default: current directory)
    2. -f, --file <name> - Compose file name (default: docker-compose.yml.j2)
    3. --dry-run - Skip docker compose execution (for testing/debugging)
    4. --print-context - Print merged config as JSON (debugging)
    5. -y, --yes - Non-interactive mode (auto-confirm prompts)
    6. --reset - Clean service to fresh state before starting
    7. --render-toml - Render ciu.toml from templates (resets state)
    8. --define-root <path> - Override repository root (no parent walking)
    9. --root-folder <path> - Alias for --define-root
    10. --skip-hostdir-check - Skip hostdir creation/validation
    11. --skip-hooks - Skip pre/post compose hooks
    12. --skip-secrets - Skip secret resolution/validation
    13. --auto-connect-network / --no-auto-connect-network - Override devcontainer network auto-connect
    """
    parser = argparse.ArgumentParser(
        description='CIU: TOML-based Docker Compose orchestration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Start service in current directory
  %(prog)s

  # Start service in specific directory
  %(prog)s -d /srv/postgres

  # Dry run with context printing (debugging)
  %(prog)s --dry-run --print-context

  # Reset service to fresh state (automated)
  %(prog)s --reset -y

  # Use custom compose file name
  %(prog)s -f custom-compose.yml.j2

Shared functionality:
    - Reads .env.ciu (REPO_ROOT, PHYSICAL_REPO_ROOT, DOCKER_NETWORK_INTERNAL).
    - Ensures .env.ciu is generated when missing (use --generate-env to refresh).
        - Renders TOML with INSTANCE_ID-based values and (when enabled) connects
            devcontainers to DOCKER_NETWORK_INTERNAL before running compose actions.
        '''
    )

    parser.add_argument(
        '-d', '--dir',
        type=Path,
        default=Path.cwd(),
        metavar='PATH',
        help='Working directory containing service files (default: current directory)'
    )

    parser.add_argument(
        '-f', '--file',
        type=str,
        default='docker-compose.yml.j2',
        metavar='NAME',
        help='Compose file name (default: docker-compose.yml.j2)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Skip docker compose execution (useful for testing/debugging)'
    )

    parser.add_argument(
        '--print-context',
        action='store_true',
        help='Print merged configuration as JSON (debugging)'
    )

    parser.add_argument(
        '--render-toml',
        action='store_true',
        help='Render ciu.toml from templates (resets state)'
    )

    parser.add_argument(
        '--define-root',
        type=Path,
        default=None,
        metavar='PATH',
        help='Override repository root directory (no parent walking)'
    )

    parser.add_argument(
        '--root-folder',
        dest='define_root',
        type=Path,
        default=None,
        metavar='PATH',
        help='Alias for --define-root'
    )

    parser.add_argument(
        '--skip-hostdir-check',
        action='store_true',
        help='Skip hostdir creation/validation (cleanup mode)'
    )

    parser.add_argument(
        '--skip-hooks',
        action='store_true',
        help='Skip pre/post compose hooks (cleanup mode)'
    )

    parser.add_argument(
        '--skip-secrets',
        action='store_true',
        help='Skip secret resolution/validation (cleanup mode)'
    )

    network_group = parser.add_mutually_exclusive_group()
    network_group.add_argument(
        '--auto-connect-network',
        dest='auto_connect_network',
        action='store_true',
        default=None,
        help='Auto-connect devcontainer to DOCKER_NETWORK_INTERNAL (override config)'
    )
    network_group.add_argument(
        '--no-auto-connect-network',
        dest='auto_connect_network',
        action='store_false',
        default=None,
        help='Disable devcontainer network auto-connect (override config)'
    )

    parser.add_argument(
        '--generate-env',
        action='store_true',
        help='Generate .env.ciu with autodetected values'
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f"ciu {get_cli_version()}"
    )

    parser.add_argument(
        '--update-cert-permission',
        action='store_true',
        help='Update Let\'s Encrypt cert permissions (requires root)'
    )

    parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Non-interactive mode (auto-confirm all prompts)'
    )

    parser.add_argument(
        '--reset',
        action='store_true',
        help='Clean service to fresh state (remove containers, volumes, configs)'
    )

    return parser.parse_args(argv)


def parse_toml(file_path: str) -> dict:
    """
    Phase 1 & 2: Parse TOML configuration file.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"TOML file not found: {file_path}")

    with open(path, 'rb') as f:
        return tomllib.load(f)


def parse_toml_string(toml_text: str, source: str) -> dict:
    """
    Parse TOML from a string with fail-fast error context.
    """
    try:
        return tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(
            f"[ERROR] Failed to parse TOML from {source}\n"
            f"[ERROR] TOML syntax error: {e}"
        ) from e


ENV_VAR_PATTERN = re.compile(r"\$(\w+)|\$\{([^}]+)\}")


def expand_env_vars_or_fail(raw_text: str, source: str) -> str:
    """
    Expand $VAR / ${VAR} using os.environ; fail-fast on missing values.
    """
    missing = set()

    def _replace(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        value = os.environ.get(var_name)
        if value is None or value == "":
            missing.add(var_name)
            return match.group(0)
        return value

    expanded = ENV_VAR_PATTERN.sub(_replace, raw_text)

    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(
            f"[ERROR] Missing required environment values in {source}: {missing_list}.\n"
            "[ERROR] .env.ciu is authoritative. Run ciu --generate-env "
            "and source .env.ciu before running CIU."
        )

    leftover = ENV_VAR_PATTERN.search(expanded)
    if leftover:
        raise ValueError(
            f"[ERROR] Unresolved environment placeholders remain in {source}: {leftover.group(0)}\n"
            "[ERROR] Ensure all required values are set in .env.ciu."
        )

    return expanded


def walk_up_tree_for_globals(start_path: Path | str) -> list[Path]:
    """
    Walk up directory tree and collect global config files.

    Returns a list ordered from root -> leaf for deterministic merges.
    """
    path = Path(start_path).resolve()
    if path.is_file():
        path = path.parent

    globals_found: list[Path] = []

    current = path
    while True:
        candidate = current / GLOBAL_CONFIG_RENDERED
        if candidate.exists():
            globals_found.append(candidate)

        if current.parent == current:
            break

        current = current.parent

    return list(reversed(globals_found))


def _stringify_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _validate_required_certs(config: dict) -> None:
    ciu_config = config.get("ciu", {})
    if not ciu_config.get("require_certs", False):
        return

    fqdn = os.environ.get("PUBLIC_FQDN") or ciu_config.get("public_fqdn")
    letsencrypt_dir = os.environ.get("PUBLIC_TLS_CRT_PEM")
    letsencrypt_dir = Path(letsencrypt_dir).parent.parent if letsencrypt_dir else None
    if not fqdn or not letsencrypt_dir:
        raise ValueError(
            "[ERROR] ciu.require_certs is true but PUBLIC_FQDN or PUBLIC_TLS_CRT_PEM is missing."
        )

    docker_gid_value = os.environ.get("DOCKER_GID")
    if not docker_gid_value:
        raise ValueError("[ERROR] DOCKER_GID is required to validate cert permissions.")

    try:
        docker_gid = int(docker_gid_value)
    except ValueError as exc:
        raise ValueError(f"[ERROR] DOCKER_GID is not an integer: {docker_gid_value}") from exc

    cert_path = Path(letsencrypt_dir) / "live" / fqdn / "fullchain.pem"
    key_path = Path(letsencrypt_dir) / "live" / fqdn / "privkey.pem"

    missing = [path for path in (cert_path, key_path) if not path.exists()]
    if missing:
        missing_list = ", ".join(str(path) for path in missing)
        raise ValueError(
            "[ERROR] Required TLS certificates are missing: "
            f"{missing_list}.\n"
            "[ERROR] Provision certificates or set reverse_proxy.require_certs = false."
        )

    def _is_readable_for_gid(path: Path) -> bool:
        mode = path.stat().st_mode
        if mode & stat.S_IROTH:
            return True
        if path.stat().st_gid == docker_gid and (mode & stat.S_IRGRP):
            return True
        return False

    for path in (cert_path, key_path):
        if not _is_readable_for_gid(path):
            raise ValueError(
                "[ERROR] TLS certificate not readable for DOCKER_GID. "
                f"Path: {path}.\n"
                "[ERROR] Run ciu-deploy --update-cert-permission or adjust host permissions."
            )


def _validate_required_fqdn(config: dict) -> None:
    ciu_config = config.get("ciu", {})
    if not ciu_config.get("require_fqdn", True):
        return
    if not os.environ.get("PUBLIC_FQDN"):
        raise ValueError(
            "[ERROR] ciu.require_fqdn is true but PUBLIC_FQDN is empty."
        )


def flatten_dict(data: dict, parent_key: str = "", sep: str = "_", prefix: str | None = None) -> dict:
    """
    Flatten nested dict into ENV_VAR-style keys (uppercased).
    """
    items: dict[str, str] = {}

    if prefix and not parent_key:
        parent_key = prefix

    for key, value in data.items():
        if key == "env" and isinstance(value, dict):
            for env_key, env_val in value.items():
                items[f"ENV_{env_key}"] = _stringify_env_value(env_val)
            continue

        new_key = f"{parent_key}{sep}{key}" if parent_key else str(key)

        if isinstance(value, dict):
            items.update(flatten_dict(value, new_key, sep=sep))
        elif isinstance(value, list):
            joined = ",".join(_stringify_env_value(item) for item in value)
            items[new_key.upper()] = joined
        else:
            items[new_key.upper()] = _stringify_env_value(value)

    return items


def build_compose_env(config: dict, base_env: Optional[dict] = None) -> dict:
    """
    Build docker compose environment from config and existing env.
    """
    env = dict(base_env or os.environ)
    env.update(flatten_dict(config))
    env.setdefault("PWD", os.getcwd())
    return env


def build_template_context(config: dict) -> dict:
    """
    Build Jinja2 template context with config + env.
    """
    return {
        **config,
        "env": dict(os.environ),
    }


def render_toml_template(template_path: Path, context: dict) -> dict:
    """
    Render a TOML Jinja2 template, expand env vars, and parse.
    """
    rendered = render_jinja2(str(template_path), build_template_context(context))
    expanded = expand_env_vars_or_fail(rendered, str(template_path))
    return parse_toml_string(expanded, str(template_path))


def ensure_override_template(defaults_path: Path, overrides_path: Path) -> None:
    """
    Ensure the override template exists by copying defaults if missing.
    """
    if overrides_path.exists():
        return

    if not defaults_path.exists():
        return

    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(defaults_path, overrides_path)
    print(
        f"[INFO] Created override template from defaults: {overrides_path}",
        flush=True
    )


def write_rendered_toml(output_path: Path, config: dict) -> None:
    """
    Write rendered TOML to disk using tomli_w.
    """
    import tomli_w

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'wb') as f:
        tomli_w.dump(config, f)


def deep_merge_configs(global_config: dict, project_config: dict) -> dict:
    """
    Phase 3: Deep merge global and project configs (key-level merge).
    """
    logger.debug("Starting deep merge...")
    logger.debug(f"  Global config keys: {list(global_config.keys())}")
    logger.debug(f"  Project config keys: {list(project_config.keys())}")

    # Start with a copy of global config
    result = global_config.copy()

    overrides_count = 0
    new_keys_count = 0

    for key, value in project_config.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Both are dicts - recursively merge
            logger.debug(f"  Merging nested dict: {key}")
            result[key] = deep_merge_configs(result[key], value)
        else:
            # Override with project value (base case)
            # This handles: scalars, lists, and new keys
            if key in result:
                logger.debug(f"  Override: {key} = {value} (was: {result[key]})")
                overrides_count += 1
            else:
                logger.debug(f"  New key: {key} = {value}")
                new_keys_count += 1
            result[key] = value
    logger.debug(f"Deep merge complete: {overrides_count} overrides, {new_keys_count} new keys")
    return result


def render_global_config_chain(working_dir: Path, repo_root_override: Optional[Path] = None) -> dict:
    """
    Render and merge global config templates from repo root to working_dir.
    """
    if repo_root_override:
        repo_root = repo_root_override.resolve()
        env_repo_root = os.environ.get("REPO_ROOT")
        if env_repo_root and Path(env_repo_root).resolve() != repo_root:
            raise ValueError(
                f"[ERROR] --define-root ({repo_root}) does not match REPO_ROOT ({env_repo_root}). "
                "Update .env.ciu or use a matching --define-root."
            )
    else:
        repo_root = Path(os.environ.get("REPO_ROOT", "")).resolve() if os.environ.get("REPO_ROOT") else None

    if not repo_root or not repo_root.exists():
        raise ValueError(
            "[ERROR] REPO_ROOT not set or invalid. "
            "Ensure .env.ciu is loaded before running CIU."
        )

    merged: dict = {}
    # Build a deterministic chain from repo root down to the working directory.
    # This allows nested repos (subtrees) to override globals without
    # accidentally pulling in configs from unrelated parent paths.
    dirs = [repo_root] + [p for p in working_dir.resolve().parents if repo_root in p.parents or p == repo_root]
    dirs = list(reversed(dirs))

    for directory in dirs:
        defaults_path = directory / GLOBAL_CONFIG_DEFAULTS
        overrides_path = directory / GLOBAL_CONFIG_OVERRIDES

        if overrides_path.exists() and not defaults_path.exists():
            raise ValueError(
                f"[ERROR] Found {GLOBAL_CONFIG_OVERRIDES} without {GLOBAL_CONFIG_DEFAULTS} in {directory}"
            )

        if defaults_path.exists():
            ensure_override_template(defaults_path, overrides_path)
            defaults_config = render_toml_template(defaults_path, merged)
            merged = deep_merge_configs(merged, defaults_config)

        if overrides_path.exists():
            overrides_config = render_toml_template(overrides_path, merged)
            merged = deep_merge_configs(merged, overrides_config)

    if not merged:
        raise ValueError(
            f"[ERROR] No global configuration found. Expected {GLOBAL_CONFIG_DEFAULTS} at repo root."
        )

    output_path = repo_root / GLOBAL_CONFIG_RENDERED
    write_rendered_toml(output_path, merged)
    return merged


def render_stack_config(working_dir: Path, global_config: dict, preserve_state: bool) -> dict:
    """
    Render stack CIU templates into ciu.toml and merge with global config.
    """
    defaults_path = working_dir / STACK_CONFIG_DEFAULTS
    overrides_path = working_dir / STACK_CONFIG_OVERRIDES
    output_path = working_dir / STACK_CONFIG_RENDERED

    if not defaults_path.exists():
        raise FileNotFoundError(f"{STACK_CONFIG_DEFAULTS} not found in {working_dir}")

    ensure_override_template(defaults_path, overrides_path)

    defaults_config = render_toml_template(defaults_path, global_config)
    merged_stack = defaults_config

    if overrides_path.exists():
        overrides_config = render_toml_template(overrides_path, deep_merge_configs(global_config, defaults_config))
        merged_stack = deep_merge_configs(merged_stack, overrides_config)

    if preserve_state and output_path.exists():
        existing = parse_toml(str(output_path))
        if isinstance(existing.get('state'), dict):
            merged_stack['state'] = existing['state']

    write_rendered_toml(output_path, merged_stack)
    return merged_stack


def get_git_hash() -> str:
    """
    Get current git commit hash (short, 8 chars).
    """
    try:
        # Get short hash
        result = subprocess.run(
            ['git', 'rev-parse', '--short=8', 'HEAD'],
            capture_output=True,
            text=True,
            check=True
        )
        git_hash = result.stdout.strip()

        # Check if working directory is dirty
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            check=True
        )
        is_dirty = len(result.stdout.strip()) > 0

        return f"{git_hash}{'-dirty' if is_dirty else ''}"

    except (subprocess.CalledProcessError, FileNotFoundError):
        return 'dev'


def get_timestamp() -> str:
    """
    Get current timestamp in ISO 8601 format (UTC).
    """
    return datetime.now(timezone.utc).isoformat()


def extract_service_definitions(config: dict, working_dir: Path) -> dict:
    """
    Extract service definition from global [service.X.Y.Z] based on current directory.
    """
    # Get repo root from config
    repo_path = config.get('ciu', {}).get('repo_root')
    if not repo_path:
        print("[WARN] ciu.repo_root not set, skipping service extraction")
        return config

    repo_root = Path(repo_path).resolve()
    working_resolved = working_dir.resolve()

    # Calculate relative path from repo root
    try:
        relative_path = working_resolved.relative_to(repo_root)
    except ValueError:
        # working_dir not under repo_root
        print(f"[WARN] {working_dir} not under repo root {repo_root}, skipping service extraction")
        return config

    # Split path into components (normalize to snake_case keys)
    path_parts = [part.replace('-', '_') for part in relative_path.parts]
    if not path_parts:
        print("[WARN] Empty relative path, skipping service extraction")
        return config

    # Navigate to [service.X.Y.Z...] in global config
    service_section = config.get('service', {})
    current = service_section

    for part in path_parts[:-1]:  # Navigate to parent (e.g., 'applications')
        if part not in current:
            # No matching service definition
            print(f"[INFO] No service definition for path: {relative_path}")
            return config
        current = current[part]

    # Extract the final service definition
    service_name = path_parts[-1]  # e.g., 'controller'
    if service_name not in current:
        print(f"[INFO] No service definition for: {relative_path}")
        return config

    service_def = current[service_name]

    # Move to top-level key (e.g., config['controller'])
    print(f"[INFO] Extracting service definition: service.{'.'.join(path_parts)} → {service_name}")

    # Deep merge with existing top-level section (project override)
    if service_name in config:
        config[service_name] = deep_merge_configs(service_def, config[service_name])
    else:
        config[service_name] = service_def

    return config


def auto_generate_values(config: dict) -> dict:
    """
    Phase 4: Auto-generate build metadata only.
    """
    logger.debug("Auto-generating runtime values...")

    # Build metadata (always generated)
    config.setdefault('auto_generated', {})
    config['auto_generated']['build_version'] = get_git_hash()
    config['auto_generated']['build_time'] = get_timestamp()
    logger.debug(f"  Build version: {config['auto_generated']['build_version']}")
    logger.debug(f"  Build time: {config['auto_generated']['build_time']}")

    # Preserve workspace-derived UID/GID for hostdir creation
    deploy_shared = config.get('deploy', {}).get('env', {}).get('shared', {})
    container_uid = deploy_shared.get('CONTAINER_UID')
    container_gid = deploy_shared.get('CONTAINER_GID')
    docker_gid = deploy_shared.get('DOCKER_GID')

    if not container_uid or not docker_gid:
        raise ValueError(
            "[ERROR] Missing required deploy.env.shared values for hostdir ownership. "
            "Ensure CONTAINER_UID and DOCKER_GID are set via .env.ciu."
        )

    config['auto_generated']['uid'] = container_uid
    config['auto_generated']['gid'] = container_gid or docker_gid
    config['auto_generated']['docker_gid'] = docker_gid
    logger.debug(f"  UID (from workspace): {config['auto_generated']['uid']}")
    logger.debug(f"  GID (from workspace): {config['auto_generated']['gid']}")
    logger.debug(f"  DOCKER_GID (from workspace): {config['auto_generated']['docker_gid']}")

    return config


def create_hostdirs(config: dict) -> dict:
    """
    Phase 4.5: Create host-mounted volume directories.
    """
    deploy_shared = config.get('deploy', {}).get('env', {}).get('shared', {})
    uid = deploy_shared.get('CONTAINER_UID') or config.get('auto_generated', {}).get('uid')
    docker_gid = deploy_shared.get('DOCKER_GID') or config.get('auto_generated', {}).get('docker_gid')

    if not uid or not docker_gid:
        raise ValueError(
            "CONTAINER_UID/DOCKER_GID not found in config - "
            "ensure .env.ciu is loaded before running CIU"
        )

    uid = int(uid)
    docker_gid = int(docker_gid)

    print(f"[INFO] Scanning for volume directories (UID:{uid}, GID:{docker_gid})...", flush=True)

    created_count = 0

    def _maybe_create(path_value: str) -> None:
        nonlocal created_count

        path_obj = Path(path_value)
        existed = path_obj.exists()

        try:
            path_obj.mkdir(mode=0o775, parents=True, exist_ok=True)
            if existed:
                if not path_obj.is_dir():
                    print(f"[ERROR] Path exists and is not a directory: {path_value}", flush=True)
                    raise SystemExit(1)

                stat_info = path_obj.stat()
                mode = stat.S_IMODE(stat_info.st_mode)

                if stat_info.st_uid == uid:
                    try:
                        os.chown(path_obj, uid, docker_gid)
                    except PermissionError as e:
                        print(f"[ERROR] Permission denied updating group for {path_value}: {e}", flush=True)
                        raise SystemExit(1)
                elif stat_info.st_gid == docker_gid and (mode & 0o020):
                    print(
                        f"[INFO]   Exists with compatible permissions: {path_value} "
                        f"(owner={stat_info.st_uid}, group={stat_info.st_gid}, mode={oct(mode)})",
                        flush=True,
                    )
                    return
                else:
                    print(
                        f"[ERROR] Existing directory has incompatible ownership or permissions: {path_value} "
                        f"(owner={stat_info.st_uid}, group={stat_info.st_gid}, mode={oct(mode)})",
                        flush=True,
                    )
                    print(
                        "[ERROR] Fix ownership or permissions, then retry.",
                        flush=True,
                    )
                    raise SystemExit(1)

            os.chown(path_obj, uid, docker_gid)
            print(f"[INFO]   Created: {path_value} ({uid}:{docker_gid}, 775)", flush=True)
            created_count += 1
        except PermissionError as e:
            print(f"[ERROR] Permission denied creating {path_value}: {e}", flush=True)
            print(f"[ERROR] Ensure you are member of docker group (GID {docker_gid})", flush=True)
            raise SystemExit(1)
        except Exception as e:
            print(f"[ERROR] Failed to create {path_value}: {e}", flush=True)
            raise SystemExit(1)

    def _scan_section(section_value: dict) -> None:
        if not isinstance(section_value, dict):
            return

        hostdir = section_value.get('hostdir')
        if isinstance(hostdir, dict):
            service_name = section_value.get('name')
            if not service_name:
                raise ValueError(
                    "[ERROR] hostdir section found without service name. "
                    "Add 'name' to the parent section so CIU can generate hostdir paths."
                )

            for purpose, vol_path in hostdir.items():
                if not isinstance(vol_path, str):
                    continue

                if not vol_path:
                    generated = f"./vol-{service_name}-{purpose}"
                    hostdir[purpose] = generated
                    _maybe_create(generated)
                    continue

                _maybe_create(vol_path)

        for value in section_value.values():
            if isinstance(value, dict):
                _scan_section(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _scan_section(item)

    # Scan config recursively for hostdir sections
    _scan_section(config)

    if created_count > 0:
        print(f"[INFO] Created {created_count} volume directories", flush=True)
    else:
        print("[INFO] No volume directories to create", flush=True)

    return config


def reset_service(config: dict, working_dir: Path, compose_file: str, yes: bool) -> None:
    """
    Reset service to fresh state (service-scoped cleanup).
    """
    deployment = config.get('deploy', {})
    project_name = deployment.get('project_name')
    labels = deployment.get('labels', {})
    label_prefix = labels.get('prefix')

    if not project_name:
        raise ValueError("deploy.project_name is required for reset")
    if not label_prefix:
        raise ValueError("deploy.labels.prefix is required for reset")

    service_name = working_dir.resolve().name

    print(f"[INFO] Resetting service: {service_name} (project: {project_name})", flush=True)

    # Step 1: docker compose down -v
    try:
        print("[INFO]   Step 1/4: Stopping containers and removing volumes...", flush=True)
        result = subprocess.run(
            ['docker', 'compose', '-f', compose_file, 'down', '-v'],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            print(f"[WARN]   docker compose down failed (may be OK if no containers): {result.stderr}", flush=True)
        else:
            print("[INFO]   Containers stopped, volumes removed", flush=True)

    except Exception as e:
        print(f"[ERROR] Failed to run docker compose down: {e}", flush=True)
        raise SystemExit(1)

    # Step 2: Remove vol-* directories
    try:
        print("[INFO]   Step 2/4: Removing host-mounted volume directories...", flush=True)
        removed_count = 0

        for vol_dir in Path('.').glob('vol-*'):
            if vol_dir.is_dir():
                shutil.rmtree(vol_dir)
                print(f"[INFO]     Removed: {vol_dir}", flush=True)
                removed_count += 1

        if removed_count > 0:
            print(f"[INFO]   Removed {removed_count} volume directories", flush=True)
        else:
            print("[INFO]   No volume directories to remove", flush=True)

    except Exception as e:
        print(f"[ERROR] Failed to remove volume directories: {e}", flush=True)
        raise SystemExit(1)

    # Step 3: Remove generated config files
    try:
        print("[INFO]   Step 3/4: Removing generated configuration files...", flush=True)
        files_to_remove = [
            Path('docker-compose.yml'),
            Path(STACK_CONFIG_RENDERED)
        ]

        removed_count = 0
        for file_path in files_to_remove:
            if file_path.exists():
                file_path.unlink()
                print(f"[INFO]     Removed: {file_path}", flush=True)
                removed_count += 1

        if removed_count > 0:
            print(f"[INFO]   Removed {removed_count} configuration files", flush=True)
        else:
            print("[INFO]   No configuration files to remove", flush=True)

    except Exception as e:
        print(f"[ERROR] Failed to remove configuration files: {e}", flush=True)
        raise SystemExit(1)

    # Step 4: Clean orphaned containers (by service label)
    try:
        print("[INFO]   Step 4/4: Cleaning orphaned containers...", flush=True)

        label_filter = f"label={label_prefix}.component={service_name}"

        result = subprocess.run(
            ['docker', 'ps', '-a', '--filter', label_filter, '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0 and result.stdout.strip():
            container_names = [name.strip() for name in result.stdout.strip().split('\n') if name.strip()]

            if container_names:
                print(f"[INFO]     Found {len(container_names)} orphaned containers", flush=True)

                for container_name in container_names:
                    rm_result = subprocess.run(
                        ['docker', 'rm', '-f', container_name],
                        capture_output=True,
                        text=True,
                        check=False
                    )

                    if rm_result.returncode == 0:
                        print(f"[INFO]       Removed: {container_name}", flush=True)
                    else:
                        print(f"[WARN]       Failed to remove {container_name}: {rm_result.stderr}", flush=True)
            else:
                print("[INFO]   No orphaned containers found", flush=True)
        else:
            print("[INFO]   No orphaned containers found", flush=True)

    except Exception as e:
        print(f"[WARN] Failed to clean orphaned containers: {e}", flush=True)
        # Don't fail - orphaned container cleanup is optional

    print(f"[INFO] Reset complete for service: {service_name}", flush=True)


def get_nested_value(obj: dict, path: str) -> Any:
    """
    Get nested value from dict using dot notation.
    """
    # Handle simple field name (no dots)
    if '.' not in path:
        return obj.get(path, '')

    # Handle nested path
    parts = path.split('.')
    current = obj

    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return ''

    return current


def collect_secret_directives(config: dict) -> dict:
    """Collect Vault-related secret directive paths from config."""
    directives = {
        'ask': set(),
        'gen_to_vault': set(),
        'ask_once': set(),
        'local': set(),
        'external': set(),
        'derive': set(),
        'ephemeral': set(),
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, str):
            return

        if value.startswith('ASK_VAULT:'):
            directives['ask'].add(value[10:])
        elif value.startswith('ASK_VAULT_ONCE:'):
            directives['ask_once'].add(value[15:])
        elif value.startswith('GEN_TO_VAULT:'):
            directives['gen_to_vault'].add(value[13:])
        elif value.startswith('GEN_LOCAL:'):
            directives['local'].add(value[10:])
        elif value.startswith('ASK_EXTERNAL:'):
            directives['external'].add(value[13:])
        elif value.startswith('DERIVE:'):
            directives['derive'].add(value)
        elif value == 'GEN_EPHEMERAL':
            directives['ephemeral'].add('GEN_EPHEMERAL')
        else:
            if re.match(r'^[A-Z][A-Z0-9_]+:', value):
                raise ValueError(
                    f"Unrecognized secret directive: {value!r}. "
                    f"Known directives: ASK_VAULT:, ASK_VAULT_ONCE:, GEN_TO_VAULT:, "
                    f"GEN_LOCAL:, ASK_EXTERNAL:, DERIVE:, GEN_EPHEMERAL"
                )

    walk(config)
    return directives


def build_vault_addr(config: dict) -> str:
    """Build Vault address from topology/services config."""
    services = config.get('topology', {}).get('services', {})
    vault_service = services.get('vault', {})
    host = vault_service.get('internal_host')
    port = vault_service.get('internal_port')

    if not host or not port:
        raise ValueError(
            "[ERROR] topology.services.vault is missing internal_host/internal_port in merged config"
        )

    return f"http://{host}:{port}"


def _vault_request_json(method: str, url: str, token: str, payload: Optional[dict] = None) -> dict:
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('X-Vault-Token', token)
    req.add_header('Content-Type', 'application/json')

    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read().decode('utf-8')
        if not body:
            return {}
        return json.loads(body)


def vault_kv2_read(vault_addr: str, token: str, path: str) -> Optional[dict]:
    url = f"{vault_addr}/v1/secret/data/{path.lstrip('/')}"
    try:
        payload = _vault_request_json('GET', url, token)
        data = payload.get('data', {}).get('data')
        return data if isinstance(data, dict) else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def vault_kv2_write(vault_addr: str, token: str, path: str, data: dict) -> None:
    url = f"{vault_addr}/v1/secret/data/{path.lstrip('/')}"
    _vault_request_json('POST', url, token, {'data': data})


def extract_vault_value(data: dict, vault_path: str) -> str:
    """Extract a single secret value from a Vault KV payload."""
    if 'value' in data:
        return str(data['value'])
    if 'password' in data and len(data) == 1:
        return str(data['password'])
    if len(data) == 1:
        return str(next(iter(data.values())))

    raise ValueError(
        f"[ERROR] Vault secret at '{vault_path}' contains multiple keys; "
        "unable to determine the canonical value."
    )


def build_vault_payload(vault_path: str, secret: str) -> dict:
    """Build a Vault KV payload for a single-value secret."""
    payload = {'value': secret}
    if vault_path.endswith('password') or vault_path.endswith('_password'):
        payload.setdefault('password', secret)
    if vault_path.endswith('access_key'):
        payload.setdefault('access_key', secret)
    if vault_path.endswith('secret_key'):
        payload.setdefault('secret_key', secret)
    return payload


def resolve_secret_directives(config: dict, stack_config_path: Path) -> dict:
    """Resolve secret directives (Vault/local) and persist state safely."""
    directives = collect_secret_directives(config)
    total_directives = sum(len(v) for v in directives.values())

    if total_directives == 0:
        logger.debug("No secret directives found in config")
        return config

    vault_required = any(
        directives[key] for key in ('ask', 'ask_once', 'gen_to_vault')
    )

    print(
        "[INFO] Resolving secret directives: "
        f"vault={sum(len(directives[k]) for k in ('ask','ask_once','gen_to_vault'))}, "
        f"local={len(directives['local'])}, external={len(directives['external'])}, "
        f"derive={len(directives['derive'])}, ephemeral={len(directives['ephemeral'])}",
        flush=True
    )

    vault_values: dict[str, str] = {}

    if vault_required:
        vault_addr = build_vault_addr(config)
        vault_token = os.environ.get('VAULT_TOKEN')
        if not vault_token:
            raise ValueError(
                "[ERROR] VAULT_TOKEN not set; cannot resolve Vault secrets. "
                "Ensure vault_env_pre_hook.py ran successfully."
            )

        # Load existing secrets from Vault for all referenced paths
        all_paths = set().union(
            directives['ask'],
            directives['ask_once'],
            directives['gen_to_vault']
        )

        for path in sorted(all_paths):
            existing = vault_kv2_read(vault_addr, vault_token, path)
            if existing is None:
                continue
            vault_values[path] = extract_vault_value(existing, path)
            logger.debug(f"Vault secret found: {path}")

    # Resolve config (in-memory only)
    vault_storage = dict(vault_values)
    resolved_config, state = resolve_secrets(
        config,
        state=config.get('secrets', {}).get('state'),
        vault_data=vault_values,
        vault_storage=vault_storage
    )

    # Write any new secrets to Vault (paths not present in vault_values)
    if vault_required:
        for path, secret_value in vault_storage.items():
            if path in vault_values:
                continue
            vault_addr = build_vault_addr(config)
            vault_token = os.environ.get('VAULT_TOKEN')
            vault_kv2_write(vault_addr, vault_token, path, build_vault_payload(path, secret_value))
            print(f"[INFO] Stored Vault secret: {path}", flush=True)

    # Persist secret state/local values to ciu.toml (no raw Vault secrets)
    secrets_section = resolved_config.get('secrets', {})
    updates = {}
    if 'local' in secrets_section:
        updates['secrets.local'] = {'value': secrets_section['local']}
    if 'state' in secrets_section:
        updates['secrets.state'] = {'value': secrets_section['state']}
    if updates:
        apply_toml_updates(stack_config_path, updates)

    return resolved_config


def resolve_secrets(config: dict, state: Optional[dict] = None, vault_data: Optional[dict] = None, vault_storage: Optional[dict] = None) -> tuple[dict, dict]:
    """
    Phase 6: Resolve secret directives (GEN_LOCAL:, GEN_EPHEMERAL, ASK_EXTERNAL:, etc.).
    """
    if state is None:
        state = {'local': {}, 'vault': {}}
    if 'local' not in state:
        state['local'] = {}
    if 'vault' not in state:
        state['vault'] = {}
    if vault_data is None:
        vault_data = {}
    if vault_storage is None:
        vault_storage = {}

    # Initialize secrets sections if not present
    if 'secrets' not in config:
        config['secrets'] = {}
    if 'local' not in config['secrets']:
        config['secrets']['local'] = {}
    if 'state' not in config['secrets']:
        config['secrets']['state'] = {}

    def resolve_value(value: Any, path: str = '') -> Any:
        if isinstance(value, dict):
            result = {}
            for key, val in value.items():
                new_path = f"{path}.{key}" if path else key
                result[key] = resolve_value(val, new_path)
            return result

        if isinstance(value, list):
            return [resolve_value(item, f"{path}[{i}]") for i, item in enumerate(value)]

        if isinstance(value, str):
            if value.startswith('GEN_LOCAL:'):
                key = value[10:]
                if key in config['secrets']['local']:
                    return config['secrets']['local'][key]

                secret = secrets.token_urlsafe(32)
                config['secrets']['local'][key] = secret

                hash_value = hashlib.sha256(secret.encode()).hexdigest()[:8]
                state['local'][key] = {'hash': hash_value}

                return secret

            if value == 'GEN_EPHEMERAL':
                return secrets.token_urlsafe(32)

            if value.startswith('ASK_EXTERNAL:'):
                key = value[13:]
                env_value = os.environ.get(key)
                if env_value is not None:
                    return env_value
                return value

            if value.startswith('DERIVE:'):
                parts = value.split(':', 2)
                if len(parts) != 3:
                    return value

                algo = parts[1]
                source_path = parts[2]
                source_value = get_nested_value(config, source_path)
                if not source_value:
                    return value

                if algo == 'sha256':
                    return hashlib.sha256(str(source_value).encode()).hexdigest()
                return value

            if value.startswith('ASK_VAULT:'):
                vault_path = value[10:]
                if vault_path in vault_data:
                    retrieved_value = vault_data[vault_path]
                    field_name = path.split('.')[-1] if '.' in path else path
                    state['vault'][field_name] = {'retrieved': True}
                    return retrieved_value
                raise ValueError(
                    f"[ERROR] Vault secret not found for ASK_VAULT:{vault_path}. "
                    "Ensure Vault is populated and reachable."
                )

            if value.startswith('ASK_VAULT_ONCE:'):
                vault_path = value[15:]
                field_name = path.split('.')[-1] if '.' in path else path
                if vault_path in vault_data:
                    state['vault'][field_name] = {'retrieved': True, 'once': True}
                    return vault_data[vault_path]

                secret = secrets.token_urlsafe(32)
                vault_storage[vault_path] = secret
                hash_value = hashlib.sha256(secret.encode()).hexdigest()[:8]
                state['vault'][field_name] = {'hash': hash_value, 'once': True}
                return secret

            if value.startswith('GEN_TO_VAULT:'):
                vault_path = value[13:]
                if vault_path in vault_storage:
                    return vault_storage[vault_path]

                secret = secrets.token_urlsafe(32)
                vault_storage[vault_path] = secret

                field_name = path.split('.')[-1] if '.' in path else path
                hash_value = hashlib.sha256(secret.encode()).hexdigest()[:8]
                state['vault'][field_name] = {'hash': hash_value}

                return secret

            return value

        return value

    resolved_config = resolve_value(config)
    resolved_config.setdefault('secrets', {})
    resolved_config['secrets']['state'] = state
    return resolved_config, state


def load_hook_module(hook_path: str, hook_dir: Path) -> Callable:
    """
    Load a Python hook module and extract the hook function.
    """
    if not Path(hook_path).is_absolute():
        hook_file = hook_dir / hook_path
    else:
        hook_file = Path(hook_path)

    if not hook_file.exists():
        raise FileNotFoundError(f"Hook file not found: {hook_file}")

    module_name = hook_file.stem
    spec = importlib.util.spec_from_file_location(module_name, hook_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load hook module: {hook_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    for func_name in ['pre_compose_hook', 'post_compose_hook', 'run']:
        if hasattr(module, func_name):
            return getattr(module, func_name)

    for class_name in ['PreComposeHook', 'PostComposeHook']:
        if hasattr(module, class_name):
            hook_class = getattr(module, class_name)

            def hook_wrapper(config: dict, env: dict) -> dict:
                try:
                    hook_instance = hook_class(env=env)
                except TypeError:
                    hook_instance = hook_class()

                setattr(hook_instance, 'config', config)
                run_method = getattr(hook_instance, 'run', None)
                if run_method is None:
                    raise AttributeError(f"Hook class {class_name} has no run() method")

                try:
                    return run_method(config, env)
                except TypeError:
                    return run_method(env)

            return hook_wrapper

    raise AttributeError(
        f"Hook module {hook_file} does not define pre_compose_hook, post_compose_hook, or run function"
    )


def validate_registry_auth(config: dict) -> None:
    """
    Validate registry authentication if external mode is configured.
    """
    registry_config = config.get('deploy', {}).get('registry', {})
    registry_url = registry_config.get('url', '')

    if not registry_url:
        print("[INFO] Registry mode: local (no authentication required)", flush=True)
        return

    print(f"[INFO] Registry mode: external ({registry_url})", flush=True)
    print("[INFO] Validating registry authentication...", flush=True)

    try:
        result = subprocess.run(
            ['docker', 'login', '--get-credentials', registry_url],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0 or not result.stdout.strip():
            print(f"[ERROR] Not authenticated to registry: {registry_url}", flush=True)
            print(f"[ERROR] Run: docker login {registry_url}", flush=True)
            print("[ERROR] Or clear deploy.registry.url for local mode", flush=True)
            sys.exit(1)

        print(f"[SUCCESS] Authenticated to registry: {registry_url}", flush=True)

    except subprocess.TimeoutExpired:
        print(f"[ERROR] Registry authentication check timed out: {registry_url}", flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Registry authentication check failed: {e}", flush=True)
        sys.exit(1)


def set_nested_value(config: dict, dotted_path: str, value: Any) -> None:
    """Set a nested dict value using a dotted path."""
    keys = dotted_path.split('.')
    cursor = config
    for key in keys[:-1]:
        if key not in cursor or not isinstance(cursor[key], dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[keys[-1]] = value


def apply_toml_updates(config_path: Path, updates: Dict[str, Dict[str, Any]]) -> None:
    """Apply hook updates to a rendered TOML file."""
    import tomli_w

    if config_path.exists():
        with open(config_path, 'rb') as f:
            config_data = tomllib.load(f)
    else:
        config_data = {}

    for key, meta in updates.items():
        set_nested_value(config_data, key, meta.get('value'))

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'wb') as f:
        tomli_w.dump(config_data, f)


def execute_hooks(hooks: list, config: dict, initial_env: dict, stack_config_path: Optional[Path] = None) -> dict:
    """
    Phase 7: Execute hooks sequentially.
    """
    logger.debug(f"Executing {len(hooks)} hook(s)...")
    logger.debug(f"  Initial env vars: {len(initial_env)} items")

    def _to_env_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    env_vars = initial_env.copy()

    for idx, hook in enumerate(hooks):
        try:
            logger.debug(
                f"  Hook {idx+1}/{len(hooks)}: {hook.__name__ if hasattr(hook, '__name__') else hook}"
            )

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"    Config keys before hook: {list(config.keys())}")

            result = hook(config, env_vars)

            if result and isinstance(result, dict):
                env_updates: Dict[str, Any] = {}
                toml_updates: Dict[str, Dict[str, Any]] = {}

                for key, value in result.items():
                    if isinstance(value, dict) and 'value' in value:
                        persist = value.get('persist', 'env')
                        apply_to_config = value.get('apply_to_config', False)

                        if persist == 'toml':
                            toml_updates[key] = value

                        if persist in ('env', 'toml'):
                            env_updates[key] = _to_env_value(value.get('value'))

                        if apply_to_config:
                            set_nested_value(config, key, value.get('value'))
                    else:
                        env_updates[key] = _to_env_value(value)

                if stack_config_path and toml_updates:
                    for key, meta in toml_updates.items():
                        set_nested_value(config, key, meta.get('value'))
                    apply_toml_updates(stack_config_path, toml_updates)
                    logger.debug(
                        f"    Hook persisted {len(toml_updates)} toml update(s): {list(toml_updates.keys())}"
                    )

                if env_updates:
                    logger.debug(f"    Hook returned {len(env_updates)} env updates: {list(env_updates.keys())}")
                    env_vars.update(env_updates)
                else:
                    logger.debug("    Hook returned no env updates")
            else:
                logger.debug("    Hook returned no env updates")

        except Exception as e:
            logger.error(f"    Hook {idx+1} failed: {e}")
            raise

    logger.debug(f"All hooks complete. Final env vars: {len(env_vars)} items")
    return env_vars


def render_jinja2(template_path: str, config: dict) -> str:
    """
    Phase 8: Render Jinja2 template with config context.
    """
    from jinja2 import Template, TemplateError

    logger.debug(f"Rendering Jinja2 template: {template_path}")

    template_file = Path(template_path)
    if not template_file.exists():
        logger.error(f"Template file not found: {template_path}")
        raise FileNotFoundError(f"Template file not found: {template_path}")

    with open(template_file, 'r') as f:
        template_content = f.read()

    logger.debug(f"  Template size: {len(template_content)} bytes")
    logger.debug(f"  Config keys available to template: {list(config.keys())}")

    if logger.isEnabledFor(logging.DEBUG):
        if 'deploy' in config:
            logger.debug(f"  deploy.project_name: {config['deploy'].get('project_name')}")
            logger.debug(f"  deploy.network_name: {config['deploy'].get('network_name')}")
        if 'auto_generated' in config:
            logger.debug(f"  auto_generated.build_version: {config['auto_generated'].get('build_version')}")
            logger.debug(f"  auto_generated.uid: {config['auto_generated'].get('uid')}")
            logger.debug(f"  auto_generated.gid: {config['auto_generated'].get('gid')}")

    try:
        template = Template(template_content)
        rendered = template.render(**config)
        logger.debug(f"  Rendered output size: {len(rendered)} bytes")
        return rendered
    except TemplateError as e:
        logger.error(f"Failed to render template: {e}")
        raise TemplateError(f"Failed to render template {template_path}: {e}") from e


def execute_docker_compose_with_logs(
    compose_file: str,
    dry_run: bool = False,
    env: Optional[dict] = None
) -> dict:
    """
    Execute docker compose up with live log streaming.
    """
    result = {
        'status': 'success',
        'message': '',
        'stdout': '',
        'stderr': ''
    }

    if dry_run:
        print("[INFO] Dry-run mode: Skipping docker compose execution", flush=True)
        return result

    print("[INFO] Executing docker compose up...", flush=True)

    cmd = ['docker', 'compose', '-f', compose_file, 'up', '-d']

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env
        )

        stdout_lines = []
        for line in proc.stdout:
            print(f"  [COMPOSE] {line.rstrip()}", flush=True)
            stdout_lines.append(line)

        proc.wait()

        result['stdout'] = ''.join(stdout_lines)

        if proc.returncode != 0:
            result['status'] = 'error'
            result['message'] = f"Docker compose failed with exit code {proc.returncode}"
            print(f"[ERROR] Docker compose execution failed (exit {proc.returncode})", flush=True)
            return result

        print("[SUCCESS] Docker compose up completed", flush=True)

    except KeyboardInterrupt:
        print("\n[WARN] User interrupted docker compose execution", flush=True)
        result['status'] = 'interrupted'
        result['message'] = 'User interrupted execution'

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    except Exception as e:
        result['status'] = 'error'
        result['message'] = f"Docker compose execution error: {e}"
        print(f"[ERROR] {result['message']}", flush=True)

    return result


def main_execution(
    working_dir: Path,
    compose_file: str = 'docker-compose.yml.j2',
    dry_run: bool = False,
    reset: bool = False,
    yes: bool = False,
    print_context: bool = False,
    render_toml: bool = False,
    define_root: Optional[Path] = None,
    skip_hostdir_check: bool = False,
    skip_hooks: bool = False,
    skip_secrets: bool = False,
    generate_env: bool = False,
    update_cert_permission: bool = False,
    auto_connect_network: Optional[bool] = None
) -> dict:
    """
    Main execution pipeline for CIU.
    """
    result = {
        'status': 'success',
        'dry_run': dry_run
    }

    print("[INFO] Checking runtime dependencies...", flush=True)
    check_runtime_dependencies()

    try:
        original_cwd = Path.cwd()
        os.chdir(working_dir)

        print("[INFO] Loading workspace environment...", flush=True)
        try:
            bootstrap_workspace_env(
                start_dir=working_dir,
                define_root=define_root,
                defaults_filename=GLOBAL_CONFIG_DEFAULTS,
                generate_env=generate_env,
                update_cert_permission=update_cert_permission,
                required_keys=[
                "REPO_ROOT",
                "PHYSICAL_REPO_ROOT",
                "DOCKER_NETWORK_INTERNAL",
                "CONTAINER_UID",
                "DOCKER_GID",
                "PUBLIC_FQDN",
                "PUBLIC_TLS_CRT_PEM",
                "PUBLIC_TLS_KEY_PEM",
                ],
            )
        except WorkspaceEnvError as e:
            raise ValueError(str(e)) from e

        standalone_root = detect_standalone_root(working_dir)
        if standalone_root:
            env_repo_root = Path(os.environ.get("REPO_ROOT", "")).resolve()
            if env_repo_root and env_repo_root != standalone_root:
                raise ValueError(
                    "[ERROR] standalone_root is true but REPO_ROOT does not match. "
                    f"Expected: {standalone_root}, got: {env_repo_root}. "
                    "Regenerate .env.ciu from the standalone root."
                )

        print("[INFO] Rendering global configuration...", flush=True)
        global_config = render_global_config_chain(working_dir, repo_root_override=define_root)

        auto_connect_setting = global_config.get('ciu', {}).get('auto_connect_network', True)
        if auto_connect_network is not None:
            auto_connect_setting = auto_connect_network

        log_level = global_config.get('deploy', {}).get('log_level', 'INFO')
        configure_logging(log_level)

        logger.info(f"Working directory: {working_dir}")
        logger.info(f"Compose file: {compose_file}")

        print("[INFO] Rendering stack configuration...", flush=True)
        stack_config = render_stack_config(working_dir, global_config, preserve_state=not render_toml)

        if render_toml:
            print("[SUCCESS] Rendered CIU TOML files (ciu-global.toml, ciu.toml)", flush=True)
            os.chdir(original_cwd)
            return result

        ensure_workspace_network(auto_connect=auto_connect_setting)

        print("[INFO] Merging configurations...", flush=True)
        merged = deep_merge_configs(global_config, stack_config)
        logger.debug(f"Merged config has {len(merged)} top-level keys")

        if reset:
            print(f"[INFO] Resetting service in {working_dir}...", flush=True)
            reset_service(merged, working_dir, compose_file, yes)
            print("[SUCCESS] Service reset complete", flush=True)

        print("[INFO] Auto-generating values (UID, GID, BUILD_VERSION)...")
        merged = auto_generate_values(merged)

        _validate_required_fqdn(merged)
        _validate_required_certs(merged)

        if skip_hostdir_check:
            print("[INFO] --skip-hostdir-check: Skipping hostdir creation/validation", flush=True)
        else:
            print("[INFO] Creating volume directories...")
            logger.debug("Creating host-mounted volume directories...")
            create_hostdirs(merged)

        # Determine stack key for hooks
        stack_keys = [key for key in stack_config.keys() if key != 'state']
        if len(stack_keys) != 1:
            raise ValueError(
                f"[ERROR] Expected exactly one stack root section in {STACK_CONFIG_DEFAULTS}. "
                f"Found: {stack_keys}"
            )
        stack_key = stack_keys[0]

        pre_hooks = merged.get(stack_key, {}).get('hooks', {}).get('pre_compose', [])
        stack_config_path = working_dir / STACK_CONFIG_RENDERED
        if skip_hooks:
            print("[INFO] --skip-hooks: Skipping pre-compose hooks", flush=True)
            logger.info("--skip-hooks: pre-compose hooks will not execute")
        elif pre_hooks:
            print(f"[INFO] Executing {len(pre_hooks)} pre-compose hook(s)...", flush=True)
            logger.debug(f"Pre-compose hooks: {pre_hooks}")
            try:
                for hook_path in pre_hooks:
                    logger.debug(f"Loading hook: {hook_path}")
                    hook_file = Path(hook_path)
                    if not hook_file.is_absolute():
                        hook_file = working_dir / hook_path

                    if not hook_file.exists():
                        print(f"[WARN] Hook file not found: {hook_file}", flush=True)
                        logger.warning(f"Hook file not found: {hook_file}")
                        continue

                    hook_module = load_hook_module(str(hook_path), working_dir)
                    logger.debug(f"Hook module loaded: {hook_module}")

                    hook_updates = execute_hooks(
                        [hook_module],
                        merged,
                        os.environ.copy(),
                        stack_config_path=stack_config_path
                    )

                    if hook_updates:
                        os.environ.update(hook_updates)
                        print(f"[INFO] Hook updated {len(hook_updates)} value(s)", flush=True)
                        logger.debug(f"Hook updates merged into env: {list(hook_updates.keys())}")

            except Exception as e:
                print(f"[ERROR] Pre-compose hook failed: {e}", flush=True)
                logger.error(f"Pre-compose hook failed: {e}", exc_info=True)
                import traceback
                traceback.print_exc()
                raise
        else:
            logger.debug("No pre-compose hooks defined")

        if skip_secrets:
            print("[INFO] --skip-secrets: Skipping secret resolution and registry authentication", flush=True)
        else:
            merged = resolve_secret_directives(merged, stack_config_path)
            logger.debug("Validating registry authentication...")
            validate_registry_auth(merged)

        result['config'] = merged
        logger.debug(f"Final config has {len(merged)} top-level keys")

        if print_context:
            print("\n[DEBUG] Merged Configuration:")
            print(json.dumps(merged, indent=2, default=str))

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("=== Final Configuration Summary ===")
            logger.debug(f"  deploy.project_name: {merged.get('deploy', {}).get('project_name')}")
            logger.debug(f"  deploy.network_name: {merged.get('deploy', {}).get('network_name')}")
            logger.debug(f"  deploy.log_level: {merged.get('deploy', {}).get('log_level')}")
            logger.debug(f"  auto_generated.build_version: {merged.get('auto_generated', {}).get('build_version')}")
            logger.debug(f"  auto_generated.uid: {merged.get('auto_generated', {}).get('uid')}")
            logger.debug(f"  auto_generated.gid: {merged.get('auto_generated', {}).get('gid')}")
            logger.debug("===================================")

        # Render docker-compose template
        compose_template = Path(compose_file)
        if compose_template.suffix == '.j2':
            rendered_compose = render_jinja2(str(compose_template), merged)
            output_path = working_dir / DOCKER_COMPOSE_OUTPUT
            output_path.write_text(rendered_compose, encoding='utf-8')
            compose_path = str(output_path)
        else:
            compose_path = str(compose_template)

        logger.info("Executing docker compose...")
        compose_env = build_compose_env(merged)
        docker_result = execute_docker_compose_with_logs(compose_path, dry_run, env=compose_env)

        if docker_result['status'] == 'error':
            result['status'] = 'error'
            result['message'] = docker_result['message']
        elif docker_result['status'] == 'interrupted':
            result['status'] = 'interrupted'
            result['message'] = 'User aborted deployment'
        else:
            result['stdout'] = docker_result['stdout']

        post_hooks = merged.get(stack_key, {}).get('hooks', {}).get('post_compose', [])
        if skip_hooks:
            print("[INFO] --skip-hooks: Skipping post-compose hooks", flush=True)
            logger.info("--skip-hooks: post-compose hooks will not execute")
        elif post_hooks:
            print(f"[INFO] Executing {len(post_hooks)} post-compose hook(s)...", flush=True)
            logger.debug(f"Post-compose hooks: {post_hooks}")
            try:
                for hook_path in post_hooks:
                    logger.debug(f"Loading hook: {hook_path}")
                    hook_file = Path(hook_path)
                    if not hook_file.is_absolute():
                        hook_file = working_dir / hook_path

                    if not hook_file.exists():
                        print(f"[WARN] Hook file not found: {hook_file}", flush=True)
                        logger.warning(f"Hook file not found: {hook_file}")
                        continue

                    hook_module = load_hook_module(str(hook_path), working_dir)
                    logger.debug(f"Hook module loaded: {hook_module}")

                    hook_updates = execute_hooks(
                        [hook_module],
                        merged,
                        os.environ.copy(),
                        stack_config_path=stack_config_path
                    )

                    if hook_updates:
                        os.environ.update(hook_updates)
                        print(f"[INFO] Hook updated {len(hook_updates)} value(s)", flush=True)
                        logger.debug(f"Hook updates merged into env: {list(hook_updates.keys())}")

            except Exception as e:
                print(f"[ERROR] Post-compose hook failed: {e}", flush=True)
                logger.error(f"Post-compose hook failed: {e}", exc_info=True)
                import traceback
                traceback.print_exc()
                raise
        else:
            logger.debug("No post-compose hooks defined")

        os.chdir(original_cwd)

    except FileNotFoundError as e:
        result['status'] = 'error'
        result['message'] = str(e)
        print(f"[ERROR] {e}")
    except Exception as e:
        result['status'] = 'error'
        result['message'] = str(e)
        print(f"[ERROR] Execution failed: {e}")
        import traceback
        traceback.print_exc()

    return result


def main(argv: Optional[list] = None) -> int:
    args = parse_arguments(argv)

    generate_env_only = (
        args.generate_env
        and not args.dry_run
        and not args.reset
        and not args.print_context
        and not args.render_toml
        and not args.update_cert_permission
        and not args.skip_hostdir_check
        and not args.skip_hooks
        and not args.skip_secrets
    )

    if generate_env_only:
        print("[INFO] Checking runtime dependencies...", flush=True)
        check_runtime_dependencies()

        env_root = resolve_env_root(
            start_dir=args.dir,
            define_root=args.define_root,
            defaults_filename=GLOBAL_CONFIG_DEFAULTS,
        )
        env_path = generate_ciu_env(env_root)
        print(f"[SUCCESS] Generated {env_path}", flush=True)
        return 0

    result = main_execution(
        working_dir=args.dir,
        compose_file=args.file,
        dry_run=args.dry_run,
        reset=args.reset,
        yes=args.yes,
        print_context=args.print_context,
        render_toml=args.render_toml,
        define_root=args.define_root,
        skip_hostdir_check=args.skip_hostdir_check,
        skip_hooks=args.skip_hooks,
        skip_secrets=args.skip_secrets,
        generate_env=args.generate_env,
        update_cert_permission=args.update_cert_permission,
        auto_connect_network=args.auto_connect_network
    )

    if result.get('status') == 'success':
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
