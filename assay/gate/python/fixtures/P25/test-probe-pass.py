from topos import __version__
from topos._assay_probe import classify


def test_real_topos_and_probe_are_imported() -> None:
    assert __version__
    assert classify(True) == "covered-true"
    assert classify(False) == "covered-false"
