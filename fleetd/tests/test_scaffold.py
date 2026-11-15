from fleetd import __version__


def test_package_declares_its_working_version():
    assert __version__ == "0.1.0"
