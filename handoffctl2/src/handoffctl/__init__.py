"""handoffctl — files-first control plane for role-separated AI development.

Frozen-core modules (written by the frontier session; implementation agents
MUST NOT modify them — file a BLOCKED report instead):
    types, paths, storage, config, leases
Package modules (one implementation handoff each, see ../../handoff/):
    frontmatter, lint, reconcile, adapters, wrapper, daemon, render,
    notify, decisions, doctor, cli
"""

__version__ = "0.1.0a0"
