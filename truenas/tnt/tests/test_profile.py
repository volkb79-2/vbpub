import os

import pytest

from tnt import profile as prof
from tnt.errors import ProfileError


def write_config(path, body):
    path.write_text(body)
    path.chmod(0o600)
    return path


def test_load_and_pick_default(config):
    profiles, default = prof.load_profiles(config)
    assert default == "lab" and set(profiles) == {"lab", "throwaway"}
    assert prof.pick(profiles, default, None).name == "lab"
    assert prof.pick(profiles, default, "throwaway").insecure
    assert profiles["lab"].host == "lab.example.invalid"


def test_pick_errors(config):
    profiles, _ = prof.load_profiles(config)
    with pytest.raises(ProfileError, match="no default"):
        prof.pick(profiles, None, None)
    with pytest.raises(ProfileError, match="unknown profile"):
        prof.pick(profiles, None, "nope")
    one = {"lab": profiles["lab"]}
    assert prof.pick(one, None, None).name == "lab"


@pytest.mark.parametrize("body,match", [
    ('schema_version=1\n[profiles.a]\nurl="ws://h/api/current"\nusername="u"\n', "ws:// is refused"),
    ('schema_version=1\n[profiles.a]\nurl="wss://h/x"\n', "username is required"),
    ('schema_version=1\n[profiles.a]\nurl="wss://h/x"\nusername="u"\ntls="off"\n', "tls must be"),
    ('schema_version=1\n[profiles.a]\nurl="wss://h/x"\nusername="u"\nauth="md5"\n', "auth must be"),
    ('schema_version=1\n[profiles.a]\nurl="wss://h/x"\nusername="u"\napi_key_file="rel/key"\n', "absolute"),
    ('schema_version=1\n[profiles."bad name"]\nurl="wss://h/x"\nusername="u"\n', "profile name"),
    ('schema_version=1\n[profiles]\n', r"no \[profiles"),
    ('schema_version=1\ndefault="zz"\n[profiles.a]\nurl="wss://h/x"\nusername="u"\n', "not a defined profile"),
    ('schema_version=1\nthis is not toml', "cannot read"),
    ('schema_version=1\n[profiles]\na = 3\n', "url is required"),
])
def test_invalid_profile_files(tmp_path, body, match):
    path = tmp_path / "p.toml"
    write_config(path, body)
    with pytest.raises(ProfileError, match=match):
        prof.load_profiles(path)


def test_missing_file(tmp_path):
    with pytest.raises(ProfileError, match="no profile file"):
        prof.load_profiles(tmp_path / "absent.toml")


@pytest.mark.parametrize("body", [
    '[profiles.a]\nurl="wss://h/api/current"\nusername="u"\n',
    'schema_version = 2\n[profiles.a]\nurl="wss://h/api/current"\nusername="u"\n',
    'schema_version = true\n[profiles.a]\nurl="wss://h/api/current"\nusername="u"\n',
])
def test_schema_version_is_required_and_exact(tmp_path, body):
    path = write_config(tmp_path / "tnt.toml", body)
    with pytest.raises(ProfileError, match="schema_version must be 1"):
        prof.load_profiles(path)


def test_default_config_path_order(tmp_path):
    assert prof.default_config_path({"TNT_CONFIG": "/x/y.toml"}).as_posix() == "/x/y.toml"
    xdg = prof.default_config_path({"XDG_CONFIG_HOME": "/xdg"}, cwd=tmp_path)
    assert xdg.as_posix() == "/xdg/tnt/tnt.toml"
    assert prof.default_config_path({}, cwd=tmp_path).as_posix().endswith(".config/tnt/tnt.toml")
    local = tmp_path / "tnt.toml"
    write_config(local, "")
    assert prof.default_config_path({"XDG_CONFIG_HOME": "/xdg"}, cwd=tmp_path) == local
    assert prof.default_config_path().name == "tnt.toml"


@pytest.mark.parametrize("mode,owner_ok,accepted", [
    (0o600, True, True), (0o640, True, False), (0o400, True, False), (0o600, False, False),
])
def test_config_file_must_be_owned_by_current_user_and_exactly_mode_0600(tmp_path, monkeypatch, mode, owner_ok, accepted):
    path = tmp_path / "tnt.toml"
    path.write_text('schema_version=1\n[profiles.a]\nurl="wss://h/api/current"\nusername="u"\nauth="password"\npassword="not-a-secret"\n')
    path.chmod(mode)
    uid = path.stat().st_uid
    if not owner_ok:
        monkeypatch.setattr(os, "geteuid", lambda: uid + 1)
    if accepted:
        assert prof.load_profiles(path)[0]["a"].password.reveal() == "not-a-secret"
    else:
        with pytest.raises(ProfileError, match="mode 0600|owned by uid"):
            prof.load_profiles(path)


def test_inline_api_key_and_password_are_supported_and_redacted(tmp_path):
    path = tmp_path / "tnt.toml"
    write_config(path, '''schema_version = 1
[profiles.key]
url = "wss://nas.example.invalid/api/current"
username = "admin"
auth = "api_key"
api_key = "1-fake-api-key-material"
ssh_user = "admin"
ssh_key_files = ["~/.ssh/id_ed25519"]
ssh_known_hosts = "~/.ssh/known_hosts"

[profiles.password]
url = "wss://other.example.invalid/api/current"
username = "operator"
auth = "password"
password = "fake-password-material"
''')
    profiles, _ = prof.load_profiles(path)
    assert profiles["key"].api_key.reveal() == "1-fake-api-key-material"
    assert profiles["password"].password.reveal() == "fake-password-material"
    assert profiles["key"].describe()["api_key"] == "***"
    assert profiles["password"].describe()["password"] == "***"
    assert "fake-api-key-material" not in repr(profiles["key"])
    assert "fake-password-material" not in repr(profiles["password"])
    assert "fake-api-key-material" not in str(profiles["key"].describe())
    assert "fake-password-material" not in str(profiles["password"].describe())


def test_auth_modes_reject_mixed_or_missing_credentials(tmp_path):
    path = tmp_path / "tnt.toml"
    write_config(path, 'schema_version=1\n[profiles.a]\nurl="wss://h/api/current"\nusername="u"\nauth="password"\n')
    with pytest.raises(ProfileError, match="password is required"):
        prof.load_profiles(path)
    write_config(path, 'schema_version=1\n[profiles.a]\nurl="wss://h/api/current"\nusername="u"\nauth="api_key"\npassword="x"\n')
    with pytest.raises(ProfileError, match="does not accept password"):
        prof.load_profiles(path)


def test_secret_never_shows_and_scrubs():
    s = prof.Secret("1-topsecret")
    assert "topsecret" not in repr(s) and "topsecret" not in str(s) and "topsecret" not in f"{s!r}"
    assert s.scrub("login 1-topsecret failed") == "login <redacted> failed"
    assert prof.Secret("").scrub("abc") == "abc"


def test_read_secret_from_key_file(config):
    profiles, _ = prof.load_profiles(config)
    assert prof.read_secret(profiles["lab"], {}).reveal() == "1-notarealkey"


def test_key_file_must_be_private(config, tmp_path):
    profiles, _ = prof.load_profiles(config)
    os.chmod(profiles["lab"].api_key_file, 0o640)
    with pytest.raises(ProfileError, match="accessible to group/other"):
        prof.read_secret(profiles["lab"], {})


def test_key_file_problems(tmp_path):
    assert "cannot stat" in prof.key_file_problem(str(tmp_path / "nope"))
    assert "not a regular file" in prof.key_file_problem(str(tmp_path))


def test_read_secret_env_sources(config, tmp_path):
    profiles, _ = prof.load_profiles(config)
    bare = profiles["throwaway"]
    assert prof.read_secret(bare, {"TNT_API_KEY": " 1-fromenv "}).reveal() == "1-fromenv"
    f = tmp_path / "k"
    f.write_text("1-fromfile")
    f.chmod(0o600)
    assert prof.read_secret(bare, {"TNT_API_KEY_FILE": str(f)}).reveal() == "1-fromfile"
    with pytest.raises(ProfileError, match="no API key"):
        prof.read_secret(bare, {})
    f.write_text("  \n")
    with pytest.raises(ProfileError, match="empty"):
        prof.read_secret(bare, {"TNT_API_KEY_FILE": str(f)})
    assert prof.read_secret(bare, {"TNT_API_KEY": "1-x"}).reveal() == "1-x"


def test_read_secret_uses_process_environment(config, monkeypatch):
    profiles, _ = prof.load_profiles(config)
    monkeypatch.setenv("TNT_API_KEY", "1-proc")
    assert prof.read_secret(profiles["throwaway"]).reveal() == "1-proc"


def test_describe_has_no_secret_fields(config):
    profiles, _ = prof.load_profiles(config)
    described = profiles["lab"].describe()
    assert set(described) == {"name", "url", "username", "api_key_file", "api_key", "password", "tls", "auth",
                              "ssh_user", "ssh_key_files", "ssh_known_hosts"}
    assert described["api_key"] == "***" and described["password"] is None


@pytest.mark.parametrize("body,match", [
    ('schema_version=1\n[profiles.a]\nurl="wss://host:bad/api/current"\nusername="u"\n', "valid URL"),
    ('schema_version=1\n[profiles.a]\nurl="wss://user:pass@host/api/current"\nusername="u"\n', "must not carry credentials"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current?token=x"\nusername="u"\n', "must not carry credentials"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current#part"\nusername="u"\n', "must not carry credentials"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\napi_key=""\n', "non-empty string"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\napi_key="/tmp/key"\n', "key material"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\napi_key="x"\napi_key_file="/tmp/key"\n', "either api_key"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\nauth="password"\npassword="p"\napi_key_file="/tmp/key"\n', "does not accept api_key"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\nssh_user=""\n', "ssh_user must"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\nssh_key_files="bad"\n', "ssh_key_files must"),
    ('schema_version=1\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\nssh_known_hosts=""\n', "ssh_known_hosts must"),
    ('schema_version=1\nwrong="top"\n[profiles.a]\nurl="wss://host/api/current"\nusername="u"\n', "unknown top-level"),
])
def test_remaining_profile_schema_refusals(tmp_path, body, match):
    path = write_config(tmp_path / "tnt.toml", body)
    with pytest.raises(ProfileError, match=match):
        prof.load_profiles(path)


def test_profile_file_must_be_regular_and_credential_reads_are_auth_specific(tmp_path):
    with pytest.raises(ProfileError, match="not a regular config file"):
        prof.load_profiles(tmp_path)
    password_profile = prof.Profile("p", "wss://host/api/current", "u", None, "verify", "password",
                                   password=prof.Secret("fake-password"))
    with pytest.raises(ProfileError, match="API-key credentials requested"):
        prof.read_secret(password_profile, {})
    api_profile = prof.Profile("a", "wss://host/api/current", "u", None, "verify", "api_key")
    with pytest.raises(ProfileError, match="password credentials are not configured"):
        prof.read_password(api_profile)


def test_inline_api_key_read_and_empty_or_pathlike_environment_values(config):
    profiles, _ = prof.load_profiles(config)
    assert prof.read_secret(profiles["lab"], {}).reveal() == "1-notarealkey"
    bare = profiles["throwaway"]
    with pytest.raises(ProfileError, match="the API key is empty"):
        prof.read_secret(bare, {"TNT_API_KEY": "  "})
    with pytest.raises(ProfileError, match="starts with"):
        prof.read_secret(bare, {"TNT_API_KEY": "/tmp/key"})
