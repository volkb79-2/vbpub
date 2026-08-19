"""A tiny module with two exercised branches and one wholly unexercised one."""


def classify(x):
    if x > 0:
        return "pos"
    return "nonpos"


def first_two(y):
    for i in range(y):
        if i == 2:
            return i
    return -1


def never_called(z):
    if z:
        return 1
    return 0
