from topos import __version__
from topos._assay_probe import classify


def test_real_topos_and_only_the_true_branch_are_imported() -> None:
    assert __version__
    assert classify(True) == "covered-true"
