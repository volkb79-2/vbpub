import os

import pytest

from tnt import profile as prof
from tnt.errors import ProfileError


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
    ('[profiles.a]\nurl="ws://h/api/current"\nusername="u"\n', "ws:// is refused"),
    ('[profiles.a]\nurl="wss://h/api/current"\nusername="u"\napi_key="1-secret"\n', "never belongs in the profile file"),
    ('[profiles.a]\nurl="wss://h/x"\n', "username is required"),
    ('[profiles.a]\nurl="wss://h/x"\nusername="u"\ntls="off"\n', "tls must be"),
    ('[profiles.a]\nurl="wss://h/x"\nusername="u"\nauth="md5"\n', "auth must be"),
    ('[profiles.a]\nurl="wss://h/x"\nusername="u"\napi_key_file="rel/key"\n', "absolute"),
    ('[profiles."bad name"]\nurl="wss://h/x"\nusername="u"\n', "profile name"),
    ('[profiles]\n', r"no \[profiles"),
    ('default="zz"\n[profiles.a]\nurl="wss://h/x"\nusername="u"\n', "not a defined profile"),
    ('this is not toml', "cannot read"),
    ('[profiles]\na = 3\n', "url is required"),
])
def test_invalid_profile_files(tmp_path, body, match):
    path = tmp_path / "p.toml"
    path.write_text(body)
    with pytest.raises(ProfileError, match=match):
        prof.load_profiles(path)


def test_missing_file(tmp_path):
    with pytest.raises(ProfileError, match="no profile file"):
        prof.load_profiles(tmp_path / "absent.toml")


def test_default_config_path_order(tmp_path):
    assert prof.default_config_path({"TNT_CONFIG": "/x/y.toml"}).as_posix() == "/x/y.toml"
    assert prof.default_config_path({"XDG_CONFIG_HOME": "/xdg"}).as_posix() == "/xdg/tnt/profiles.toml"
    assert prof.default_config_path({}).as_posix().endswith(".config/tnt/profiles.toml")
    assert prof.default_config_path().name == "profiles.toml"


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
    assert set(profiles["lab"].describe()) == {"name", "url", "username", "api_key_file", "tls", "auth"}
