"""agctl — agent control: controller state and the estate view (DESIGN section 2).

Read-only by contract. Every reader in this package opens its sources for reading only, takes
no lock and reaches no host or network; `git` with ``--no-optional-locks`` is the only
subprocess. The installed distribution's version is the only version this package ever
reports — it never invents one (see :func:`agctl.cli.identity`).
"""

__all__: list[str] = []
