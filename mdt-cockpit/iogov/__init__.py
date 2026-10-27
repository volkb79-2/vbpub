"""iogov: a RECOMMEND-ONLY recorder of long-term shifts in virtual-disk capability (v1).

It measures with tiny bounded probes, journals what it saw, and recognises slow regime changes.
It never changes a cap, a model, a queue setting or any cgroup file. See docs/IO-GOVERNOR-V1-DESIGN.md.
"""

__version__ = "1.0.0"
SCHEMA_VERSION = 1
