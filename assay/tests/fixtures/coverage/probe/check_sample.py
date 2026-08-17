from sample import classify, first_two


def test_classify_positive():
    assert classify(1) == "pos"


def test_first_two():
    assert first_two(5) == 2
