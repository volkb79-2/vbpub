import pytest

from tnt.testing import FakeServer


@pytest.fixture
def server():
    with FakeServer() as s:
        yield s


@pytest.fixture
def opener(server):
    """A session opener for the CLI that talks to the fake server (loopback ws is honoured only here)."""
    from tnt.session import open_session

    def _open(profile, **kw):
        return open_session(server.profile(profile.name), secret=server.secret(), allow_loopback_ws=True, **kw)
    return _open


PROFILE_TOML = """default = "lab"

[profiles.lab]
url = "wss://lab.example.invalid/api/current"
username = "tnt"
api_key_file = "{key}"
tls = "verify"

[profiles.throwaway]
url = "wss://vm.example.invalid/api/current"
username = "tnt"
tls = "insecure"
auth = "plain"
"""


@pytest.fixture
def config(tmp_path):
    key = tmp_path / "lab.key"
    key.write_text("1-notarealkey\n")
    key.chmod(0o600)
    path = tmp_path / "profiles.toml"
    path.write_text(PROFILE_TOML.format(key=key))
    return path
