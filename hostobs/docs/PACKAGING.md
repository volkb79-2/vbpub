# Python client packaging

`python/pyproject.toml` defines the `hostobs-client` distribution. It installs the
`hostobs_client` package and the `hostobs-mark` command, whose target is
`hostobs_client.mark:main`. The client has no runtime dependencies; it uses only
the Python standard library and remains importable from a zip archive.

The `conformance` lane runs `python/tests/test_packaging.py` as part of its
standard-library unittest discovery. That test builds a zipapp with Python's
stdlib `zipapp` module in the lane, using the package roots and include patterns
from `python/pyproject.toml`. It compares the archive against an independent
recursive inventory of the package source, checks that non-Python files are
declared as package data, and verifies the isolated `hostobs-mark` command's
exit behavior. The test also pins the build backend, build requirements,
`where`, `include`, and Python floor from `python/pyproject.toml`. The lane
image `tester-unified-rust` has no pip or ensurepip (HOB-44), so the lane does
not build or install a wheel.

The conformance zipapp is lane evidence; it is not a release wheel. No release
process builds this wheel yet: `hostobs/cmru.toml` is not present yet. A CMRU
wheel-build contract is planned for package A1. Only a future A1 wheel build can
prove the contents and metadata of the actual wheel artifact.

`pyproject.toml` declares the version as dynamic and configures
`setuptools-scm` to recognize matching `hostobs-v*` Git tags. No current release
process invokes this configuration to build a wheel, and there is no literal or
fallback version. The future A1 contract still needs to define the wheel build
and version handoff. No release exists yet.
