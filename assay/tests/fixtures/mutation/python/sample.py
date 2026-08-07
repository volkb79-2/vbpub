"""Fixture module for P11's mutation-engine oracle tests (O1-O3).

Read directly by tests/test_adapters_python_generate_mutants.py as literal
committed source -- never generated, and never executed: this file is not
`test_*.py`-named, so pytest never collects or imports it, which is why the
several unreachable `return` statements below (dead code after an earlier
`return` in the same function) are harmless -- nothing here ever runs.

Exercises every catalogue member (`compare-swap`/`boolop-swap`/
`bool-const-flip`/`falsy-swap`), the boolean-chain trap (A-115, two chains
of different lengths), a real non-ASCII byte elsewhere in the file (the
byte-exact-splice proof), and every construct this package's own catalogue
deliberately does NOT cover.
"""

# A non-ASCII comment, deliberately placed early: café -- proves a splice
# anywhere below leaves this exact byte sequence (a 2-byte UTF-8 character)
# completely untouched, and that this project's byte-offset arithmetic does
# not silently misalign once a multi-byte character has shifted every
# subsequent ast.col_offset away from its own character count.


def compare_ops(a, b):
    if a < b:
        return a
    if a <= b:
        return a
    if a > b:
        return a
    if a >= b:
        return a
    if a == b:
        return a
    if a != b:
        return a
    if a is b:
        return a
    if a is not b:
        return a
    if a in b:
        return a
    if a not in b:
        return a
    return None


def bool_chain_and(a, b, c):
    if a and b and c:
        return True
    return False


def bool_chain_or(a, b, c, d):
    if a or b or c or d:
        return True
    return False


def bool_consts():
    x = True
    y = False
    return x, y


def falsy_none():
    return None


def falsy_zero():
    return 0


def falsy_empty_str():
    return ""


def falsy_empty_bytes():
    return b""


def falsy_empty_list():
    return []


def falsy_empty_tuple():
    return ()


def falsy_empty_dict():
    return {}


def non_falsy_return():
    return 42


def bare_return():
    return
