from __future__ import annotations

from cowtree.errors import (
    BackendFailed,
    CowError,
    LeaseHeld,
    PathRefused,
    RootInvalid,
    SpaceRefused,
)


def test_error_codes_and_optional_backend_context():
    assert CowError.code == "cow"
    assert RootInvalid.code == "root-invalid"
    assert SpaceRefused.code == "space"
    assert PathRefused.code == "path"
    assert BackendFailed.code == "backend"
    assert LeaseHeld.code == "lease"

    bare = BackendFailed("stat", None)
    assert str(bare) == "backend operation 'stat' failed"
    detailed = BackendFailed("clone", 18, "/input/tree", "unsupported")
    assert str(detailed) == "backend operation 'clone' failed (errno=18) at '/input/tree': unsupported"
