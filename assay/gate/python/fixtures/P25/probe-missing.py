"""P25 qualification probe with one deliberately unexecuted branch."""


def classify(flag: bool) -> str:
    if flag:
        return "covered-true"
    return "missing-false"


def excluded_probe() -> str:
    return "excluded"  # pragma: no cover

# P25 comment-only delta.
