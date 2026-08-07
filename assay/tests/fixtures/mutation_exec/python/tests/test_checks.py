from pkg.checks import is_adult, is_valid_status


def test_is_adult_at_the_boundary():
    """A REAL, meaningful assertion at the exact boundary the mutant
    targets -- this is what genuinely KILLS the compare-swap mutant on
    ``age >= 18``."""
    assert is_adult(18) is True
    assert is_adult(17) is False


def test_is_valid_status_runs_without_checking_which_result_came_back():
    """Deliberately HOLLOW: the function is exercised, but the assertion
    never distinguishes True from False -- this is what lets the
    compare-swap mutant on ``code == 200`` genuinely SURVIVE."""
    result = is_valid_status(200)
    assert result is not None
