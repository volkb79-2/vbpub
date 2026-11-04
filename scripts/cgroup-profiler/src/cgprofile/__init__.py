"""cgroup-profiler internals.

Deliberately empty of re-exports. The package is split into a collector tier
(standard library only, runs inside a privileged helper container) and an
analysis tier (pandas/plotly/ruptures, runs outside) — see ``DESIGN.md`` §1.
A convenience re-export here would let a collector module pull in pandas by
accident and quietly break that split, so every import names its module.
"""
