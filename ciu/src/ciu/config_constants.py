#!/usr/bin/env python3
"""
File- and directory-name constants for CIU.

CRITICAL: This is the SINGLE SOURCE OF TRUTH for every CIU file and directory
name. All modules MUST import from this file instead of hardcoding strings, so
a rename is a one-line change here.

Naming convention (greenfield standard):
- ``*.defaults.toml.j2`` = committed template defaults
- ``*.toml.j2``          = gitignored override template (auto-created from defaults)
- ``*.toml``             = gitignored rendered runtime config
- ``ciu.compose.yml.j2`` = committed compose template
- ``ciu.compose.yml``    = gitignored rendered compose (what CIU runs)
- ``docker-compose.yml`` = OPTIONAL hand-written compose a maintainer MAY ship;
                           CIU never writes it. The ``--shipped`` path runs it.
- ``ciu.env``            = gitignored workspace machine-identity env
- ``.ciu/``              = machine-owned artifact dir (overlay, secrets, rendered,
                           lock); humans MUST NOT edit its contents (S1.6).

Processing order reads straight off the suffix chain: a ``.j2`` suffix marks a
template (input); stripping it yields the rendered output.

Why constants here, and not in ``ciu.global.toml``?
    These names are needed to BOOTSTRAP, so they cannot live in runtime config:
    you cannot read ``ciu.global.toml`` to discover the name of
    ``ciu.global.toml``, and ``ciu env generate`` must write ``ciu.env`` before
    any config has been rendered. This module is therefore the single source of
    truth. "No hardcoding" is satisfied by every module IMPORTING from here
    instead of repeating a string literal — not by moving bootstrap names into
    runtime config (which is impossible). A rename stays a one-line change here.
"""

# ============================================================================
# TOML configuration filenames
# ============================================================================

# Global configuration (repository root)
GLOBAL_CONFIG_DEFAULTS = 'ciu.global.defaults.toml.j2'
GLOBAL_CONFIG_OVERRIDES = 'ciu.global.toml.j2'
GLOBAL_CONFIG_RENDERED = 'ciu.global.toml'

# Stack configuration (per stack directory: applications/*, infra/*, tools/*)
STACK_CONFIG_DEFAULTS = 'ciu.defaults.toml.j2'
STACK_CONFIG_OVERRIDES = 'ciu.toml.j2'
STACK_CONFIG_RENDERED = 'ciu.toml'

# ============================================================================
# Compose filenames
# ============================================================================

# CIU's own compose: committed template -> gitignored rendered output.
CIU_COMPOSE_TEMPLATE = 'ciu.compose.yml.j2'
CIU_COMPOSE_OUTPUT = 'ciu.compose.yml'

# A maintainer-authored, committed compose for the plain `docker compose` /
# `--shipped` path. CIU runs it but NEVER renders or overwrites it.
SHIPPED_COMPOSE = 'docker-compose.yml'

# ============================================================================
# Workspace environment (machine-identity layer, S2)
# ============================================================================

WORKSPACE_ENV = 'ciu.env'

# ============================================================================
# Machine-owned artifact directory (.ciu/, S1.6) and its contents
# ============================================================================

MACHINE_DIR = '.ciu'                       # per-stack and project-scoped
OVERLAY_NAME = 'ciu.compose.overlay.yml'   # generated overlay, under MACHINE_DIR
SECRETS_SUBDIR = 'secrets'                 # secret store, under MACHINE_DIR
RENDERED_SUBDIR = 'rendered'               # rendered configfiles, under MACHINE_DIR
LOCK_NAME = 'lock'                         # exclusive run lock, under MACHINE_DIR


# ============================================================================
# Helper functions
# ============================================================================


def get_rendered_config_name(defaults_name: str) -> str:
    """Map a ``*.defaults.toml.j2`` template name to its rendered ``*.toml``.

    Examples:
        >>> get_rendered_config_name('ciu.defaults.toml.j2')
        'ciu.toml'
        >>> get_rendered_config_name('ciu.global.defaults.toml.j2')
        'ciu.global.toml'
    """
    return defaults_name.replace('.defaults.toml.j2', '.toml')


def get_defaults_template_name(rendered_name: str) -> str:
    """Map a rendered ``*.toml`` name back to its ``*.defaults.toml.j2`` template.

    Examples:
        >>> get_defaults_template_name('ciu.toml')
        'ciu.defaults.toml.j2'
        >>> get_defaults_template_name('ciu.global.toml')
        'ciu.global.defaults.toml.j2'
    """
    return rendered_name.replace('.toml', '.defaults.toml.j2')


def is_config_file(filename: str) -> bool:
    """True if *filename* is a recognized CIU configuration file name."""
    config_files = {
        GLOBAL_CONFIG_DEFAULTS,
        GLOBAL_CONFIG_OVERRIDES,
        GLOBAL_CONFIG_RENDERED,
        STACK_CONFIG_DEFAULTS,
        STACK_CONFIG_OVERRIDES,
        STACK_CONFIG_RENDERED,
    }
    return filename in config_files
