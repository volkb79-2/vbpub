"""P25 qualification probe."""


def classify(flag: bool) -> str:
    if flag:
        return "covered-true"
    return "covered-false"


def excluded_probe() -> str:
    return "excluded"  # pragma: no cover

# P25 comment-only delta.
