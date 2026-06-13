"""
CIU v2 deploy_pkg — orchestration helper package.

Public API re-exported from sub-modules:

  http_util:  http_get_json
  phases:     PHASE_KEY_RE, ordered_phases, service_enabled,
              iter_enabled_services, parse_env_overrides
  profiles:   Profile, resolve_profile, reject_groups
  health:     classify, evaluate_gate, wait_for_gate, anchored_name_filter
  registry:   check_registry_auth
"""
from __future__ import annotations

from .health import (
    anchored_name_filter,
    classify,
    evaluate_gate,
    wait_for_gate,
)
from .http_util import http_get_json
from .phases import (
    PHASE_KEY_RE,
    iter_enabled_services,
    ordered_phases,
    parse_env_overrides,
    service_enabled,
)
from .profiles import (
    Profile,
    reject_groups,
    resolve_profile,
)
from .registry import check_registry_auth

__all__ = [
    # http_util
    "http_get_json",
    # phases
    "PHASE_KEY_RE",
    "ordered_phases",
    "service_enabled",
    "iter_enabled_services",
    "parse_env_overrides",
    # profiles
    "Profile",
    "resolve_profile",
    "reject_groups",
    # health
    "classify",
    "evaluate_gate",
    "wait_for_gate",
    "anchored_name_filter",
    # registry
    "check_registry_auth",
]
