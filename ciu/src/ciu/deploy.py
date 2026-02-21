#!/usr/bin/env python3
"""
Master orchestration script for DST-DNS deployment.

This script provides action-based orchestration with explicit, user-controlled execution:

Actions (executed in the order specified):
    --stop               Stop all containers (preserves volumes, container selection based on label filtering on project and environment)
    --clean              Clean volumes and data (skip running containers, selection based on label filtering on project and environment))
    --reset              Alias for --clean (matches ciu --reset behavior)
    --build              Build Docker images
    --build-no-cache     Build images from scratch (no cache)
    --start              Alias for --deploy (explicit start action)
    --deploy             Deploy services (default if no actions specified)
    --render-toml         Render ciu-global.toml and stack ciu.toml files
    --healthcheck [scope]  Run health checks (internal|external|both, default: both)
    --selftest [scope]     Run self-tests (internal|external|both, default: both)
    --print-config-context  Print evaluated configuration and exit
    --list-groups        List available service groups and exit

Options:
    --services-only      Only stop application services (keep infrastructure running)
    --phases PHASES      Comma-separated phase numbers to deploy (e.g., --phases 1,2,3)
    --groups GROUPS      Comma-separated named groups to deploy (e.g., --groups infra,apps)
    --ignore-errors      Continue execution even when errors occur
    --warnings-as-errors Treat warnings as errors and exit
    --repo-root PATH     Specify repository root directory
    --root-folder PATH   Alias for --repo-root
    --root-folder PATH   Alias for --repo-root

Named Service Groups:
    Groups are defined in ciu-global.toml [deploy.groups]
    Built-in groups:
        infra            Infrastructure only (vault, redis, postgres, consul)
        apps             Application services only (controller, workers, webapp)
        observability    Monitoring stack only (skywalking, otel, docker-stats)
        minimal          Minimum viable deployment (vault + data + controller)
        full             All services (default)

Examples:
    ciu-deploy                           # Default: deploy only
    ciu-deploy --deploy                  # Explicit deploy
    ciu-deploy --render-toml             # Render global + stack TOML files
    ciu-deploy --start                   # Explicit start alias for deploy
    ciu-deploy --stop                    # Stop all containers
    ciu-deploy --stop --services-only    # Stop only app services (keep infra)
    ciu-deploy --stop --clean --deploy   # Full restart with clean state
    ciu-deploy --reset                   # Alias for clean
    ciu-deploy --clean --build --deploy  # Clean + rebuild + deploy
    ciu-deploy --print-config-context    # Inspect configuration
    ciu-deploy --selftest                # Run self-tests (internal + external)
    ciu-deploy --selftest internal       # Run internal self-tests only
    ciu-deploy --selftest external       # Run external self-tests only
    ciu-deploy --phases 1,2 --deploy     # Deploy only phases 1 and 2
    ciu-deploy --groups infra --deploy   # Deploy infrastructure only
    ciu-deploy --groups apps --deploy    # Deploy application services only
    ciu-deploy --groups minimal --deploy # Deploy minimal set for testing
    ciu-deploy --list-groups             # Show available groups
    ciu-deploy --deploy --healthcheck    # Deploy then health check (internal + external)
    ciu-deploy --deploy --healthcheck external  # Deploy then check external routes only

Execution Order:
    - Actions execute in the order specified on command line (left to right)
    - No implicit actions (e.g., --clean does NOT automatically stop)
    - If --groups/--phases is provided, actions apply only to those selections
    - Fail-fast by default (use --ignore-errors to continue on failure)

Configuration:
    - Service startup order: ciu-global.toml [deploy.phases]
    - Named service groups: ciu-global.toml [deploy.groups]
    - Network name: DOCKER_NETWORK_INTERNAL from .env.ciu
    - Repository paths: REPO_ROOT and PHYSICAL_REPO_ROOT environment variables
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import tomllib
import uuid
from pathlib import Path
from typing import Set, TypedDict

from .config_constants import (
    GLOBAL_CONFIG_DEFAULTS,
    GLOBAL_CONFIG_RENDERED,
    STACK_CONFIG_DEFAULTS,
    STACK_CONFIG_RENDERED,
)
from .cli_utils import get_cli_version
from .workspace_env import (
    WorkspaceEnvError,
    bootstrap_workspace_env,
    detect_standalone_root,
)
from .render_utils import (
    find_stack_anchor,
    build_global_config_debug_lines,
    load_global_config,
    render_global_config,
    render_stack_configs,
)


# Color codes for output
BLUE = '\033[94m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'

# Debug logging is opt-in and tied to deploy.log_level.
DEBUG_ENABLED = False


def set_debug_enabled(log_level: str | None) -> None:
    """Enable debug logging when log_level is DEBUG (case-insensitive)."""
    global DEBUG_ENABLED
    DEBUG_ENABLED = str(log_level or '').strip().upper() == 'DEBUG'


class DeploymentContext:
    """Track deployment progress for reporting and error handling."""

    def __init__(self) -> None:
        self.deployment_id = uuid.uuid4().hex[:8]
        self.start_time = time.time()
        self.current_service: str | None = None
        self.services_started: set[str] = set()
        self.services_failed: set[str] = set()
        self.context: dict[str, str] = {}

    def set_service(self, service_name: str) -> None:
        self.current_service = service_name
        self.context['service'] = service_name

    def get_context(self) -> dict[str, str]:
        return self.context

    def record_success(self, service_name: str) -> None:
        self.services_started.add(service_name)
        self.services_failed.discard(service_name)

    def record_failure(self, service_name: str, _message: str) -> None:
        self.services_failed.add(service_name)
        self.services_started.add(service_name)

    def get_summary(self) -> "DeploymentSummary":
        duration_seconds = int(time.time() - self.start_time)
        return {
            'deployment_id': self.deployment_id,
            'duration_seconds': duration_seconds,
            'services_started': len(self.services_started),
            'services_failed': len(self.services_failed),
        }


_DEPLOYMENT_CONTEXT: DeploymentContext | None = None


def get_deployment_context() -> DeploymentContext:
    """Get or create the singleton deployment context."""
    global _DEPLOYMENT_CONTEXT
    if _DEPLOYMENT_CONTEXT is None:
        _DEPLOYMENT_CONTEXT = DeploymentContext()
    return _DEPLOYMENT_CONTEXT


class DeploymentSummary(TypedDict):
    deployment_id: str
    duration_seconds: int
    services_started: int
    services_failed: int

def resolve_groups_to_phases(global_config: dict, group_names: list[str]) -> Set[str]:
    """Resolve named groups into a set of deployment phase keys."""
    groups_config = global_config.get('deploy', {}).get('groups', {})
    if not groups_config:
        print(f"{RED}[ERROR]{RESET} No groups defined in [deploy.groups]", flush=True)
        print(f"{RED}[ERROR]{RESET} Define groups in ciu-global.toml [deploy.groups]", flush=True)
        sys.exit(1)

    resolved_phases: Set[str] = set()

    for group_name in group_names:
        group_name = group_name.strip().lower()

        if group_name not in groups_config:
            available = ', '.join(sorted(groups_config.keys()))
            print(f"{RED}[ERROR]{RESET} Unknown group: '{group_name}'", flush=True)
            print(f"{RED}[ERROR]{RESET} Available groups: {available}", flush=True)
            print(f"{RED}[ERROR]{RESET} Use --list-groups to see group definitions", flush=True)
            sys.exit(1)

        group_def = groups_config[group_name]

        # Support both formats:
        # Simple array: ["phase_1", "phase_2"]
        # Object: { phases = ["phase_1", "phase_2"], description = "..." }
        if isinstance(group_def, list):
            phases = group_def
        elif isinstance(group_def, dict):
            phases = group_def.get('phases', [])
        else:
            print(f"{RED}[ERROR]{RESET} Invalid group definition for '{group_name}': {type(group_def)}", flush=True)
            sys.exit(1)

        for phase_key in phases:
            # Handle both "phase_1" and just "1" formats
            if phase_key.startswith('phase_'):
                resolved_phases.add(phase_key)
            else:
                resolved_phases.add(f"phase_{phase_key}")

        print(f"{BLUE}[INFO]{RESET} Group '{group_name}': includes phases {phases}", flush=True)

    return resolved_phases


def normalize_service_slug(name: str) -> str:
    """Normalize service names to slug format for comparisons."""
    slug = name.strip().lower().replace('_', '-')
    slug = re.sub(r'[^a-z0-9-]+', '-', slug)
    return slug.strip('-')


def collect_enabled_service_slugs(phases: list[dict], global_config: dict) -> set[str]:
    """Collect enabled service slugs from phase definitions and service registry."""
    enabled_slugs: set[str] = set()
    enabled_paths: set[str] = set()

    for phase in phases:
        for service in phase.get('services', []):
            if not service.get('enabled', True):
                continue
            name = service.get('name')
            path = service.get('path')
            if name:
                enabled_slugs.add(normalize_service_slug(name))
            if path:
                enabled_paths.add(path)
                enabled_slugs.add(normalize_service_slug(Path(path).name))

    service_registry = global_config.get('service', {})
    for category, projects in service_registry.items():
        category_path = category.replace('_', '-')
        for project_key, services in projects.items():
            project_path = project_key.replace('_', '-')
            stack_path = f"{category_path}/{project_path}"
            if stack_path not in enabled_paths:
                continue
            if not isinstance(services, dict):
                continue
            for service_key, service_config in services.items():
                if not isinstance(service_config, dict):
                    continue
                name = service_config.get('name') or service_key
                enabled_slugs.add(normalize_service_slug(name))

    return enabled_slugs


def filter_deployment_phases(
    deployment_phases: list[dict],
    selected_phase_keys: Set[str] | None
) -> list[dict]:
    """Filter deployment phases by selected phase keys.

    Args:
        deployment_phases: All enabled deployment phases.
        selected_phase_keys: Optional set of phase keys to keep.

    Returns:
        Filtered list of phases.
    """
    if not selected_phase_keys:
        return deployment_phases
    return [phase for phase in deployment_phases if phase.get('key') in selected_phase_keys]


def collect_bake_targets_from_phases(phases: list[dict]) -> list[str]:
    """Collect docker-bake targets based on service stack paths.

    Rule: Only services in applications/ or tools/ are build targets.
    The bake target is the final path component.
    """
    targets: set[str] = set()
    for phase in phases:
        for service in phase.get('services', []):
            if not service.get('enabled', True):
                continue
            service_path = service.get('path')
            if not service_path:
                continue
            parts = Path(service_path).parts
            if not parts:
                continue
            category = parts[0]
            if category not in {'applications', 'tools'}:
                continue
            targets.add(parts[-1])
    return sorted(targets)


def build_action_sequence(argv: list[str]) -> list[str]:
    """Build the ordered action list from CLI args, honoring aliases."""
    action_flags = [
        '--stop', '--clean', '--reset', '--build', '--build-no-cache',
        '--start', '--deploy', '--render-toml', '--healthcheck', '--selftest', '--print-config-context'
    ]
    action_aliases = {
        'start': 'deploy',
        'reset': 'clean',
    }
    actions: list[str] = []
    for arg in argv[1:]:
        if arg in action_flags:
            action_name = arg.lstrip('--').replace('-', '_')
            action_name = action_aliases.get(action_name, action_name)
            actions.append(action_name)
    return actions


def list_available_groups(global_config: dict) -> None:
    """
    Print available service groups and their definitions.
    
    Args:
        global_config: Global configuration dictionary
    """
    groups_config = global_config.get('deploy', {}).get('groups', {})
    phases_config = global_config.get('deploy', {}).get('phases', {})
    
    print("\n" + "="*70)
    print("AVAILABLE SERVICE GROUPS")
    print("="*70)
    
    if not groups_config:
        print("\nNo groups defined in [deploy.groups]")
        print("Add groups to ciu-global.toml")
        print("\nExample:")
        print('  [deploy.groups]')
        print('  infra = ["phase_1", "phase_2"]')
        print('  apps = ["phase_4"]')
        return
    
    for group_name in sorted(groups_config.keys()):
        group_def = groups_config[group_name]
        
        # Support both formats:
        # Simple array: ["phase_1", "phase_2"]
        # Object: { phases = ["phase_1", "phase_2"], description = "..." }
        if isinstance(group_def, list):
            phases = group_def
            description = "No description"
        elif isinstance(group_def, dict):
            phases = group_def.get('phases', [])
            description = group_def.get('description', 'No description')
        else:
            phases = []
            description = f"Invalid format: {type(group_def)}"

        print(f"\n{BLUE}{group_name}{RESET}")
        print(f"  Description: {description}")
        print(f"  Phases: {phases}")
        
        # List services in each phase
        print("  Services:")
        for phase_key in phases:
            # Handle both "phase_1" and "1" formats
            if not phase_key.startswith('phase_'):
                phase_key = f"phase_{phase_key}"
            if phase_key in phases_config:
                phase = phases_config[phase_key]
                phase_name = phase.get('name', phase_key)
                services = phase.get('services', [])
                enabled_services = [s.get('name', s.get('path', 'unnamed')) 
                                   for s in services if s.get('enabled')]
                if enabled_services:
                    print(f"    {phase_key} ({phase_name}): {', '.join(enabled_services)}")
    
    print("\n" + "="*70)
    print("\nUsage examples:")
    print("  ciu-deploy --groups infra --deploy    # Deploy infrastructure only")
    print("  ciu-deploy --groups apps --deploy     # Deploy applications only")
    print("  ciu-deploy --groups infra,apps        # Multiple groups")
    print("  ciu-deploy --phases 1,2,4 --deploy    # Specific phases")
    print("")


def info(msg, **context):
    """Log info message with optional structured context."""
    ctx = get_deployment_context().get_context()
    ctx.update(context)
    
    # Human-readable format
    print(f"{BLUE}[INFO]{RESET} {msg}", flush=True)
    
    # Structured context (when extra data provided)
    if context:
        for key, value in context.items():
            print(f"  {key}: {value}", flush=True)

def success(msg, **context):
    """Log success message with optional structured context."""
    ctx = get_deployment_context().get_context()
    ctx.update(context)
    
    print(f"{GREEN}[SUCCESS]{RESET} {msg}", flush=True)
    
    if context:
        for key, value in context.items():
            print(f"  {key}: {value}", flush=True)

def warn(msg, **context):
    """Log warning message with optional structured context."""
    ctx = get_deployment_context().get_context()
    ctx.update(context)
    
    print(f"{YELLOW}[WARN]{RESET} {msg}", flush=True)
    
    if context:
        for key, value in context.items():
            print(f"  {key}: {value}", flush=True)

def error(msg, **context):
    """Log error message with optional structured context and exit."""
    ctx = get_deployment_context().get_context()
    ctx.update(context)
    
    # Record error in context
    if ctx.get('service'):
        get_deployment_context().record_failure(ctx['service'], msg)
    
    print(f"{RED}[ERROR]{RESET} {msg}", flush=True)
    
    if context:
        for key, value in context.items():
            print(f"  {key}: {value}", flush=True)
    
    # Print deployment summary before exit
    summary = get_deployment_context().get_summary()
    print(f"\n{RED}[DEPLOYMENT FAILED]{RESET}")
    print(f"  Deployment ID: {summary['deployment_id']}")
    print(f"  Duration: {summary['duration_seconds']}s")
    print(f"  Services started: {summary['services_started']}")
    print(f"  Services failed: {summary['services_failed']}")
    
    sys.exit(1)


def debug(msg, **context):
    """Log debug message (only shown when DEBUG logging is enabled)."""
    if not DEBUG_ENABLED:
        return
    ctx = get_deployment_context().get_context()
    ctx.update(context)
    
    print(f"{BLUE}[DEBUG]{RESET} {msg}", flush=True)
    
    if context:
        for key, value in context.items():
            print(f"  {key}: {value}", flush=True)

def get_container_name(global_config: dict, service_name: str) -> str:
    """
    Construct container name from global config and service name.
    
    Pattern: {project_name}-{environment_tag}-{service_name}
    
    Args:
        global_config: Global configuration dictionary
        service_name: Service name (e.g., 'vault', 'postgres', 'controller')
    
    Returns:
        Container name (e.g., 'dstdns-98535c-vault', 'dstdns-prod-postgres')
    
    Raises:
        SystemExit: If project_name or environment_tag not configured
    """
    deployment = global_config.get('deploy', {})
    project_name = deployment.get('project_name')
    environment_tag = deployment.get('environment_tag')
    
    if not project_name:
        error("CRITICAL: deploy.project_name not set in global config")
    
    if not environment_tag:
        error("CRITICAL: deploy.environment_tag not set in global config")
    
    return f"{project_name}-{environment_tag}-{service_name}"


def is_service_container(container_name: str) -> bool:
    """
    Check if container is a service container (manually managed, not part of deployment).
    
    Service containers are long-running infrastructure containers that:
    - Are manually managed via wrapper scripts (admin-debug-exec.sh, testing-exec.sh)
    - Should NOT be stopped by --stop action
    - Should NOT be cleaned by --clean action
    - Have label: dstdns.service-container=true
    
    Args:
        container_name: Container name to check
    
    Returns:
        True if container is a service container, False otherwise
    """
    try:
        # Check for service-container label
        result = subprocess.run(
            [
                "docker",
                "inspect",
                container_name,
                "--format",
                "{{.Config.Labels.\"dstdns.service-container\"}}",
            ],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0 and result.stdout.strip() == 'true':
            return True
            
        # Fallback: Check container name for known service container patterns
        # This provides backwards compatibility for containers without labels
        service_patterns = ['admin-debug', 'testing']
        for pattern in service_patterns:
            if pattern in container_name.lower():
                return True
        
        return False
        
    except Exception as e:
        warn(f"Failed to check if {container_name} is service container: {e}")
        return False


def get_python_executable(repo_root) -> str:
    """
    Get the Python executable to use for running CIU.
    
    Per project requirements (AGENTS.md): Always use the workspace venv
    to ensure required libraries (PyYAML, hvac, jinja2, toml) are available.
    
    Returns:
        str: Path to Python executable (venv if available, else system python3)
    """
    python_executable = os.environ.get('PYTHON_EXECUTABLE')
    if not python_executable:
        error(
            "PYTHON_EXECUTABLE is not set. "
            "Run: ciu --generate-env (or ciu-deploy --generate-env) and source .env.ciu."
        )
    assert python_executable is not None
    python_path = Path(python_executable)
    if not python_path.exists():
        error(
            f"PYTHON_EXECUTABLE does not exist: {python_executable}. "
            "Regenerate .env.ciu to update paths (ciu --generate-env)."
        )
    info(f"Using workspace Python: {python_executable}")
    return python_executable


def get_ciu_command(python_executable: str) -> list[str]:
    """Build the CIU command using the installed module."""
    return [python_executable, "-m", "ciu"]

def run_cmd(cmd, cwd=None, check=True, env=None, capture_output=True, text=True, timeout=None):
    """Run a command and return the result."""
    info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture_output,
        text=text,
        env=env,
        timeout=timeout
    )
    if check and result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip())
        error(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")
    return result

def stop_deployment(
    repo_root,
    services_only: bool = False,
    selected_service_slugs: set[str] | None = None,
):
    """
    Stop all running containers without cleaning volumes.
    Uses Docker label filtering to find ALL containers matching project and environment.
    This ensures containers not in phase definitions (like pgadmin) are also stopped.
    
    Args:
        repo_root: Repository root path
        services_only: If True, only stop application services (keep infra running)
    """
    info("="*70)
    if services_only:
        info("STOP: Stopping application services only (infrastructure preserved)")
    else:
        info("STOP: Stopping all containers (volumes preserved)")
    info("="*70)
    
    # Load global config to get project name and environment tag
    global_config = load_global_config(repo_root)
    deployment = global_config.get('deploy', {})
    project_name = deployment.get('project_name')
    env_tag = deployment.get('environment_tag')
    label_prefix = deployment.get('labels', {}).get('prefix')

    if not project_name:
        error("CRITICAL: deploy.project_name not set in global config")
    if not env_tag:
        error("CRITICAL: deploy.environment_tag not set in global config")
    if not label_prefix:
        error("CRITICAL: deploy.labels.prefix not set in global config")
    
    # Define infrastructure patterns to skip when services_only is True
    # These patterns match containers for vault, postgres, redis, minio, etc.
    infra_patterns = ['vault', 'postgres', 'redis', 'minio', 'pgadmin', 'adminer', 'consul']
    
    info(f"Stopping containers: project={project_name}, environment={env_tag}")
    if services_only:
        info("  Mode: Services only (skipping infrastructure containers)")
        info(f"  Infrastructure patterns to preserve: {', '.join(infra_patterns)}")
    if selected_service_slugs:
        info("  Mode: Scoped stop (matching selected phases/groups)")
    info("  Strategy: Using Docker labels (managed-by + name pattern)")
    info("")
    
    # Find all containers matching managed-by label AND name pattern
    # NOTE: Using managed-by label since some containers may not have project/environment labels
    #       (labels were added after containers were created)
    # Strategy: Try label-based filter first, then fallback to name-only pattern
    # This ensures containers from before label implementation are also found
    
    # First, try with managed-by label (preferred)
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label={label_prefix}.managed-by=orchestrator",
            "--filter",
            f"name=^{project_name}-{env_tag}-",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True
    )
    
    if not result.stdout.strip():
        # Fallback: Try name pattern only (catches containers without labels)
        debug("No containers with managed-by label, trying name pattern only...")
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name=^{project_name}-{env_tag}-",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True
        )
    
    if not result.stdout.strip():
        info("No matching containers found")
        info("="*70)
        success("STOP COMPLETE (no containers running)")
        info("="*70)
        info("")
        return
    
    containers = [c.strip() for c in result.stdout.strip().split('\n') if c.strip()]
    
    # Filter out service containers (manually managed) and infrastructure (if services_only)
    service_containers = []
    infra_containers = []
    deployment_containers = []
    
    prefix = f"{project_name}-{env_tag}-"
    for container in containers:
        if is_service_container(container):
            service_containers.append(container)
        elif services_only and any(pattern in container.lower() for pattern in infra_patterns):
            infra_containers.append(container)
        else:
            if selected_service_slugs:
                service_name = container
                if container.startswith(prefix):
                    service_name = container[len(prefix):]
                service_slug = normalize_service_slug(service_name)
                if service_slug not in selected_service_slugs:
                    continue
            deployment_containers.append(container)
    
    if service_containers:
        info(f"Skipping {len(service_containers)} service containers (manually managed):")
        for container in service_containers:
            info(f"  - {container}")
        info("")
    
    if infra_containers:
        info(f"Preserving {len(infra_containers)} infrastructure containers (--services-only):")
        for container in infra_containers:
            info(f"  - {container}")
        info("")
    
    if not deployment_containers:
        info("No deployment containers found to stop")
        info("="*70)
        success("STOP COMPLETE (no deployment containers running)")
        info("="*70)
        info("")
        return
    
    info(f"Found {len(deployment_containers)} deployment containers to stop:")
    for container in deployment_containers:
        info(f"  - {container}")
    info("")
    
    stopped_containers = []
    failed_containers = []
    
    for container in deployment_containers:
        info(f"Stopping {container}...")
        
        stop_result = subprocess.run(
            ["docker", "stop", container],
            capture_output=True,
            text=True
        )
        
        if stop_result.returncode != 0:
            warn(f"  ⚠ Failed: {stop_result.stderr.strip()}")
            failed_containers.append(container)
        else:
            success(f"  ✓ Stopped")
            stopped_containers.append(container)
    
    info("")
    info("="*70)
    success("STOP COMPLETE")
    info("="*70)
    info(f"Successfully stopped: {len(stopped_containers)}/{len(deployment_containers)}")
    if failed_containers:
        warn(f"Failed to stop: {len(failed_containers)} containers")
        for container in failed_containers:
            warn(f"  - {container}")
    if infra_containers:
        info(f"Infrastructure containers preserved: {len(infra_containers)}")
    info("Note: Volumes preserved for restart")
    info("")
    
    # Helpful manual check commands
    info("Manual check commands:")
    info(f"  docker ps -a --filter label=project={project_name} --filter label=environment={env_tag}")
    info("  docker ps -a")
    info("")


def cleanup_deployment(repo_root, selected_phase_keys: Set[str] | None = None):
    """
    Clean all volumes and data.
    Does NOT stop containers - use --stop action first if needed.
    Dynamically discovers services from ciu-global.toml phases.
    
    Cleanup Strategy:
    1. Run init containers with CLEAN_DATA_DIR=true to delete service-owned data
       (e.g., PostgreSQL UID 70 files that host user cannot delete)
    2. Stop and remove containers/volumes via docker compose down -v
    3. Clean remaining host-mounted directories via Docker alpine container
    4. Remove rendered config files and credential state files
    """
    info("="*70)
    info("CLEANUP: Removing volumes and data (containers NOT stopped)")
    info("="*70)
    
    # Load global config to get all service paths dynamically
    global_config = load_global_config(repo_root)
    deployment_phases = load_deployment_phases(global_config)
    deployment_phases = filter_deployment_phases(deployment_phases, selected_phase_keys)
    full_cleanup = not selected_phase_keys

    if not deployment_phases:
        warn("No phases selected for cleanup")
        return

    # Prefer host-visible paths for cleanup in devcontainer Docker-outside-Docker setups
    physical_repo_root = os.getenv('PHYSICAL_REPO_ROOT')
    physical_repo_root_path = None
    if physical_repo_root:
        physical_repo_root_path = Path(physical_repo_root).resolve()
    
    # STEP 1: Run cleanup init containers with CLEAN_DATA_DIR=true
    # This handles service-owned files (e.g., postgres UID 70) that host user cannot delete
    info("")
    info("Step 1: Running cleanup init containers...")
    stack_paths = {
        (repo_root / service.get('path'))
        for phase in deployment_phases
        for service in phase.get('services', [])
        if service.get('enabled', True) and service.get('path')
    }
    cleaned_by_init = run_cleanup_init_containers(
        repo_root,
        global_config,
        allowed_stack_paths=stack_paths
    )
    if cleaned_by_init:
        info(f"  Cleanup init containers completed: {len(cleaned_by_init)} services")
    info("")
    
    # STEP 2: Collect all service paths from deployment phases
    stacks = []
    info(f"Step 2: Discovering services from {len(deployment_phases)} deployment phases...")

    for phase in deployment_phases:
        phase_name = phase.get('name', 'Unknown Phase')
        services = phase.get('services', [])

        info(f"  Phase: {phase_name} ({len(services)} services)")

        for service in services:
            service_path = service.get('path', '')
            service_name = service.get('name', 'Unknown Service')

            if service_path:
                full_path = repo_root / service_path
                stacks.append((full_path, service_name))
                info(f"    - {service_name} ({service_path})")
    
    info(f"Total services to clean: {len(stacks)}")
    info("")
    
    cleaned_containers = []
    cleaned_volumes = []
    cleaned_directories = []
    
    for stack_path, stack_name in stacks:
        # Service containers are skipped by is_service_container() check below
        # No need for hardcoded exclusions here
            
        if not stack_path.exists():
            continue
            
        compose_file = stack_path / "docker-compose.yml"
        
        # Stop and remove containers/volumes if compose file exists
        if compose_file.exists():
            info(f"Cleaning stack: {stack_name}")
            # CRITICAL: Use COMPOSE_PROFILES=full to ensure ALL services are visible
            # (including pgadmin and other profile-dependent services)
            # Without this, `docker compose down` ignores containers started with profiles
            cleanup_env = os.environ.copy()
            cleanup_env['COMPOSE_PROFILES'] = 'full,pgadmin'
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "down", "-v"],
                cwd=stack_path,
                capture_output=True,
                text=True,
                env=cleanup_env
            )
            if result.returncode == 0:
                # Parse output to see what was removed
                for line in result.stderr.split('\n'):
                    if 'Container' in line and 'Removed' in line:
                        container = line.split()[1]
                        cleaned_containers.append(container)
                    elif 'Volume' in line and 'Removed' in line:
                        volume = line.split()[1]
                        cleaned_volumes.append(volume)
            
        # Remove host-mounted volume directories
        # CRITICAL: Use Docker to clean bind mount directories because:
        # 1. PostgreSQL creates PGDATA with mode 0700 (owner-only)
        # 2. Host user (UID 1000) cannot delete content owned by postgres (UID 70)
        # 3. Docker daemon runs as root and can clean any directory
        for vol_dir in stack_path.glob("vol-*"):
            if vol_dir.is_dir():
                info(f"  Removing directory: {vol_dir.name}")
                # Use Docker to remove directories as root (handles permission issues)
                cleanup_target = vol_dir.resolve()
                if physical_repo_root_path:
                    try:
                        rel_path = cleanup_target.relative_to(repo_root)
                        cleanup_target = physical_repo_root_path / rel_path
                    except ValueError:
                        pass
                abs_vol_dir = str(cleanup_target)
                parent_dir = str(cleanup_target.parent)
                dir_name = cleanup_target.name
                result = subprocess.run(
                    ['docker', 'run', '--rm',
                     '-v', f'{parent_dir}:/cleanup_parent',
                     'alpine:latest',
                     'sh', '-c',
                     f'rm -rf "/cleanup_parent/{dir_name}" 2>/dev/null || true'],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    # Fallback to direct removal if docker method fails
                    warn(f"    Docker cleanup failed, attempting direct rm: {result.stderr.strip()}")
                    shutil.rmtree(vol_dir, ignore_errors=True)
                cleaned_directories.append(str(vol_dir))
        
        # Remove rendered docker-compose.yml files
        if compose_file.exists():
            compose_file.unlink()
            info("  Removed: docker-compose.yml")
        
        # Remove rendered stack config files
        rendered_config = stack_path / STACK_CONFIG_RENDERED
        if rendered_config.exists():
            rendered_config.unlink()
            info(f"  Removed: {STACK_CONFIG_RENDERED}")
        
        # CRITICAL: Remove vault-init.json to prevent stale credentials
        # This file persists Vault unseal keys/root tokens and must be cleaned
        # to ensure fresh initialization on next deploy
        vault_init_json = stack_path / "vault-init.json"
        if vault_init_json.exists():
            vault_init_json.unlink()
            info("  Removed: vault-init.json (credential state file)")
    
    # Also check and clean global vault-init.json locations
    if full_cleanup:
        vault_init_global = repo_root / "infra" / "vault" / "vault-init.json"
        if vault_init_global.exists():
            vault_init_global.unlink()
            info("Removed global vault-init.json")

        # Remove ciu-global.toml (rendered runtime config)
        global_rendered = repo_root / GLOBAL_CONFIG_RENDERED
        if global_rendered.exists():
            global_rendered.unlink()
            info("Removed ciu-global.toml")
    
    # Remove any orphaned containers matching BOTH project AND environment (for parallel deployments)
    deployment = global_config.get('deploy', {})
    project_name = deployment.get('project_name')
    env_tag = deployment.get('environment_tag')

    if not project_name:
        error("CRITICAL: deploy.project_name not set in global config")
    if not env_tag:
        error("CRITICAL: deploy.environment_tag not set in global config")
    
    if full_cleanup:
        info(f"Checking for orphaned containers (project={project_name}, environment={env_tag})...")
        info("  Note: Filtering by BOTH project label AND environment tag to support parallel deployments")
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=project={project_name}",
                "--filter",
                f"label=environment={env_tag}",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            orphaned = result.stdout.strip().split('\n')
            for container in orphaned:
                # Skip service containers (admin-debug, testing, etc.)
                if is_service_container(container):
                    info(f"  Skipping: {container} (service container - manually managed)")
                    continue
                info(f"  Removing orphaned container: {container}")
                subprocess.run(
                    ["docker", "rm", "-f", container],
                    capture_output=True
                )
                cleaned_containers.append(container)
    
    # Remove any orphaned volumes
    if full_cleanup:
        info("Checking for orphaned DST-DNS volumes...")
        result = subprocess.run(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label=project={project_name}",
                "--format",
                "{{.Name}}",
            ],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            orphaned = result.stdout.strip().split('\n')
            for volume in orphaned:
                info(f"  Removing orphaned volume: {volume}")
                subprocess.run(
                    ["docker", "volume", "rm", volume],
                    capture_output=True
                )
                cleaned_volumes.append(volume)
    
    # Remove project-prefixed named volumes (e.g., SkyWalking: dstdns-98535c-banyandb-data)
    # These may not have labels but match our naming convention: {project_name}-{env_tag}-*
    if full_cleanup:
        info(f"Checking for named volumes with prefix: {project_name}-{env_tag}-")
        result = subprocess.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            named_volumes = [
                name for name in result.stdout.strip().split('\n')
                if name.startswith(f"{project_name}-{env_tag}-")
            ]
            for volume in named_volumes:
                if volume and volume not in cleaned_volumes:
                    info(f"  Removing named volume: {volume}")
                    result = subprocess.run(
                        ["docker", "volume", "rm", volume],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        cleaned_volumes.append(volume)
                    else:
                        warn(f"    Could not remove {volume}: {result.stderr.strip()}")
    
    # Also clean legacy volumes without environment tag (e.g., dstdns-vault-data)
    if full_cleanup:
        info(f"Checking for legacy volumes with prefix: {project_name}-")
        result = subprocess.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            legacy_volumes = [
                name for name in result.stdout.strip().split('\n')
                if name.startswith(f"{project_name}-")
                and not name.startswith(f"{project_name}-{env_tag}-")
            ]
            for volume in legacy_volumes:
                if volume and volume not in cleaned_volumes:
                    info(f"  Removing legacy volume: {volume}")
                    result = subprocess.run(
                        ["docker", "volume", "rm", volume],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        cleaned_volumes.append(volume)
                    else:
                        warn(f"    Could not remove {volume}: {result.stderr.strip()}")
    
    # Prune all unused volumes (comprehensive cleanup)
    if full_cleanup:
        info("Pruning all unused Docker volumes...")
        result = subprocess.run(
            ["docker", "volume", "prune", "-af"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            # Parse the prune output to count pruned volumes
            for line in result.stdout.split('\n'):
                if 'Total reclaimed space' in line:
                    info(f"  {line.strip()}")
    
    # Summary
    info("="*70)
    success("CLEANUP COMPLETE")
    info("="*70)
    info(f"Containers removed: {len(set(cleaned_containers))}")
    if cleaned_containers:
        for c in set(cleaned_containers):
            info(f"  - {c}")
    info(f"Volumes removed: {len(set(cleaned_volumes))}")
    if cleaned_volumes:
        for v in set(cleaned_volumes):
            info(f"  - {v}")
    info(f"Directories removed: {len(cleaned_directories)}")
    if cleaned_directories:
        for d in cleaned_directories:
            info(f"  - {d}")
    info("")


def run_cleanup_init_containers(
    repo_root,
    global_config,
    allowed_stack_paths: set[Path] | None = None,
):
    """
    Run init containers with CLEAN_DATA_DIR=true to clean persistent data.
    
    This allows init containers (running as root) to delete files owned by
    service UIDs (e.g., postgres UID 70) that the host user cannot delete.
    
    Configuration: deploy.cleanup_init_containers in ciu-global.toml
    Format: ["stack_path:service_name", ...]
    Example: ["infra/db-core:postgres-init", "infra/db-core:minio-init"]
    
    Args:
        repo_root: Repository root path
        global_config: Loaded global configuration
        allowed_stack_paths: Optional set of stack paths to allow (phase/group scoping)
        
    Returns:
        List of container names that ran cleanup
    """
    deploy_cfg = global_config.get('deploy', {})
    cleanup_containers = deploy_cfg.get('cleanup_init_containers', [])
    
    if not cleanup_containers:
        info("No cleanup init containers configured (deploy.cleanup_init_containers)")
        return []
    
    info(f"Running {len(cleanup_containers)} cleanup init container(s) with CLEAN_DATA_DIR=true...")
    
    cleaned = []
    for entry in cleanup_containers:
        if ':' not in entry:
            warn(f"  Invalid format (expected 'stack_path:service_name'): {entry}")
            continue
        
        stack_path_rel, service_name = entry.split(':', 1)
        stack_path = repo_root / stack_path_rel
        if allowed_stack_paths is not None and stack_path not in allowed_stack_paths:
            debug(f"Skipping cleanup init container outside selected phases: {entry}")
            continue
        compose_file = stack_path / "docker-compose.yml"
        
        if not compose_file.exists():
            # Need to render compose file first using --dry-run --skip-hostdir-check
            # The --skip-hostdir-check flag is essential because:
            # 1. During cleanup, volume directories may be owned by service UIDs (e.g., postgres UID 70)
            # 2. CIU normally validates write access to host directories
            # 3. That validation fails when directories are owned by container UIDs
            # 4. Using --skip-hostdir-check allows rendering the template without validation
            info(f"  Rendering compose file for {stack_path_rel}...")
            # Run CIU with --dry-run --skip-hostdir-check --skip-hooks --skip-secrets to render only
            # --skip-hooks prevents vault/database hooks from running during cleanup phase
            # --skip-secrets prevents vault URL validation which fails when vault isn't running
            python_exe = get_python_executable(repo_root)
            ciu_cmd = get_ciu_command(python_exe)
            result = subprocess.run(
                ciu_cmd + ['--dry-run', '--skip-hostdir-check', '--skip-hooks', '--skip-secrets', '-d', str(stack_path)],
                cwd=stack_path,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                warn(f"  Failed to render compose file: {result.stderr}")
                continue
        
        if not compose_file.exists():
            warn(f"  Skipping {entry}: docker-compose.yml not found after render attempt")
            continue
        
        info(f"  Running cleanup: {service_name} in {stack_path_rel}")
        
        # Set environment with CLEAN_DATA_DIR=true
        cleanup_env = os.environ.copy()
        cleanup_env['CLEAN_DATA_DIR'] = 'true'
        cleanup_env['COMPOSE_PROFILES'] = 'full,pgadmin'
        
        # Run just the init container (not the full stack)
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "run", "--rm", service_name],
            cwd=stack_path,
            capture_output=True,
            text=True,
            env=cleanup_env
        )
        
        if result.returncode == 0:
            success(f"    ✓ {service_name} cleanup completed")
            # Show cleanup output
            for line in result.stdout.split('\n'):
                if '[CLEAN]' in line:
                    info(f"      {line.strip()}")
            cleaned.append(service_name)
        else:
            warn(f"    ✗ {service_name} cleanup failed: {result.stderr.strip()}")
    
    return cleaned


def build_images(repo_root, use_cache=True, targets: list[str] | None = None):
    """
    Build all Docker images using docker buildx bake.
    
    Args:
        repo_root: Repository root path
        use_cache: Use Docker layer cache (default: True)
    
    Returns:
        True if build succeeded, False otherwise
    """
    info("="*70)
    info(f"BUILD: Building images (cache={'enabled' if use_cache else 'disabled'})")
    info("="*70)
    
    cmd = ["docker", "buildx", "bake"]
    if targets:
        cmd.extend(targets)
    else:
        cmd.append("all")
    cmd.append("--load")
    if not use_cache:
        cmd.append("--no-cache")
    
    info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=repo_root
    )
    
    if result.returncode != 0:
        error("Docker build failed")
        return False
    
    info("="*70)
    success("BUILD COMPLETE")
    info("="*70)
    info("")
    return True


def ensure_network(network_name):
    """
    Ensure the shared Docker network exists.
    
    Args:
        network_name: Network name from .env.ciu (DOCKER_NETWORK_INTERNAL)
    """
    if not network_name:
        raise ValueError(
            "network_name is required - must come from .env.ciu (DOCKER_NETWORK_INTERNAL)"
        )
    
    info(f"Ensuring Docker network '{network_name}' exists...")
    result = subprocess.run(
        ["docker", "network", "inspect", network_name],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        info(f"Creating Docker network '{network_name}'...")
        run_cmd(["docker", "network", "create", network_name])
        success(f"Network '{network_name}' created")
    else:
        success(f"Network '{network_name}' already exists")


def assert_devcontainer_connected_to_network(network_name, auto_connect: bool = True):
    """Ensure the devcontainer is connected to the deployment network.

    This is CRITICAL before starting containers to ensure services are accessible
    from the devcontainer for development and debugging.

    Args:
        network_name: Docker network name to verify connection to
        auto_connect: Whether to attach the devcontainer when missing

    Raises:
        SystemExit: If devcontainer connection cannot be established
    """
    try:
        if os.environ.get('IS_DEVCONTAINER') != '1':
            warn("Not running in devcontainer - skipping network connection check")
            return

        container_name = os.environ.get('DEVCONTAINER_NAME')
        if not container_name:
            error(
                "DEVCONTAINER_NAME is not set. "
                "Run: ciu --generate-env and source .env.ciu."
            )
            sys.exit(1)

        info(f"Checking devcontainer network connection: {container_name}")

        result = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                network_name,
                "--format",
                "{{range .Containers}}{{.Name}} {{end}}",
            ],
            capture_output=True,
            text=True
        )

        if container_name not in result.stdout.split():
            if auto_connect:
                info(f"Connecting devcontainer '{container_name}' to '{network_name}'")
                connect = subprocess.run(
                    ["docker", "network", "connect", network_name, container_name],
                    capture_output=True,
                    text=True
                )
                if connect.returncode != 0:
                    error(
                        f"Failed to connect devcontainer to '{network_name}': {connect.stderr.strip()}"
                    )
                    sys.exit(1)

                result = subprocess.run(
                    [
                        "docker",
                        "network",
                        "inspect",
                        network_name,
                        "--format",
                        "{{range .Containers}}{{.Name}} {{end}}",
                    ],
                    capture_output=True,
                    text=True
                )
            else:
                error(
                    f"Devcontainer '{container_name}' is not connected to '{network_name}' and auto-connect is disabled"
                )
                sys.exit(1)

        if container_name not in result.stdout.split():
            error(
                f"CRITICAL: Devcontainer '{container_name}' is NOT connected to network '{network_name}'"
            )
            error("Deployment cannot continue without devcontainer network connectivity.")
            sys.exit(1)

        success(f"✓ Devcontainer connected to '{network_name}'")

    except Exception as e:
        error(f"Failed to verify devcontainer network connection: {e}")
        sys.exit(1)
# - Works immediately after container rebuild


def start_stack(stack_path, stack_name, python_exe="python3", enable_preflight=False, profiles=None, auto_build=False, env_overrides=None, repo_root=None):
    """Start a stack using CIU.
    
    Args:
        stack_path: Path to stack directory
        stack_name: Display name for the stack
        python_exe: Python executable to use (default: python3)
        enable_preflight: Enable pre-flight Vault secret validation (default: False)
        profiles: List of Docker Compose profiles to enable (default: None)
        auto_build: Auto-build missing images (default: False, enabled with --clean)
        env_overrides: Dict of environment variables to override in service config (default: None)
    
    Note:
        No artificial wait times - relies on health checks in CIU
        
        Pre-flight validation should be enabled for services that consume
        shared secrets (ASK_VAULT:) to fail early if secrets are missing.
        
        env_overrides allows orchestrator to inject deployment-specific overrides,
        e.g., changing secret directives from GEN_LOCAL to GEN for Vault integration.
        
        auto_build enables docker compose --build flag to build missing images.
        Used with --clean to ensure images are available after cleanup.
    """
    # Track service in deployment context
    get_deployment_context().set_service(stack_name)
    
    print("\n" + "="*70)
    info(f"▶ STARTING: {stack_name}", 
         service=stack_name,
         path=str(stack_path)[:50])  # Truncate long paths
    print("="*70)
    
    if not os.path.isdir(stack_path):
        warn(f"Stack directory not found: {stack_path}, skipping")
        return False
    
    if not repo_root:
        error("repo_root is required for CIU path resolution")
        sys.exit(1)
    
    # Run with explicit environment: non-interactive mode
    env = os.environ.copy()
    env['COMPINIT_ASSUME_YES'] = '1'  # Skip interactive prompts
    # Force Python unbuffered mode for real-time output
    env['PYTHONUNBUFFERED'] = '1'
    
    # Apply orchestrator overrides (e.g., change GEN_LOCAL to GEN for Vault integration)
    if env_overrides:
        env.update(env_overrides)
        info(f"Orchestrator overrides: {', '.join(env_overrides.keys())}")
    
    # Set auto-build if requested (used with --clean to build missing images)
    if auto_build:
        env['COMPINIT_AUTO_BUILD'] = '1'
        info("Auto-build enabled (COMPINIT_AUTO_BUILD=1) - will build missing images")
    
    # Enable pre-flight validation for services that consume shared secrets
    if enable_preflight:
        env['COMPINIT_PREFLIGHT_VAULT_SECRETS'] = '1'
        info("Pre-flight Vault secret validation: ENABLED")
    
    # Set Docker Compose profiles if specified
    if profiles:
        env['COMPOSE_PROFILES'] = ','.join(profiles)
        info(f"Docker Compose profiles: {','.join(profiles)}")
    
    # Build command with config paths (CIU requires explicit directory)
    # CRITICAL: Use config_constants, NOT hardcoded filenames
    
    stack_path_obj = Path(stack_path)
    compose_defaults = stack_path_obj / STACK_CONFIG_DEFAULTS
    
    # Get global config path (FAIL-FAST: repo_root is REQUIRED)
    if not repo_root:
        error("repo_root is required for config path resolution")
        sys.exit(1)
    global_config_path = Path(repo_root) / GLOBAL_CONFIG_RENDERED
    if not global_config_path.exists():
        error(
            f"Rendered global config not found: {global_config_path}. "
            "Render ciu-global.toml before running ciu-deploy."
        )
    
    ciu_cmd = get_ciu_command(python_exe)
    cmd = ciu_cmd + ['-d', str(stack_path_obj)]
    # Note: CIU uses -d flag for directory
    # It will automatically discover ciu.defaults.toml.j2 and merge with global config
    
    info(f"Running: {' '.join(cmd)}")
    
    # Use Popen for real-time output streaming instead of capture_output
    process = subprocess.Popen(
        cmd,
        cwd=stack_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # Line-buffered
        universal_newlines=True
    )
    
    # Stream output in real-time
    if process.stdout is None:
        error("Failed to capture CIU output (stdout is None)")
        return False

    for line in process.stdout:
        print(line, end='', flush=True)
    
    # Wait for process to complete
    try:
        return_code = process.wait(timeout=300)  # 5 minute timeout per stack
    except subprocess.TimeoutExpired:
        error(
            f"Stack '{stack_name}' timed out after 300 seconds",
            service=stack_name,
            timeout_seconds=300
        )
        process.kill()
        return False
    
    if return_code != 0:
        # Record failure in deployment context
        get_deployment_context().record_failure(stack_name, f"Exit code {return_code}")
        
        error(
            f"Stack '{stack_name}' failed to start",
            service=stack_name,
            exit_code=return_code
        )
        return False
    
    # Record success in deployment context
    get_deployment_context().record_success(stack_name)
    
    print("="*70)
    success(f"✓ COMPLETED: {stack_name}", 
            service=stack_name)
    print("="*70 + "\n")
    
    return True


def check_vault_initialized(global_config):
    """
    Check if Vault is ready for initialization or already initialized.
    
    This function now accepts Vault in either state:
    - initialized=false, sealed=true (ready for post-compose hook to initialize)
    - initialized=true, sealed=false (already initialized and unsealed)
    
    The key check is: Can we communicate with Vault? (vault status succeeds)
    
    Args:
        global_config: Global configuration dictionary for container name construction
    """
    # Construct vault container name from config (fail-fast pattern)
    vault_container = get_container_name(global_config, 'vault')
    
    # First check if container is actually running
    # Use format string template separately for clarity
    format_template = '{{.Names}}'
    container_check = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={vault_container}",
            "--filter",
            "status=running",
            "--format",
            format_template,
        ],
        capture_output=True,
        text=True
    )
    
    if not container_check.stdout.strip():
        return False, "Vault container not running"
    
    # Now check Vault status - allow failures during startup
    result = subprocess.run(
        ["docker", "exec", vault_container, "vault", "status", "-format=json"],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        # Container is running but Vault process not ready yet
        return False, "Vault process starting..."
    
    # If vault status command succeeded, Vault is responsive and ready
    # Post-compose hook will handle initialization if needed
    try:
        status = json.loads(result.stdout)
        initialized = status.get('initialized', False)
        sealed = status.get('sealed', True)
        
        if initialized and not sealed:
            return True, "Vault is initialized and unsealed"
        elif not initialized and sealed:
            return True, "Vault is ready for initialization (will be handled by post-compose hook)"
        elif initialized and sealed:
            return True, "Vault is initialized but sealed (will be unsealed by post-compose hook)"
        else:
            # Unusual state but Vault is responsive
            return True, f"Vault responsive (initialized={initialized}, sealed={sealed})"
    except Exception as e:
        # If we can't parse JSON but command succeeded, still accept it
        return True, "Vault is responsive (status command succeeded)"


def load_proxy_config(repo_root, global_config):
    """Load reverse proxy configuration to get public FQDN and port."""
    external = global_config.get('topology', {}).get('external', {})
    public_fqdn = (external.get('public_fqdn') or '').strip()

    if not public_fqdn:
        raise ValueError(
            "topology.external.public_fqdn is required but missing. "
            "Run env-workspace-setup-generate.sh and source .env.ciu."
        )

    try:
        ports = external.get('ports', {})
        https_port = ports.get('https')
        if https_port is None:
            raise ValueError("topology.external.ports.https is required")
    except Exception as e:
        warn(f"Could not load topology.external.ports.https: {e}")
        raise

    return {
        'fqdn': public_fqdn,
        'port': https_port,
        'is_dynamic': False
    }


def is_reverse_proxy_enabled(global_config):
    """Return True if the reverse proxy service is enabled in deployment phases."""
    phases = global_config.get('deploy', {}).get('phases', {})
    phase_5 = phases.get('phase_5', {})

    if not phase_5 or not phase_5.get('enabled', True):
        return False

    for service in phase_5.get('services', []):
        if service.get('path') == 'infra-global/reverse-proxy' and service.get('enabled', True):
            return True

    return False


def format_access_url(config, path=""):
    """Format an access URL using configuration."""
    return f"https://{config['fqdn']}:{config['port']}{path}"


def wait_for_vault_ready(global_config, timeout=60):
    """Wait for Vault to be initialized and unsealed with retry logic.
    
    Args:
        global_config: Global configuration dictionary for container name construction
        timeout: Maximum seconds to wait for Vault
    """
    info(f"Waiting for Vault to be ready (timeout: {timeout}s)...")
    start = time.time()
    
    while time.time() - start < timeout:
        ok, msg = check_vault_initialized(global_config)
        if ok:
            success(f"Vault is ready: {msg}")
            return True
        
        # Show progress
        elapsed = int(time.time() - start)
        info(f"  [{elapsed}s] {msg}, retrying...")
        time.sleep(3)
    
    vault_container = get_container_name(global_config, 'vault')
    error(f"Vault failed to become ready within {timeout}s. Check logs: docker logs {vault_container}")
    return False


def check_container_health(container_name):
    """Check if a container is running and healthy."""
    # First check if container exists
    result = subprocess.run(
        ["docker", "inspect", "--format={{.State.Status}}", container_name],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        return False, "Not found"
    
    status = result.stdout.strip()
    
    if status != "running":
        return False, f"Status: {status}"
    
    # Check health status if container has healthcheck
    result = subprocess.run(
        ["docker", "inspect", "--format={{.State.Health.Status}}", container_name],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0 and result.stdout.strip():
        health = result.stdout.strip()
        if health == "healthy":
            return True, "Running (healthy)"
        elif health == "starting":
            return None, "Running (starting)"
        else:
            return False, f"Running ({health})"
    
    # No healthcheck defined, just check if running
    return True, "Running"


def check_postgres_ready(global_config):
    """Check if PostgreSQL is ready to accept connections.
    
    Args:
        global_config: Global configuration dictionary for container name construction
    """
    postgres_container = get_container_name(global_config, 'postgres')
    result = subprocess.run(
        ["docker", "exec", postgres_container, "pg_isready", "-U", "postgres"],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        return True, "Ready to accept connections"
    else:
        return False, "Not ready"


def check_postgres_users(global_config):
    """Check if PostgreSQL users have been created.
    
    Args:
        global_config: Global configuration dictionary for container name construction
    """
    postgres_container = get_container_name(global_config, 'postgres')
    result = subprocess.run(
        [
            "docker",
            "exec",
            postgres_container,
            "psql",
            "-U",
            "postgres",
            "-t",
            "-c",
            "SELECT usename FROM pg_user WHERE usename IN ('controller', 'workerdb', 'webapp');",
        ],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        return False, "Failed to query users"
    
    users = [u.strip() for u in result.stdout.strip().split('\n') if u.strip()]
    expected = {'controller', 'workerdb', 'webapp'}
    found = set(users)
    
    if found == expected:
        return True, f"All users created: {', '.join(sorted(users))}"
    elif found:
        missing = expected - found
        return False, f"Missing users: {', '.join(sorted(missing))}"
    else:
        return False, "No application users found"


def check_redis_ready(global_config):
    """Check if Redis is ready to accept connections.
    
    Args:
        global_config: Global configuration dictionary for container name construction
    """
    redis_container = get_container_name(global_config, 'redis')
    # Authenticate with Redis using the password already present inside
    # the container environment. This keeps the readiness probe aligned with
    # production configuration (requirepass enabled) without exposing secrets
    # to the host.
    command = (
        "if [ -n \"$REDIS_PASSWORD\" ]; then "
        "REDISCLI_AUTH=\"$REDIS_PASSWORD\" redis-cli ping; "
        "else redis-cli ping; fi"
    )

    result = subprocess.run(
        ["docker", "exec", redis_container, "sh", "-c", command],
        capture_output=True,
        text=True
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    
    if result.returncode == 0 and stdout == "PONG":
        return True, "Responding to PING"
    
    details = stdout or stderr or "Unknown error"
    return False, f"Not responding ({details})"


def check_consul_ready(global_config):
    """Check if Consul is ready to accept API requests.

    Args:
        global_config: Global configuration dictionary for container name construction
    """
    consul_container = get_container_name(global_config, 'consul')
    result = subprocess.run(
        ["docker", "exec", consul_container, "consul", "members"],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        return True, "Consul members listed"

    details = result.stderr.strip() or result.stdout.strip() or "Unknown error"
    return False, f"Consul not ready ({details})"

def wait_for_service_healthy(global_config, service_name, check_func, timeout=60, check_interval=3):
    """
    Wait for a service to become healthy using a provided check function.
    
    This is a generic health polling function that ensures services are actually
    queryable before proceeding, not just "running" according to Docker.
    
    Args:
        global_config: Global configuration dictionary for container name construction
        service_name: Human-readable service name (e.g., "PostgreSQL", "Redis")
        check_func: Function to call for health check (should take global_config as arg)
        timeout: Maximum seconds to wait for service (default: 60)
        check_interval: Seconds between health checks (default: 3)
    
    Returns:
        True if service became healthy, False if timeout reached
    
    Example:
        # Wait for PostgreSQL to accept queries, not just connections
        wait_for_service_healthy(global_config, "PostgreSQL", check_postgres_users)
        
        # Custom check: ensure database can execute SELECT 1
        def check_postgres_query():
            container = get_container_name(global_config, 'postgres')
            result = subprocess.run(
                ["docker", "exec", container, "psql", "-U", "postgres", "-c", "SELECT 1"],
                capture_output=True,
                text=True
            )
            return result.returncode == 0, "Query successful" if result.returncode == 0 else "Query failed"
        wait_for_service_healthy(global_config, "PostgreSQL queries", check_postgres_query)
    """
    info(f"Waiting for {service_name} to become healthy (timeout: {timeout}s)...")
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            ok, msg = check_func(global_config)
            if ok:
                success(f"{service_name} is healthy: {msg}")
                return True
            
            # Show progress
            elapsed = int(time.time() - start)
            info(f"  [{elapsed}s] {msg}, retrying in {check_interval}s...")
            time.sleep(check_interval)
            
        except Exception as e:
            elapsed = int(time.time() - start)
            info(f"  [{elapsed}s] Error checking health: {e}, retrying...")
            time.sleep(check_interval)
    
    container = get_container_name(global_config, service_name.lower().replace(' ', '-'))
    error(f"{service_name} failed to become healthy within {timeout}s. Check logs: docker logs {container}")
    return False


def load_vault_root_token(global_config):
    """Load Vault root token from the active Vault stack config."""
    repo_root = os.environ.get('REPO_ROOT')
    if not repo_root:
        return False, "REPO_ROOT not set (workspace environment missing)", None

    vault_config_path = Path(repo_root) / "infra" / "vault" / STACK_CONFIG_RENDERED
    if not vault_config_path.exists():
        return False, f"Vault config not found: {vault_config_path}", None

    try:
        with open(vault_config_path, 'rb') as f:
            vault_config = tomllib.load(f)
    except Exception as e:
        return False, f"Failed to parse vault config: {e}", None

    state_section = vault_config.get('state', {})
    token = state_section.get('root_token') or state_section.get('token')
    if isinstance(token, dict):
        token = token.get('value')

    if not token:
        return False, "Vault token unavailable for health check (state.root_token missing)", None

    return True, "Vault token loaded", token


def check_vault_secrets(global_config):
    """Check if Vault has generated secrets.
    
    Args:
        global_config: Global configuration dictionary for container name construction
    """
    vault_container = get_container_name(global_config, 'vault')
    ok, msg, token = load_vault_root_token(global_config)
    if not ok or not token:
        return False, msg

    # Use docker exec with explicit token environment variable
    cmd = [
        "docker",
        "exec",
        "-e",
        f"VAULT_TOKEN={token}",
        vault_container,
        "vault",
        "kv",
        "list",
        "-format=json",
        "secret/"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return False, "Cannot list secrets (token may not be set)"
    
    try:
        secrets = json.loads(result.stdout)
        if secrets:
            return True, f"{len(secrets)} secret(s) stored"
        else:
            return False, "No secrets found"
    except Exception as e:
        return False, f"Failed to parse secrets: {e}"


VAULT_DIRECTIVE_PREFIXES = (
    "GEN_TO_VAULT:",
    "ASK_VAULT:",
    "ASK_VAULT_ONCE:",
)


def _collect_vault_paths(value: object, paths: set[str]) -> None:
    """Recursively collect Vault directive paths from config values."""
    if isinstance(value, dict):
        for item in value.values():
            _collect_vault_paths(item, paths)
        return
    if isinstance(value, list):
        for item in value:
            _collect_vault_paths(item, paths)
        return
    if not isinstance(value, str):
        return

    for prefix in VAULT_DIRECTIVE_PREFIXES:
        if value.startswith(prefix):
            vault_path = value[len(prefix):].strip()
            if vault_path:
                paths.add(vault_path)
            return


def _collect_required_vault_paths(global_config: dict) -> tuple[set[str] | None, str | None]:
    """Collect Vault paths referenced by enabled stack configs."""
    repo_root = os.environ.get('REPO_ROOT')
    if not repo_root:
        return None, "REPO_ROOT not set (workspace environment missing)"

    required_paths: set[str] = set()
    deployment_phases = load_deployment_phases(global_config)

    for phase in deployment_phases:
        for service in phase.get('services', []):
            stack_path = service.get('path')
            if not stack_path:
                continue
            config_path = Path(repo_root) / stack_path / STACK_CONFIG_RENDERED
            if not config_path.exists():
                return None, f"Stack config not found: {config_path}"
            try:
                with open(config_path, 'rb') as f:
                    stack_config = tomllib.load(f)
            except Exception as e:
                return None, f"Failed to parse stack config {config_path}: {e}"

            _collect_vault_paths(stack_config, required_paths)

    return required_paths, None


def check_vault_secret_paths(global_config):
    """Verify that required Vault secret paths are readable."""
    vault_container = get_container_name(global_config, 'vault')
    ok, msg, token = load_vault_root_token(global_config)
    if not ok or not token:
        return False, msg

    required_paths, error_msg = _collect_required_vault_paths(global_config)
    if error_msg:
        return False, error_msg
    if not required_paths:
        return True, "No Vault secrets referenced by enabled stacks"

    missing = []
    for vault_path in sorted(required_paths):
        cmd = [
            "docker",
            "exec",
            "-e",
            f"VAULT_TOKEN={token}",
            vault_container,
            "vault",
            "kv",
            "get",
            "-format=json",
            f"secret/{vault_path}"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            missing.append(vault_path)

    if missing:
        return False, f"Missing Vault secrets: {', '.join(missing)}"
    return True, f"Verified {len(required_paths)} Vault secret path(s)"


def check_consul_kv_paths(global_config):
    """Verify Consul KV paths configured for service settings."""
    consul_container = get_container_name(global_config, 'consul')
    whitelist = global_config.get('consul', {}).get('whitelist', {})
    services = whitelist.get('services', {}) if isinstance(whitelist, dict) else {}
    if not services:
        return False, "Consul whitelist services not configured"

    missing = []
    for service_name, config in services.items():
        kv_path = (config or {}).get('kv_path')
        if not kv_path:
            missing.append(service_name)
            continue
        cmd = [
            "docker",
            "exec",
            consul_container,
            "consul",
            "kv",
            "get",
            kv_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            missing.append(service_name)

    if missing:
        return False, f"Missing Consul KV entries: {', '.join(missing)}"
    return True, f"Verified {len(services)} Consul KV path(s)"


def check_minio_ready(global_config):
    """Check MinIO readiness via mc inside the container."""
    minio_container = get_container_name(global_config, 'minio')
    cmd = ["docker", "exec", minio_container, "mc", "ready", "local"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True, "MinIO ready"
    return False, "MinIO not ready (mc ready failed)"


def check_minio_bucket(global_config):
    """Verify that the environment-specific MinIO bucket exists."""
    deploy_config = global_config.get('deploy', {})
    project_name = deploy_config.get('project_name')
    environment_tag = deploy_config.get('environment_tag')
    if not project_name or not environment_tag:
        return False, "Deploy project_name/environment_tag not set"

    bucket_name = f"{project_name}-{environment_tag}".lower()

    minio_container = get_container_name(global_config, 'minio')
    cmd = ["docker", "exec", minio_container, "mc", "ls", f"local/{bucket_name}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True, f"MinIO bucket exists: {bucket_name}"
    return False, f"MinIO bucket missing: {bucket_name}"


def check_service_health_endpoint(service_name, hostname, port):
    """
    Check service /health endpoint using HTTP request.
    
    Args:
        service_name: Display name of service
        hostname: Service hostname (from config)
        port: Service port (from config)
    
    Returns:
        Tuple of (ok: bool, msg: str, response_data: dict or None)
    """
    url = f"http://{hostname}:{port}/health"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DST-DNS-HealthCheck/1.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            status = data.get('status', 'unknown')
            version = data.get('version', 'unknown')
            return True, f"OK (status={status}, version={version})", data
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}", None
    except Exception as e:
        return False, f"Error: {e}", None


def normalize_check_scope(scope: str | None) -> str:
    if not scope:
        return "both"
    if scope not in {"internal", "external", "both"}:
        raise ValueError("Scope must be one of: internal, external, both")
    return scope


def resolve_external_base_url(global_config: dict) -> str | None:
    external = global_config.get('topology', {}).get('external', {})
    base_url = (external.get('base_url') or '').strip()
    if base_url:
        return base_url.rstrip('/')

    public_fqdn = (external.get('public_fqdn') or '').strip()
    ports = external.get('ports', {})
    https_port = ports.get('https')
    if not public_fqdn or https_port is None:
        return None

    return f"https://{public_fqdn}:{https_port}"


def build_external_url(base_url: str, route_path: str | None, suffix: str = "") -> str:
    base = base_url.rstrip('/')
    path = (route_path or '').strip()
    if path and not path.startswith('/'):
        path = f"/{path}"
    url = f"{base}{path}"
    if suffix:
        suffix_path = suffix if suffix.startswith('/') else f"/{suffix}"
        url = f"{url}{suffix_path}"
    return url


def check_http_json_endpoint(url: str):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DST-DNS-HealthCheck/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            status = data.get('status', 'unknown')
            version = data.get('version', 'unknown')
            return True, f"OK (status={status}, version={version})", data
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}", None
    except Exception as e:
        return False, f"Error: {e}", None


def check_http_status_ok(url: str):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DST-DNS-HealthCheck/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return True, f"HTTP {response.status}", None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}", None
    except Exception as e:
        return False, f"Error: {e}", None


def check_service_selftest_endpoint(service_name, hostname, port):
    """
    Check service /health/selftest endpoint and return test results.
    
    Args:
        service_name: Display name of service
        hostname: Service hostname (from config)
        port: Service port (from config)
    
    Returns:
        Tuple of (ok: bool, msg: str, response_data: dict or None)
    """
    url = f"http://{hostname}:{port}/health/selftest"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DST-DNS-HealthCheck/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            test_status = data.get('test_status', 'unknown')
            
            # Support both old format (tests) and new format (service_tests)
            tests = data.get('service_tests', data.get('tests', {}))
            
            # Count passed/failed tests
            if isinstance(tests, dict):
                # Support both "ok" and "success" keys
                passed = sum(1 for t in tests.values() if isinstance(t, dict) and t.get('ok', t.get('success', False)))
                failed = sum(1 for t in tests.values() if isinstance(t, dict) and not t.get('ok', t.get('success', True)))
                total = len(tests)
                
                if failed == 0 and total > 0:
                    return True, f"All tests passed ({passed}/{total})", data
                elif total == 0:
                    return None, "No tests configured", data
                else:
                    return False, f"Tests failed ({passed}/{total} passed)", data
            else:
                # tests is a string or other type (no tests configured)
                return None, str(tests), data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, "Selftest endpoint not available", None
        return False, f"HTTP {e.code}: {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}", None
    except Exception as e:
        return False, f"Error: {e}", None


def check_service_selftest_endpoint_url(url: str):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DST-DNS-HealthCheck/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            tests = data.get('service_tests', data.get('tests', {}))

            if isinstance(tests, dict):
                passed = sum(1 for t in tests.values() if isinstance(t, dict) and t.get('ok', t.get('success', False)))
                failed = sum(1 for t in tests.values() if isinstance(t, dict) and not t.get('ok', t.get('success', True)))
                total = len(tests)

                if failed == 0 and total > 0:
                    return True, f"All tests passed ({passed}/{total})", data
                elif total == 0:
                    return None, "No tests configured", data
                else:
                    return False, f"Tests failed ({passed}/{total} passed)", data
            return None, str(tests), data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, "Selftest endpoint not available", None
        return False, f"HTTP {e.code}: {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}", None
    except Exception as e:
        return False, f"Error: {e}", None


def run_health_checks(global_config, log_file=None, scope: str | None = None, include_selftests: bool = False):
    """Run comprehensive health checks on all services.
    
    Args:
        global_config: Global configuration dictionary for container name construction
        log_file: Optional path to write selftest results (default: deployment-selftest.log)
        include_selftests: Whether to include /health/selftest endpoints and deep checks
    """
    info("="*70)
    info("HEALTH CHECKS: Verifying deployment status")
    info("="*70)
    
    checks = []
    selftest_results = [] if include_selftests else None
    scope = normalize_check_scope(scope)
    run_internal = scope in {"internal", "both"}
    run_external = scope in {"external", "both"}
    external_base_url = resolve_external_base_url(global_config) if run_external else None
    
    # Open log file for selftest results (selftest runs only)
    if include_selftests:
        if log_file is None:
            log_file = Path.cwd() / "deployment-selftest.log"

        log_content = []
        log_content.append("=" * 80)
        log_content.append("DST-DNS Deployment Selftest Results")
        log_content.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        log_content.append("=" * 80)
        log_content.append("")
    else:
        log_content = None

    def append_log(line: str) -> None:
        if log_content is not None:
            log_content.append(line)
    
    # Load deployment phases to determine which services are actually enabled
    info("[INFO] Loading deployment phases to check enabled services...")
    phases = load_deployment_phases(global_config)
    enabled_services = collect_enabled_service_slugs(phases, global_config)
    info(f"[INFO] Found {len(enabled_services)} enabled services to check")
    
    def is_service_enabled(service_name):
        """Check if a service is enabled in the deployment config."""
        return normalize_service_slug(service_name) in enabled_services
    
    if run_internal:
        # Vault checks
        info("\n[Vault]")
        if is_service_enabled('vault'):
            ok, msg = check_vault_initialized(global_config)
            info(f"  Initialization: {msg}")
            checks.append(("Vault initialized", ok))
            
            ok, msg = check_vault_secrets(global_config)
            info(f"  Secrets: {msg}")
            checks.append(("Vault secrets", ok))
        else:
            info("  Skipping Vault (not enabled)")
        
        # Core services
        info("\n[Core Services]")
        
        if is_service_enabled('postgres'):
            postgres_container = get_container_name(global_config, 'postgres')
            ok, msg = check_container_health(postgres_container)
            info(f"  PostgreSQL container: {msg}")
            checks.append(("PostgreSQL container", ok))
            
            if ok:
                ok, msg = check_postgres_ready(global_config)
                info(f"  PostgreSQL ready: {msg}")
                checks.append(("PostgreSQL ready", ok))
                
                ok, msg = check_postgres_users(global_config)
                info(f"  PostgreSQL users: {msg}")
                checks.append(("PostgreSQL users", ok))
        else:
            info("  Skipping PostgreSQL (not enabled)")
        
        if is_service_enabled('redis'):
            redis_container = get_container_name(global_config, 'redis')
            ok, msg = check_container_health(redis_container)
            info(f"  Redis container: {msg}")
            checks.append(("Redis container", ok))
            
            if ok:
                ok, msg = check_redis_ready(global_config)
                info(f"  Redis ready: {msg}")
                checks.append(("Redis ready", ok))
        else:
            info("  Skipping Redis (not enabled)")

        if is_service_enabled('consul'):
            consul_container = get_container_name(global_config, 'consul')
            ok, msg = check_container_health(consul_container)
            info(f"  Consul container: {msg}")
            checks.append(("Consul container", ok))

            if ok:
                ok, msg = check_consul_ready(global_config)
                info(f"  Consul ready: {msg}")
                checks.append(("Consul ready", ok))
                if include_selftests:
                    ok, msg = check_consul_kv_paths(global_config)
                    info(f"  Consul KV paths: {msg}")
                    checks.append(("Consul KV paths", ok))
        else:
            info("  Skipping Consul (not enabled)")
    else:
        info("\n[Vault]")
        info("  Skipping internal Vault checks (scope=external)")
        info("\n[Core Services]")
        info("  Skipping internal core checks (scope=external)")
    
    # Application services with dynamic config
    if run_internal:
        info("\n[Application Services]")
        append_log("[Application Services Health Endpoints]")
        append_log("")
        
        # Extract service definitions from global config
        app_services_config = [
            ('controller', 'controller'),
            ('worker_io', 'worker-io'),
            ('worker_db', 'worker-db'),
            ('webapp_server', 'webapp-server'),
            ('webapp_ui', 'webapp-ui'),
        ]
        
        for config_key, display_name in app_services_config:
            # Skip if service is not enabled
            if not is_service_enabled(display_name):
                info(f"  Skipping {display_name} (not enabled)")
                continue
            # Get service config (handle both direct and nested configs)
            service_config = global_config.get(config_key, {})
            
            # Get service name and port from config
            service_name = service_config.get('name', display_name)
            internal_port = service_config.get('internal_port', 8080)
            
            # Check container health first
            container_name = get_container_name(global_config, service_name)
            ok, msg = check_container_health(container_name)
            info(f"  {display_name} container: {msg}")
            checks.append((f"{display_name} container", ok))
            
            # If container is running, check HTTP health endpoint
            if ok and display_name not in ['webapp-ui']:  # webapp-ui is nginx, no /health endpoint
                ok, msg, data = check_service_health_endpoint(display_name, service_name, internal_port)
                info(f"  {display_name} /health: {msg}")
                checks.append((f"{display_name} /health", ok))
                
                append_log(f"Service: {display_name}")
                append_log(f"  Endpoint: http://{service_name}:{internal_port}/health")
                append_log(f"  Status: {msg}")
                if data:
                    append_log(f"  Response: {json.dumps(data, indent=4)}")
                append_log("")
                
                # Check selftest endpoint (if available)
                # All application services now have selftest endpoints
                if include_selftests and display_name in ['controller', 'webapp-server', 'worker-io', 'worker-db']:
                    ok_test, msg_test, test_data = check_service_selftest_endpoint(
                        display_name, service_name, internal_port
                    )
                    if ok_test is not None:  # Endpoint exists
                        info(f"  {display_name} /health/selftest: {msg_test}")
                        checks.append((f"{display_name} selftest", ok_test))
                        
                        append_log(f"Service: {display_name}")
                        append_log(f"  Endpoint: http://{service_name}:{internal_port}/health/selftest")
                        append_log(f"  Status: {msg_test}")
                        if test_data:
                            # Pretty print test results - support both formats
                            tests = test_data.get('service_tests', test_data.get('tests', {}))
                            if isinstance(tests, dict):
                                for test_name, test_result in tests.items():
                                    if isinstance(test_result, dict):
                                        # Support both "ok" and "success" keys
                                        is_ok = test_result.get('ok', test_result.get('success', False))
                                        status_icon = "✓" if is_ok else "✗"
                                        message = test_result.get('message', test_result.get('note', test_result.get('error', 'No message')))
                                        append_log(f"    {status_icon} {test_name}: {message}")
                                    else:
                                        append_log(f"    {test_name}: {test_result}")
                            else:
                                append_log(f"    {tests}")
                        append_log("")
                        if selftest_results is not None:
                            selftest_results.append((display_name, ok_test, test_data))
    else:
        info("\n[Application Services]")
        info("  Skipping internal application checks (scope=external)")

    if run_external:
        info("\n[External Routes]")
        append_log("[External Routes Health Endpoints]")
        append_log("")

        if not external_base_url:
            msg = "External base URL not configured"
            if scope == "external":
                error(msg)
                checks.append(("External base URL", False))
            else:
                warn(msg + "; skipping external checks")
        elif not is_reverse_proxy_enabled(global_config):
            msg = "Reverse proxy not enabled; skipping external checks"
            if scope == "external":
                error(msg)
                checks.append(("External routes", False))
            else:
                warn(msg)
        else:
            info(f"  Base URL: {external_base_url}")
            routes_config = global_config.get('topology', {}).get('routes', {})

            external_services = [
                ('controller', 'controller', True),
                ('webapp_server', 'webapp-server', True),
                ('webapp_ui', 'webapp-ui', False),
            ]

            for route_key, display_name, supports_health in external_services:
                if not is_service_enabled(display_name):
                    info(f"  Skipping {display_name} (not enabled)")
                    continue

                route_path = (routes_config.get(route_key, {}) or {}).get('path')
                if not route_path:
                    msg = f"Missing external route for {display_name}"
                    if scope == "external":
                        error(msg)
                        checks.append((f"{display_name} external route", False))
                    else:
                        warn(msg)
                    continue

                if supports_health:
                    health_url = build_external_url(external_base_url, route_path, "/health")
                    info(f"  {display_name} external /health URL: {health_url}")
                    ok, msg, data = check_http_json_endpoint(health_url)
                    info(f"  {display_name} external /health: {msg}")
                    checks.append((f"{display_name} external /health", ok))

                    append_log(f"Service: {display_name}")
                    append_log(f"  Endpoint: {health_url}")
                    append_log(f"  Status: {msg}")
                    if data:
                        append_log(f"  Response: {json.dumps(data, indent=4)}")
                    append_log("")

                    if include_selftests:
                        selftest_url = build_external_url(external_base_url, route_path, "/health/selftest")
                        info(f"  {display_name} external /health/selftest URL: {selftest_url}")
                        ok_test, msg_test, test_data = check_service_selftest_endpoint_url(selftest_url)
                        if ok_test is not None:
                            info(f"  {display_name} external /health/selftest: {msg_test}")
                            checks.append((f"{display_name} external selftest", ok_test))

                            append_log(f"Service: {display_name}")
                            append_log(f"  Endpoint: {selftest_url}")
                            append_log(f"  Status: {msg_test}")
                            if test_data:
                                tests = test_data.get('service_tests', test_data.get('tests', {}))
                                if isinstance(tests, dict):
                                    for test_name, test_result in tests.items():
                                        if isinstance(test_result, dict):
                                            is_ok = test_result.get('ok', test_result.get('success', False))
                                            status_icon = "✓" if is_ok else "✗"
                                            message = test_result.get('message', test_result.get('note', test_result.get('error', 'No message')))
                                            append_log(f"    {status_icon} {test_name}: {message}")
                                        else:
                                            append_log(f"    {test_name}: {test_result}")
                                else:
                                    append_log(f"    {tests}")
                            append_log("")
                            if selftest_results is not None:
                                selftest_results.append((f"{display_name} external", ok_test, test_data))
                else:
                    external_url = build_external_url(external_base_url, route_path)
                    info(f"  {display_name} external route URL: {external_url}")
                    ok, msg, _ = check_http_status_ok(external_url)
                    info(f"  {display_name} external route: {msg}")
                    checks.append((f"{display_name} external route", ok))

                    append_log(f"Service: {display_name}")
                    append_log(f"  Endpoint: {external_url}")
                    append_log(f"  Status: {msg}")
                    append_log("")
    
    # Deep infrastructure checks (selftest only)
    if include_selftests and run_internal:
        info("\n[Deep Infrastructure]")
        if is_service_enabled('vault'):
            ok, msg = check_vault_secret_paths(global_config)
            info(f"  Vault secret paths: {msg}")
            checks.append(("Vault secret paths", ok))
        if is_service_enabled('minio'):
            ok, msg = check_minio_ready(global_config)
            info(f"  MinIO ready: {msg}")
            checks.append(("MinIO ready", ok))
            ok, msg = check_minio_bucket(global_config)
            info(f"  MinIO bucket: {msg}")
            checks.append(("MinIO bucket", ok))

    # Observability services
    if run_internal:
        info("\n[Observability]")
        obs_services = [
            ("otel-aggregator", "OTel Aggregator"),
            ("skywalking-oap", "Skywalking OAP"),
            ("skywalking-ui", "Skywalking UI"),
            ("cadvisor", "cAdvisor"),
        ]
        for service_name, label in obs_services:
            if not is_service_enabled(service_name):
                info(f"  Skipping {label} (not enabled)")
                continue
            container_name = get_container_name(global_config, service_name)
            ok, msg = check_container_health(container_name)
            info(f"  {label}: {msg}")
            checks.append((label, ok))
    else:
        info("\n[Observability]")
        info("  Skipping internal observability checks (scope=external)")
    
    # Infrastructure services
    if run_internal:
        info("\n[Infrastructure]")
        infra_services = [
            ("reverse-proxy", "Reverse Proxy"),
            ("registry", "Docker Registry"),
            ("pgadmin", "pgAdmin"),
            ("webhook-listener", "Webhook Listener"),
            ("webhook-dispatcher", "Webhook Dispatcher"),
        ]
        for service_name, label in infra_services:
            if not is_service_enabled(service_name):
                info(f"  Skipping {label} (not enabled)")
                continue
            container_name = get_container_name(global_config, service_name)
            ok, msg = check_container_health(container_name)
            info(f"  {label}: {msg}")
            checks.append((label, ok))
    else:
        info("\n[Infrastructure]")
        info("  Skipping internal infrastructure checks (scope=external)")
    
    # Write selftest results to log file (selftest runs only)
    if include_selftests and log_content is not None and selftest_results is not None:
        log_content.append("=" * 80)
        log_content.append("Summary")
        log_content.append("=" * 80)
        selftest_passed = sum(1 for _, ok, _ in selftest_results if ok)
        selftest_failed = sum(1 for _, ok, _ in selftest_results if not ok)
        log_content.append(f"Selftest endpoints checked: {len(selftest_results)}")
        log_content.append(f"  Passed: {selftest_passed}")
        log_content.append(f"  Failed: {selftest_failed}")
        log_content.append("")
        
        try:
            with open(log_file, 'w') as f:
                f.write('\n'.join(log_content))
            info(f"\n📝 Selftest results written to: {log_file}")
        except Exception as e:
            warn(f"Failed to write selftest log: {e}")
    
    # Summary
    info("="*70)
    passed = sum(1 for _, ok in checks if ok is True)
    failed = sum(1 for _, ok in checks if ok is False)
    pending = sum(1 for _, ok in checks if ok is None)
    total = len(checks)
    
    if failed == 0 and pending == 0:
        success(f"HEALTH CHECKS PASSED: {passed}/{total} checks successful")
    elif failed == 0:
        warn(f"HEALTH CHECKS PENDING: {passed}/{total} passed, {pending} starting")
    else:
        warn(f"HEALTH CHECKS: {passed} passed, {failed} failed, {pending} pending")
        info("\nFailed checks:")
        for name, ok in checks:
            if ok is False:
                info(f"  ❌ {name}")
    
    info("="*70)
    
    return failed == 0

def print_config_context(repo_root):
    """
    Print evaluated configuration context for inspection and debugging.
    Shows all configuration values that would be used during deployment.
    """
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}CONFIGURATION CONTEXT{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")
    
    # Load global config
    try:
        global_config = load_global_config(repo_root)
    except Exception as e:
        error(f"Failed to load global config: {e}")
        return False
    
    deploy = global_config.get('deploy', {})
    
    # Repository paths
    print(f"{GREEN}[Repository Paths]{RESET}")
    print(f"  REPO_ROOT:          {os.environ.get('REPO_ROOT', repo_root)}")
    print(f"  PHYSICAL_REPO_ROOT: {os.environ.get('PHYSICAL_REPO_ROOT', repo_root)}")
    print(f"  Config file:        {repo_root / GLOBAL_CONFIG_RENDERED}")
    print()
    
    # Project identity
    print(f"{GREEN}[Project Identity]{RESET}")
    print(f"  Project name:    {deploy.get('project_name', 'NOT SET')}")
    print(f"  Environment:     {deploy.get('environment_tag', 'NOT SET')}")
    print(f"  Docker network:  {deploy.get('network_name', 'NOT SET')}")
    print()
    
    # Service URLs
    print(f"{GREEN}[Service URLs]{RESET}")
    services_config = global_config.get('topology', {}).get('services', {})
    for service_name, service_config in services_config.items():
        if isinstance(service_config, dict):
            host = service_config.get('internal_host')
            port = service_config.get('internal_port')
            if host and port is not None:
                print(f"  {service_name:20s} http://{host}:{port}")
    print()
    
    # Public access
    print(f"{GREEN}[Public Access]{RESET}")
    external = global_config.get('topology', {}).get('external', {})
    public_fqdn = external.get('public_fqdn', 'NOT SET')
    base_url = external.get('base_url', '')
    print(f"  Public FQDN:     {public_fqdn}")
    if base_url:
        print(f"  External URL:    {base_url}")
    print()
    
    # Deployment phases
    print(f"{GREEN}[Deployment Phases]{RESET}")
    phases = global_config.get('deploy', {}).get('phases', {})
    for phase_key in sorted(phases.keys()):
        phase = phases[phase_key]
        if isinstance(phase, dict):
            enabled = phase.get('enabled', True)
            name = phase.get('name', phase_key)
            status = f"{GREEN}ENABLED{RESET}" if enabled else f"{YELLOW}DISABLED{RESET}"
            print(f"  {phase_key}: {name:30s} [{status}]")
            if enabled:
                services = phase.get('services', [])
                for svc in services:
                    if isinstance(svc, dict):
                        svc_name = svc.get('name', 'unknown')
                        print(f"    - {svc_name}")
    print()
    
    # Environment variables
    print(f"{GREEN}[Key Environment Variables]{RESET}")
    env_vars = [
        'VAULT_ADDR', 'VAULT_TOKEN', 'PUBLIC_FQDN',
        'CONTAINER_UID', 'CONTAINER_GID', 'DOCKER_GID'
    ]
    for var in env_vars:
        value = os.environ.get(var, 'NOT SET')
        # Redact sensitive values
        if 'TOKEN' in var or 'PASSWORD' in var:
            value = value[:8] + '...' if value and value != 'NOT SET' else value
        print(f"  {var:20s} {value}")
    print()
    
    print(f"{BLUE}{'='*70}{RESET}\n")
    return True


def render_all_configs(
    repo_root: Path,
    deployment_phases: list[dict],
    selected_phases: list[int] | None
) -> None:
    """Render ciu-global.toml and stack ciu.toml files for enabled phases."""
    anchor_dir = find_stack_anchor(repo_root)
    info(
        "Rendering ciu-global.toml via CIU using stack "
        f"{anchor_dir.relative_to(repo_root)}"
    )

    global_config = render_global_config(repo_root)
    phases = load_deployment_phases(global_config)

    if selected_phases:
        filtered_phases = []
        for phase in phases:
            phase_key = phase.get('key', '')
            if phase_key.startswith('phase_'):
                try:
                    phase_num = int(phase_key.split('_')[1])
                except (IndexError, ValueError):
                    continue
                if phase_num in selected_phases:
                    filtered_phases.append(phase)
        phases = filtered_phases

    stack_paths: set[Path] = set()
    for phase in phases:
        for service in phase.get('services', []):
            if not service.get('enabled', True):
                continue
            service_path = service.get('path')
            if not service_path:
                continue
            stack_paths.add(repo_root / service_path)

    if not stack_paths:
        warn("No stack paths found to render")
        return

    info(f"Rendering ciu.toml for {len(stack_paths)} stack(s)")
    for stack_path in sorted(stack_paths, key=lambda path: str(path)):
        info(f"  Rendering stack: {stack_path.relative_to(repo_root)}")

    render_stack_configs(stack_paths, global_config, preserve_state=True)


def ensure_global_config_rendered(
    repo_root: Path,
    _python_exe: str,
    deployment_phases: list[dict]
) -> dict:
    """Ensure ciu-global.toml and stack ciu.toml files are rendered.

    Returns the loaded global configuration.
    """
    global_config = render_global_config(repo_root)

    stack_paths: set[Path] = set()
    for phase in deployment_phases:
        for service in phase.get('services', []):
            if not service.get('enabled', True):
                continue
            service_path = service.get('path')
            if not service_path:
                continue
            stack_paths.add(repo_root / service_path)

    if stack_paths:
        info(f"Ensuring ciu.toml for {len(stack_paths)} stack(s)")
        render_stack_configs(stack_paths, global_config, preserve_state=True)

    return global_config

# Registry deployment is now handled by:
# 1. CIU validates deploy.registry.url (empty for local, non-empty for external)
# 2. Deployment phases include registry service if needed
# 3. No orchestrator-level decision needed - config-driven only


def load_deployment_phases(global_config):
    """
    Load deployment phases from global configuration with debug output.
    
    Returns a list of phase dictionaries with evaluated enabled status.
    """
    phases_config = global_config.get('deploy', {}).get('phases', {})
    deployment_config = global_config.get('deploy', {})
    
    print("\n[DEBUG] === Evaluating Deployment Phases ===", flush=True)
    
    phases = []
    
    # Sort phases by key to ensure consistent order
    for phase_key in sorted(phases_config.keys()):
        phase_data = phases_config[phase_key]
        
        print(f"[DEBUG] Phase {phase_key}:", flush=True)
        print(f"[DEBUG]   Name: {phase_data.get('name', 'UNNAMED')}", flush=True)
        
        # Evaluate enabled condition
        enabled_expr = phase_data.get('enabled', True)
        print(f"[DEBUG]   Enabled expression: {enabled_expr}", flush=True)
        
        if isinstance(enabled_expr, str):
                # Evaluate expression with deploy.control values in context
            try:
                # Build evaluation context with control flags
                eval_context = {
                    "__builtins__": {},
                    **deployment_config.get('control', {})
                }
                enabled = eval(enabled_expr, {"__builtins__": {}}, eval_context)
            except Exception as e:
                warn(f"Failed to evaluate enabled condition '{enabled_expr}' for phase {phase_key}: {e}")
                enabled = False
        else:
            enabled = bool(enabled_expr)
        
        if not enabled:
            continue
            
        # Process services
        services = []
        for service in phase_data.get('services', []):
                # Evaluate service-level enabled condition
            service_enabled_expr = service.get('enabled', True)
            if isinstance(service_enabled_expr, str):
                try:
                    # Build evaluation context with control flags
                    eval_context = {
                        "__builtins__": {},
                        **deployment_config.get('control', {})
                    }
                    service_enabled = eval(service_enabled_expr, {"__builtins__": {}}, eval_context)
                except Exception as e:
                    warn(f"Failed to evaluate service enabled condition '{service_enabled_expr}' for {service.get('name', service.get('path', 'unknown'))}: {e}")
                    service_enabled = False
            else:
                service_enabled = bool(service_enabled_expr)
            
            if service_enabled:
                services.append(service)
        
        if services:  # Only include phases that have enabled services
            phases.append({
                'key': phase_key,
                'name': phase_data.get('name', phase_key),
                'description': phase_data.get('description', ''),
                'services': services,
                'env_overrides': phase_data.get('env_overrides', [])
            })
    
    print(f"\n[DEBUG] Total active phases: {len(phases)}", flush=True)
    print("[DEBUG] === End Phase Evaluation ===\n", flush=True)
    
    return phases


def execute_deployment_phase(phase, repo_root, python_exe, args, global_config):
    # Note: args parameter kept for compatibility but rebuild flag now comes from environment
    """
    Execute a single deployment phase.
    
    Args:
        phase: Phase dictionary from load_deployment_phases
        repo_root: Repository root path
        python_exe: Python executable path
        args: Command line arguments
        global_config: Global configuration dictionary
    """
    print("\n" + "#" * 70)
    print(f"{GREEN}### {phase['key'].upper()}: {phase['name']} ###{RESET}")
    print("#" * 70)
    
    if phase.get('description'):
        info(phase['description'])
    
    # Special handling for certain phases
    if phase['key'] == 'phase_1':
        # Vault phase - wait for readiness and load credentials
        vault_path = repo_root / "infra" / "vault"
        start_stack(vault_path, "Vault", python_exe=python_exe, repo_root=repo_root)
        
        # Wait for Vault to be ready with retry logic
        if not wait_for_vault_ready(global_config, timeout=60):
            vault_container = get_container_name(global_config, 'vault')
            error(f"Vault failed to become ready. Check logs: docker logs {vault_container}")
        
        if args.vault_only:
            success("Vault started. Use --services-only to start remaining services.")
            return
    
    elif phase['key'] == 'phase_2':
        # Core data services phase
        # Note: pgAdmin is included in db-core compose but controlled by profile
        # The compose file handles the profile internally based on its own config
        pass
    
    elif phase['key'] == 'phase_4':
        # Application services phase
        # Auto-build if --clean was used (build missing images)
        auto_build = args.clean
        if auto_build:
            info("🔨 Auto-build enabled via --clean flag")
            info("   Missing Docker images will be built automatically")
        
        # Add auto-build flag to all services in this phase
        for service in phase['services']:
            service['auto_build'] = auto_build
    
    # Execute all services in this phase
    for service in phase['services']:
        service_path = repo_root / service['path']
        service_name = service['name']
        
        # Build start_stack arguments
        start_kwargs = {
            'python_exe': python_exe
        }
        
        # Add optional parameters
        if service.get('preflight'):
            start_kwargs['enable_preflight'] = True
        
        if service.get('profiles'):
            start_kwargs['profiles'] = service['profiles']
        
        if service.get('auto_build'):
            start_kwargs['auto_build'] = service['auto_build']
        
        # Combine phase-level and service-level env_overrides
        env_overrides = {}
        
        # Phase-level overrides (applied to all services in phase)
        phase_overrides = phase.get('env_overrides', [])
        for override in phase_overrides:
            if '=' in override:
                key, value = override.split('=', 1)
                env_overrides[key.strip()] = value.strip()
        
        # Service-level overrides (applied to this specific service)
        service_overrides = service.get('env_overrides', [])
        for override in service_overrides:
            if '=' in override:
                key, value = override.split('=', 1)
                env_overrides[key.strip()] = value.strip()
        
        if env_overrides:
            start_kwargs['env_overrides'] = env_overrides
        
        # Pass repo_root for config path resolution
        start_kwargs['repo_root'] = repo_root
        
        start_stack(service_path, service_name, **start_kwargs)


def main():
    # Initialize deployment context for tracking
    ctx = get_deployment_context()
    
    parser = argparse.ArgumentParser(
        description="DST-DNS Deployment Orchestrator - Action-based deployment control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Execution Order:
  Actions execute in the order specified on the command line.
  If no actions are specified, --deploy is the default.

Named Service Groups:
    Groups are defined in ciu-global.toml [deploy.groups]
  Built-in groups: infra, apps, observability, minimal, full

Examples:
  %(prog)s                              # Deploy (default)
    %(prog)s --start                      # Explicit start (alias for deploy)
  %(prog)s --stop                       # Stop containers
  %(prog)s --stop --services-only       # Stop only application services (keep infra)
    %(prog)s --render-toml                # Render global + stack TOML files
  %(prog)s --stop --clean --deploy      # Full restart with clean state
    %(prog)s --reset                      # Alias for clean
  %(prog)s --print-config-context       # Inspect configuration
  %(prog)s --phases 1,2 --deploy        # Deploy only phases 1 and 2
  %(prog)s --groups infra --deploy      # Deploy infrastructure only
  %(prog)s --groups apps --deploy       # Deploy applications only
  %(prog)s --groups minimal --deploy    # Minimal set for testing
  %(prog)s --list-groups                # Show available groups
    %(prog)s --deploy --healthcheck       # Deploy then check health (internal + external)
    %(prog)s --deploy --healthcheck internal  # Deploy then check internal health only
    %(prog)s --deploy --healthcheck external  # Deploy then check external routes only

Shared functionality:
    - Uses .env.ciu for REPO_ROOT, PHYSICAL_REPO_ROOT, and DOCKER_NETWORK_INTERNAL.
    - Ensures .env.ciu is generated when missing (use --generate-env to refresh).
        - Renders TOML with INSTANCE_ID-based values and (when enabled) connects
            devcontainers to DOCKER_NETWORK_INTERNAL before running deploy actions.
        """
    )
    
    # Actions (mutually composable, executed in order)
    action_group = parser.add_argument_group('Actions')
    action_group.add_argument('--stop', action='store_true',
                             help='Stop all containers (preserves volumes)')
    action_group.add_argument('--clean', action='store_true',
                             help='Clean volumes and data')
    action_group.add_argument('--reset', action='store_true',
                             help='Alias for --clean (matches ciu --reset)')
    action_group.add_argument('--build', action='store_true',
                             help='Build Docker images')
    action_group.add_argument('--build-no-cache', action='store_true',
                             help='Build images from scratch (no cache)')
    action_group.add_argument('--start', action='store_true',
                             help='Alias for --deploy (explicit start action)')
    action_group.add_argument('--deploy', action='store_true',
                             help='Deploy services (default if no actions)')
    action_group.add_argument('--render-toml', action='store_true',
                             help='Render ciu-global.toml and stack ciu.toml files')
    action_group.add_argument('--healthcheck', nargs='?', const='both',
                             choices=['internal', 'external', 'both'],
                             help='Run health checks (internal|external|both, default: both)')
    action_group.add_argument('--selftest', nargs='?', const='both',
                             choices=['internal', 'external', 'both'],
                             help='Run self-tests (internal|external|both, default: both)')
    action_group.add_argument('--print-config-context', action='store_true',
                             help='Print evaluated configuration and exit')
    action_group.add_argument('--list-groups', action='store_true',
                             help='List available service groups and exit')
    
    # Options (modifiers)
    option_group = parser.add_argument_group('Options')
    option_group.add_argument('--services-only', action='store_true',
                             help='Only stop application services (keep infrastructure running)')
    option_group.add_argument('--phases',
                             help='Comma-separated phase numbers to deploy (e.g., 1,2,3)')
    option_group.add_argument('--groups',
                             help='Comma-separated named groups to deploy (e.g., infra,apps)')
    option_group.add_argument('--ignore-errors', action='store_true',
                             help='Continue execution on errors')
    option_group.add_argument('--warnings-as-errors', action='store_true',
                             help='Treat warnings as errors')
    option_group.add_argument('--repo-root', type=str, default=None,
                             help='Repository root directory (default: current working directory)')
    option_group.add_argument('--root-folder', dest='repo_root', type=str, default=None,
                             help='Alias for --repo-root')
    option_group.add_argument('--generate-env', action='store_true',
                             help='Generate .env.ciu with autodetected values')
    option_group.add_argument('--update-cert-permission', action='store_true',
                             help='Update Let\'s Encrypt cert permissions (requires root)')
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
    option_group.add_argument(
        '--version',
        action='version',
        version=f"ciu-deploy {get_cli_version()}"
    )
    
    args = parser.parse_args()
    
    # Log deployment start with ID
    info(f"Starting deployment (ID: {ctx.deployment_id})")

    # Load workspace environment (.env.ciu) and validate required keys.
    # When a subtree is used as repo root, load the env file from that subtree
    # so REPO_ROOT/PHYSICAL_REPO_ROOT reflect the override instead of the parent.
    try:
        env_root = bootstrap_workspace_env(
            start_dir=Path.cwd(),
            define_root=Path(args.repo_root).resolve() if args.repo_root else None,
            defaults_filename=GLOBAL_CONFIG_DEFAULTS,
            generate_env=args.generate_env,
            update_cert_permission=args.update_cert_permission,
            required_keys=[
                'REPO_ROOT',
                'PHYSICAL_REPO_ROOT',
                'PUBLIC_FQDN',
                'PUBLIC_TLS_CRT_PEM',
                'PUBLIC_TLS_KEY_PEM',
                'DOCKER_GID',
                'CONTAINER_UID',
                'CONTAINER_GID',
                'USER_UID',
                'USER_GID',
                'DOCKER_NETWORK_INTERNAL'
            ],
        )
    except WorkspaceEnvError as env_error:
        error(str(env_error))
        return 1

    standalone_root = detect_standalone_root(env_root)
    if standalone_root:
        env_repo_root = Path(os.environ.get("REPO_ROOT", "")).resolve()
        if env_repo_root and env_repo_root != standalone_root:
            error(
                "standalone_root is true but REPO_ROOT does not match. "
                f"Expected: {standalone_root}, got: {env_repo_root}. "
                "Regenerate .env.ciu from the standalone root."
            )
    
    # Get repository root (env or --repo-root argument).
    # If --repo-root is inside the current REPO_ROOT, remap PHYSICAL_REPO_ROOT
    # to the corresponding subtree on the host to keep bind mounts aligned.
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
        info(f"Using repository root from --repo-root/--root-folder: {repo_root}")
        env_repo_root_value = os.environ.get('REPO_ROOT')
        env_physical_root_value = os.environ.get('PHYSICAL_REPO_ROOT')
        if env_repo_root_value:
            env_repo_root = Path(env_repo_root_value).resolve()
            if env_repo_root != repo_root:
                warn(f"REPO_ROOT from .env.ciu differs: {env_repo_root}")
            try:
                rel_path = repo_root.relative_to(env_repo_root)
            except ValueError:
                rel_path = None
            if rel_path and env_physical_root_value:
                physical_repo_root = Path(env_physical_root_value).resolve() / rel_path
                os.environ['PHYSICAL_REPO_ROOT'] = str(physical_repo_root)
        os.environ['REPO_ROOT'] = str(repo_root)
    else:
        repo_root = Path(os.environ['REPO_ROOT']).resolve()
        if not repo_root.exists():
            error(f"Repository root does not exist: {repo_root}")
            sys.exit(1)
        info(f"Using repository root from .env.ciu: {repo_root}")
    
    # Fail-fast: Validate global config defaults template exists
    global_config_defaults = repo_root / GLOBAL_CONFIG_DEFAULTS
    if not global_config_defaults.exists():
        error(f"Global config defaults not found: {global_config_defaults}")
        error("This does not appear to be a valid DST-DNS repository root.")
        error("Either:")
        error("  1. Run this script from the repository root directory")
        error("  2. Use --repo-root <path> to specify the repository root")
        sys.exit(1)
    
    os.chdir(repo_root)
    info(f"Working directory: {repo_root}")

    # Render global configuration to resolve INSTANCE_ID-based values
    global_config = render_global_config(repo_root)
    set_debug_enabled(global_config.get('deploy', {}).get('log_level'))
    for line in build_global_config_debug_lines(global_config):
        debug(line)

    auto_connect_setting = global_config.get('ciu', {}).get('auto_connect_network', True)
    if args.auto_connect_network is not None:
        auto_connect_setting = args.auto_connect_network

    # Debug: Print workspace environment values
    info("="*70)
    info("DEBUG: Workspace Environment")
    info("="*70)
    info(f"  USER_UID: {os.environ.get('USER_UID')}")
    info(f"  USER_GID: {os.environ.get('USER_GID')}")
    info(f"  DOCKER_GID: {os.environ.get('DOCKER_GID')}")
    info(f"  DEVCONTAINER_NAME: {os.environ.get('DEVCONTAINER_NAME', 'N/A')}")
    info(f"  DOCKER_NETWORK_INTERNAL: {os.environ.get('DOCKER_NETWORK_INTERNAL')}")
    info("="*70)
    
    # FAIL-FAST: project_name is REQUIRED (no defaults, no fallbacks)
    deploy = global_config.get('deploy', {})
    if not deploy.get('project_name'):
        error("CRITICAL: deploy.project_name not set in ciu-global.toml")
        error("Define deploy.project_name in ciu-global.toml.j2")
        error("Example: project_name = \"dstdns\"")
        sys.exit(1)
    
    # Get network name from .env.ciu (single source of truth)
    network_name = deploy.get('network_name') or os.environ.get('DOCKER_NETWORK_INTERNAL')
    if not network_name:
        error("CRITICAL: DOCKER_NETWORK_INTERNAL not set in .env.ciu")
        error("Run: ciu --generate-env and source .env.ciu")
        sys.exit(1)

    python_exe = get_python_executable(repo_root)
    
    # Handle --list-groups (exit immediately after printing)
    if args.list_groups:
        list_available_groups(global_config)
        return 0
    
    # Collect actions to execute (in order specified)
    actions = build_action_sequence(sys.argv)
    
    # Default action: deploy
    if not actions:
        actions = ['deploy']
        info("No actions specified, defaulting to --deploy")
    
    info(f"Actions to execute (in order): {', '.join(actions)}")
    
    # Parse phases if specified (can come from --phases or --groups)
    selected_phases = None
    selected_phase_keys: Set[str] = set()
    
    # Handle --phases argument
    if args.phases:
        try:
            selected_phases = [int(p.strip()) for p in args.phases.split(',')]
            selected_phase_keys = {f"phase_{p}" for p in selected_phases}
            info(f"Selected phases: {selected_phases}")
        except ValueError:
            error(f"Invalid phases format: {args.phases}. Use comma-separated numbers (e.g., 1,2,3)")
            return 1
    
    # Handle --groups argument (resolve to phase numbers)
    if args.groups:
        if args.phases:
            error("Cannot specify both --phases and --groups")
            error("Use one or the other")
            return 1
        
        group_names = [g.strip() for g in args.groups.split(',')]
        selected_phase_keys = resolve_groups_to_phases(global_config, group_names)
        # Convert back to list of phase numbers for existing logic
        selected_phases = []
        for key in selected_phase_keys:
            if key.startswith('phase_'):
                try:
                    selected_phases.append(int(key.split('_')[1]))
                except (IndexError, ValueError):
                    pass
        selected_phases.sort()
        info(f"Resolved groups to phases: {selected_phases}")
    
    deployment_phases = load_deployment_phases(global_config)
    selected_deployment_phases = filter_deployment_phases(deployment_phases, selected_phase_keys)
    selected_service_slugs = (
        collect_enabled_service_slugs(selected_deployment_phases, global_config)
        if selected_phase_keys
        else set()
    )
    selected_bake_targets = collect_bake_targets_from_phases(selected_deployment_phases)

    # Execute actions in order
    for action in actions:
        try:
            info(f"\n{BLUE}>>> Executing action: {action.upper()}{RESET}\n")
            
            if action == 'print_config_context':
                if not print_config_context(repo_root):
                    error("Failed to print configuration context")
                    if not args.ignore_errors:
                        return 1
                # Exit after printing config
                return 0
            
            elif action == 'stop':
                stop_deployment(
                    repo_root,
                    services_only=args.services_only,
                    selected_service_slugs=selected_service_slugs or None,
                )
            
            elif action == 'clean':
                cleanup_deployment(repo_root, selected_phase_keys=selected_phase_keys or None)

            elif action == 'render_toml':
                render_all_configs(repo_root, selected_deployment_phases, selected_phases)
            
            elif action == 'build':
                targets = selected_bake_targets or None
                if selected_phase_keys and not targets:
                    warn("No build targets found for selected phases; skipping build")
                    continue
                if not build_images(repo_root, use_cache=True, targets=targets):
                    error("Docker build failed")
                    if not args.ignore_errors:
                        return 1
            
            elif action == 'build_no_cache':
                targets = selected_bake_targets or None
                if selected_phase_keys and not targets:
                    warn("No build targets found for selected phases; skipping build")
                    continue
                if not build_images(repo_root, use_cache=False, targets=targets):
                    error("Docker build failed")
                    if not args.ignore_errors:
                        return 1
            
            elif action == 'deploy':
                # Show deployment configuration from phases (accurate)
                # Load deployment phases from configuration
                deployment_phases = selected_deployment_phases
                enabled_service_slugs = collect_enabled_service_slugs(deployment_phases, global_config)
                
                # Filter phases if --phases specified
                if selected_phases:
                    info(f"Deploying {len(deployment_phases)} selected phases")
                
                if not deployment_phases:
                    warn("No phases to deploy")
                    continue

                ensure_global_config_rendered(repo_root, python_exe, deployment_phases)
                
                # Step 0: Ensure Docker network
                ensure_network(network_name)
                
                # Step 1: CRITICAL - Assert devcontainer is connected to network
                assert_devcontainer_connected_to_network(network_name, auto_connect=auto_connect_setting)
                
                if enabled_service_slugs:
                    info("Deployment configuration:")

                    def any_enabled(*names: str) -> bool:
                        return any(normalize_service_slug(name) in enabled_service_slugs for name in names)

                    info(f"  Skywalking: {'ENABLED' if any_enabled('skywalking-oap', 'skywalking-ui') else 'DISABLED'}")
                    info(f"  Consul: {'ENABLED' if any_enabled('consul') else 'DISABLED'}")
                    info(f"  pgAdmin: {'ENABLED' if any_enabled('pgadmin') else 'DISABLED (not in default phases)'}")
                    info(f"  cAdvisor: {'ENABLED' if any_enabled('cadvisor') else 'DISABLED'}")
                    info(f"  OTel Aggregator: {'ENABLED' if any_enabled('otel-aggregator') else 'DISABLED'}")
                    info(f"  Reverse Proxy: {'ENABLED' if any_enabled('reverse-proxy') else 'DISABLED'}")
                
                # Execute deployment phases
                for i, phase in enumerate(deployment_phases, 1):
                    info(f"\n{BLUE}>>> Phase {i}/{len(deployment_phases)}: {phase.get('name')}{RESET}")
                    try:
                        # Pass args for phase-specific handling (like vault-only, services-only)
                        # Note: These flags are deprecated but kept for backward compatibility
                        class LegacyArgs:
                            vault_only = False
                            services_only = False
                            clean = False
                        legacy_args = LegacyArgs()
                        
                        execute_deployment_phase(
                            phase, repo_root, python_exe,
                            legacy_args, global_config
                        )
                    except Exception as e:
                        error(f"Phase failed: {e}")
                        if not args.ignore_errors:
                            return 1
            
            elif action == 'healthcheck':
                if not run_health_checks(
                    global_config,
                    scope=args.healthcheck,
                    include_selftests=False
                ):
                    error("Health checks failed")
                    if not args.ignore_errors:
                        return 1
            
            elif action == 'selftest':
                if not run_health_checks(
                    global_config,
                    log_file='deployment-selftest.log',
                    scope=args.selftest,
                    include_selftests=True
                ):
                    error("Self-tests failed")
                    if not args.ignore_errors:
                        return 1
            
            else:
                warn(f"Unknown action: {action}")
        
        except Exception as e:
            error(f"Action '{action}' failed: {e}")
            if not args.ignore_errors:
                return 1
    
    # Deployment complete
    summary = get_deployment_context().get_summary()
    info("\n" + "="*70)
    success("ALL ACTIONS COMPLETE")
    info("="*70)
    info(f"Deployment ID: {summary['deployment_id']}")
    info(f"Duration: {summary['duration_seconds']}s")
    info(f"Services started: {summary['services_started']}")
    if summary['services_failed'] > 0:
        warn(f"Services failed: {summary['services_failed']}")
    info("="*70)
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[INTERRUPTED]{RESET} Deployment interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"{RED}[FATAL]{RESET} Unexpected error: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
