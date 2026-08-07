from pkg.greet import greet


def test_greet_returns_a_friendly_message():
    assert greet("world") == "Hello, world!"
