from exits import falls_off_the_end, guarded


def test_guarded():
    assert guarded(True) == "taken"


def test_falls_off_the_end():
    assert falls_off_the_end(True) is None
