"""The package's own identity: one version source, and a CLI that can be built.

The literal `__version__ = "0.1.0"` was removed with the cli-extended migration: the
version comes from installed distribution metadata (resolved by ``CliIdentity.resolve``),
and the test run publishes a stand-in distribution whose version is ``WORKING_VERSION``.
This check pins both halves — the value a caller reads, and the absence of a stored
literal that could outlive the tag it was copied from.
"""

from fleetd import __version__

from conftest import WORKING_VERSION


def test_package_version_is_the_test_run_distribution_version():
    assert __version__ == WORKING_VERSION


def test_the_package_stores_no_version_literal():
    import fleetd

    assert "__version__" not in vars(fleetd)
