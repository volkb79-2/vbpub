#!/usr/bin/env python3
"""
Configuration filename constants for DST-DNS project.

CRITICAL: This is the SINGLE SOURCE OF TRUTH for all config filenames.
All scripts MUST import from this file instead of using hardcoded strings.

Naming Convention (Greenfield Standard):
- *.defaults.toml.j2 = Template defaults (committed)
- *.toml.j2 = Template overrides (gitignored)
- *.toml = Rendered runtime config (gitignored)
"""

# ============================================================================
# TOML Configuration Filenames (CANONICAL - DO NOT HARDCODE)
# ============================================================================

# Global configuration (repository root)
GLOBAL_CONFIG_DEFAULTS = 'ciu-global.defaults.toml.j2'
GLOBAL_CONFIG_OVERRIDES = 'ciu-global.toml.j2'
GLOBAL_CONFIG_RENDERED = 'ciu-global.toml'

# Stack configuration (per-service directory: applications/*, infra/*, infra-global/*, tools/*)
STACK_CONFIG_DEFAULTS = 'ciu.defaults.toml.j2'
STACK_CONFIG_OVERRIDES = 'ciu.toml.j2'
STACK_CONFIG_RENDERED = 'ciu.toml'

# Docker Compose generated files
DOCKER_COMPOSE_TEMPLATE = 'docker-compose.yml.j2'
DOCKER_COMPOSE_OUTPUT = 'docker-compose.yml'

# ============================================================================
# Helper Functions
# ============================================================================


def get_rendered_config_name(defaults_name: str) -> str:
    """
    Get the corresponding rendered filename for a defaults template.

    Args:
        defaults_name: The .defaults.toml.j2 filename

    Returns:
        The corresponding .toml filename

    Examples:
        >>> get_rendered_config_name('ciu.defaults.toml.j2')
        'ciu.toml'
        >>> get_rendered_config_name('ciu-global.defaults.toml.j2')
        'ciu-global.toml'
    """
    return defaults_name.replace('.defaults.toml.j2', '.toml')


def get_defaults_template_name(rendered_name: str) -> str:
    """
    Get the corresponding defaults template filename for a rendered TOML.

    Args:
        rendered_name: The rendered .toml filename

    Returns:
        The corresponding .defaults.toml.j2 filename

    Examples:
        >>> get_defaults_template_name('ciu.toml')
        'ciu.defaults.toml.j2'
        >>> get_defaults_template_name('ciu-global.toml')
        'ciu-global.defaults.toml.j2'
    """
    return rendered_name.replace('.toml', '.defaults.toml.j2')


def is_config_file(filename: str) -> bool:
    """
    Check if a filename is a recognized configuration file.

    Args:
        filename: The filename to check

    Returns:
        True if the filename matches a known config pattern
    """
    config_files = {
        GLOBAL_CONFIG_DEFAULTS,
        GLOBAL_CONFIG_OVERRIDES,
        GLOBAL_CONFIG_RENDERED,
        STACK_CONFIG_DEFAULTS,
        STACK_CONFIG_OVERRIDES,
        STACK_CONFIG_RENDERED,
    }
    return filename in config_files
