import copy
import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parents[1] / "golden"


def golden(relative: str):
    return json.loads((GOLDEN / relative).read_text(encoding="utf-8"))


def edit(document, *steps):
    """Deep-copy ``document`` and apply ``(path, value)`` steps; a path is a tuple of keys/indexes.

    A value of ``DELETE`` removes the key.
    """
    out = copy.deepcopy(document)
    for path, value in steps:
        node = out
        for key in path[:-1]:
            node = node[key]
        if value is DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
    return out


DELETE = object()


def messages(violations):
    return [str(v) for v in violations]


def has(violations, path, fragment):
    """True when some violation is at exactly ``path`` and its message contains ``fragment``."""
    return any(v.path == path and fragment in v.message for v in violations)


@pytest.fixture
def pf():
    return lambda name: golden(f"fragments/{name}.json")


@pytest.fixture
def rec():
    return lambda name: golden(f"records/{name}.json")
