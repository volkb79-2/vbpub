"""A branch whose untaken arc leaves the function rather than reaching a line."""


def guarded(x):
    if x:
        return "taken"
    return "fallthrough"


def falls_off_the_end(x):
    if x:
        pass
