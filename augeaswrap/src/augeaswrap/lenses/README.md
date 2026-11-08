# Lens inventory

This package-data directory is reserved for Augeas overlay lenses. It is empty
in the endpoint-record milestone; no lens is shipped yet. `tools/lens-check.sh`
is a no-op while no `.aug` lens exists. When a lens is added, it must have a
matching `tests/test_<lens>.aug` file and `augparse` must be available to the
registered R0/R1 lane.

All new lenses must carry their source and licence notes here. The current
project licence is MIT; any future upstream-derived lens requires a reviewed
licence change before it is added.
