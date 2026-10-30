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
        credential = kw.pop("secret", server.secret())
        return open_session(server.profile(profile.name, auth=profile.auth), secret=credential,
                            allow_loopback_ws=True, **kw)
    return _open


PROFILE_TOML = """schema_version = 1
default_host = "lab"

[hosts.lab]
url = "wss://lab.example.invalid/api/current"
username = "tnt"
auth = "api_key"
tls = "verify"

[hosts.throwaway]
url = "wss://vm.example.invalid/api/current"
username = "tnt"
tls = "insecure"
auth = "api_key"
"""

SECRETS_TOML = """schema_version = 1

[hosts.lab]
host_url = "wss://lab.example.invalid/api/current"
api_key = "1-fakefakefakefakefakefakefakefake"

[hosts.throwaway]
host_url = "wss://vm.example.invalid/api/current"
api_key = "1-throwaway-placeholder"
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "tnt.toml"
    path.write_text(PROFILE_TOML)
    path.chmod(0o600)
    secrets = tmp_path / "tnt.secrets.toml"
    secrets.write_text(SECRETS_TOML)
    secrets.chmod(0o600)
    # Isolate CLI tests from any operator secrets in the process environment or XDG home.
    monkeypatch.setenv("TNT_SECRETS", str(secrets))
    return path
