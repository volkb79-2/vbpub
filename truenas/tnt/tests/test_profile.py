from __future__ import annotations

import os
from pathlib import Path

import pytest

from tnt import profile as prof
from tnt.errors import ProfileError


def write_private(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o600)
    return path


def config_body(default='"lab"'):
    default_line = f"default_host = {default}\n" if default else ""
    return f'''schema_version = 1
{default_line}
[hosts.lab]
url = "wss://lab.example.invalid/api/current"
tls = "verify"
auth = "api_key"
username = "operator"
description = "Primary lab"
tags = ["storage", "primary"]
ssh_user = "admin"
ssh_key_files = ["/home/operator/.ssh/id_ed25519"]
ssh_known_hosts = "/home/operator/.ssh/known_hosts"

[hosts.throwaway]
url = "wss://test.example.invalid/api/current"
tls = "insecure"
auth = "password"
username = "tester"
description = "Short-lived test"
'''


def secrets_body(key_path=None):
    key_field = f'api_key_file = "{key_path}"' if key_path else 'api_key = "1-fake-key-material"'
    return f'''schema_version = 1

[hosts.lab]
host_url = "wss://lab.example.invalid/api/current"
{key_field}

[hosts.throwaway]
host_url = "wss://test.example.invalid/api/current"
password = "fake-password-material"
'''


def test_load_hosts_and_pick_default(tmp_path):
    path = write_private(tmp_path / "tnt.toml", config_body())
    hosts, default = prof.load_profiles(path)
    assert default == "lab" and set(hosts) == {"lab", "throwaway"}
    assert prof.pick(hosts, default, None).name == "lab"
    assert prof.pick(hosts, default, "throwaway").insecure
    assert hosts["lab"].host == "lab.example.invalid"
    assert hosts["lab"].description == "Primary lab"
    assert hosts["lab"].tags == ("storage", "primary")


def test_pick_requires_explicit_host_when_multiple_have_no_default(tmp_path):
    path = write_private(tmp_path / "tnt.toml", config_body(default=None))
    hosts, default = prof.load_profiles(path)
    with pytest.raises(ProfileError, match=r"several hosts.*lab.*throwaway.*--host"):
        prof.pick(hosts, default, None)
    with pytest.raises(ProfileError, match="unknown host"):
        prof.pick(hosts, default, "missing")
    assert prof.pick({"lab": hosts["lab"]}, None, None).name == "lab"


@pytest.mark.parametrize("body,match", [
    ('schema_version=1\n[hosts.a]\nurl="ws://h/api/current"\nusername="u"\n', "ws:// is refused"),
    ('schema_version=1\n[hosts.a]\nurl="wss://h/x"\n', "username is required"),
    ('schema_version=1\n[hosts.a]\nurl="wss://h/x"\nusername="u"\ntls="off"\n', "tls must be"),
    ('schema_version=1\n[hosts.a]\nurl="wss://h/x"\nusername="u"\nauth="md5"\n', "auth must be"),
    ('schema_version=1\n[hosts."bad name"]\nurl="wss://h/x"\nusername="u"\n', "host name"),
    ('schema_version=1\nhosts = []\n', r"\[hosts.NAME\] must be a table"),
    ('schema_version=1\ndefault_host="zz"\n[hosts.a]\nurl="wss://h/x"\nusername="u"\n', "not a defined host"),
    ('schema_version=1\nthis is not toml', "cannot read"),
    ('schema_version=1\n[hosts]\na = 3\n', "url is required"),
    ('schema_version=1\n[hosts.a]\nurl="wss://h/x"\nusername="u"\napi_key="x"\n', "credentials belong in tnt.secrets.toml"),
    ('schema_version=1\n[hosts.a]\nurl="wss://h/x"\nusername="u"\napi_key_file="/tmp/key"\n', "credentials belong in tnt.secrets.toml"),
    ('schema_version=1\n[hosts.a]\nurl="wss://h/x"\nusername="u"\npassword="x"\n', "credentials belong in tnt.secrets.toml"),
])
def test_invalid_host_files(tmp_path, body, match):
    path = write_private(tmp_path / "tnt.toml", body)
    with pytest.raises(ProfileError, match=match):
        prof.load_profiles(path)


def test_missing_config_file(tmp_path):
    with pytest.raises(ProfileError, match="no config file"):
        prof.load_profiles(tmp_path / "absent.toml")


@pytest.mark.parametrize("body", [
    '[hosts.a]\nurl="wss://h/api/current"\nusername="u"\n',
    'schema_version = 2\n[hosts.a]\nurl="wss://h/api/current"\nusername="u"\n',
    'schema_version = true\n[hosts.a]\nurl="wss://h/api/current"\nusername="u"\n',
])
def test_schema_version_is_required_and_exact(tmp_path, body):
    path = write_private(tmp_path / "tnt.toml", body)
    with pytest.raises(ProfileError, match="schema_version must be 1"):
        prof.load_profiles(path)


def test_default_config_and_paired_secrets_resolution(tmp_path):
    assert prof.default_config_path({"TNT_CONFIG": "/x/tnt.toml"}, cwd=tmp_path) == Path("/x/tnt.toml")
    xdg = prof.default_config_path({"XDG_CONFIG_HOME": "/xdg"}, cwd=tmp_path)
    assert xdg == Path("/xdg/tnt/tnt.toml")
    local = write_private(tmp_path / "tnt.toml", config_body())
    assert prof.default_config_path({"XDG_CONFIG_HOME": "/xdg"}, cwd=tmp_path) == local
    paths = prof.resolve_paths(cwd=tmp_path, env={"XDG_CONFIG_HOME": "/xdg"})
    assert paths.config == local and paths.secrets == Path("/xdg/tnt/tnt.secrets.toml")
    local_secrets = write_private(tmp_path / "tnt.secrets.toml", secrets_body())
    paths = prof.resolve_paths(cwd=tmp_path, env={"XDG_CONFIG_HOME": "/xdg"})
    assert paths.config == local and paths.secrets == local_secrets
    xdg_config = write_private(tmp_path / "xdg" / "tnt" / "tnt.toml", config_body())
    paths = prof.resolve_paths(cwd=tmp_path / "empty", env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    assert paths.config == xdg_config and paths.secrets == xdg_config.parent / "tnt.secrets.toml"
    assert prof.resolve_paths("/explicit/tnt.toml", "/explicit/private.toml").secrets == Path("/explicit/private.toml")


def test_default_secrets_path_and_independent_explicit_path_precedence(tmp_path):
    assert prof.default_secrets_path({"TNT_SECRETS": "/env/private.toml"}, cwd=tmp_path) == Path("/env/private.toml")
    local = write_private(tmp_path / "tnt.secrets.toml", secrets_body())
    assert prof.default_secrets_path({"XDG_CONFIG_HOME": "/xdg"}, cwd=tmp_path) == local
    assert prof.default_secrets_path({"XDG_CONFIG_HOME": "/xdg"}, cwd=tmp_path / "empty") == Path(
        "/xdg/tnt/tnt.secrets.toml")
    paired = prof.resolve_paths("/explicit/tnt.toml", "/other/private.toml", env={
        "TNT_CONFIG": "/ignored/config.toml", "TNT_SECRETS": "/ignored/secrets.toml"})
    assert paired == prof.ConfigPaths(Path("/explicit/tnt.toml"), Path("/other/private.toml"))


def test_resolve_paths_uses_explicit_environment_and_local_paths_independently(tmp_path):
    explicit_config = write_private(tmp_path / "chosen" / "tnt.toml", config_body())
    explicit_secrets = tmp_path / "elsewhere" / "tnt.secrets.toml"
    paths = prof.resolve_paths(explicit_config, explicit_secrets, env={
        "TNT_CONFIG": "/ignored/config.toml", "TNT_SECRETS": "/ignored/secrets.toml"})
    assert paths == prof.ConfigPaths(explicit_config, explicit_secrets)

    env_config = write_private(tmp_path / "env" / "tnt.toml", config_body())
    env_secrets = write_private(tmp_path / "env-secrets" / "tnt.secrets.toml", secrets_body())
    paths = prof.resolve_paths(env={"TNT_CONFIG": str(env_config), "TNT_SECRETS": str(env_secrets)}, cwd=tmp_path)
    assert paths == prof.ConfigPaths(env_config, env_secrets)

    local_config = write_private(tmp_path / "tnt.toml", config_body())
    local_secret = write_private(tmp_path / "tnt.secrets.toml", secrets_body())
    paths = prof.resolve_paths(env={"TNT_CONFIG": str(env_config), "TNT_SECRETS": str(env_secrets)}, cwd=tmp_path)
    assert paths == prof.ConfigPaths(env_config, env_secrets)
    paths = prof.resolve_paths(cwd=tmp_path, env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    assert paths == prof.ConfigPaths(local_config, local_secret)


@pytest.mark.parametrize("mode,owner_ok,accepted", [
    (0o600, True, True), (0o640, True, False), (0o400, True, False), (0o600, False, False),
])
def test_config_is_checked_on_opened_descriptor_with_exact_mode_and_owner(tmp_path, monkeypatch, mode, owner_ok, accepted):
    path = tmp_path / "tnt.toml"
    path.write_text(config_body())
    path.chmod(mode)
    uid = path.stat().st_uid
    if not owner_ok:
        monkeypatch.setattr(os, "geteuid", lambda: uid + 1)
    if accepted:
        assert prof.load_profiles(path)[0]["lab"].url.startswith("wss://")
    else:
        with pytest.raises(ProfileError, match="mode 0600|owned by uid"):
            prof.load_profiles(path)


def test_config_symlink_is_refused(tmp_path):
    target = write_private(tmp_path / "real.toml", config_body())
    link = tmp_path / "tnt.toml"
    link.symlink_to(target)
    with pytest.raises(ProfileError, match="symlink|no-follow"):
        prof.load_profiles(link)


def test_secrets_are_loaded_only_from_the_paired_file_and_redacted(tmp_path):
    config = write_private(tmp_path / "tnt.toml", config_body())
    secrets = write_private(tmp_path / "tnt.secrets.toml", secrets_body())
    hosts, _ = prof.load_profiles(config)
    entries = prof.load_secrets(secrets)
    assert prof.read_credential(hosts["lab"], entries).reveal() == "1-fake-key-material"
    assert prof.read_credential(hosts["throwaway"], entries).reveal() == "fake-password-material"
    assert "fake-key-material" not in repr(entries) and "fake-password-material" not in repr(entries)
    assert "fake-key-material" not in repr(hosts["lab"])
    assert "id_ed25519" not in str(hosts["lab"].describe())
    assert "/home/operator/.ssh/known_hosts" not in str(hosts["lab"].describe())
    assert "1-fake-key-material" not in str(hosts["lab"].describe())


def test_cwd_config_cannot_pair_an_xdg_credential_saved_for_another_endpoint(tmp_path):
    cwd = tmp_path / "project"
    xdg = tmp_path / "xdg"
    config = write_private(cwd / "tnt.toml", '''schema_version = 1
default_host = "lab"

[hosts.lab]
url = "wss://appliance-a.example.invalid/api/current"
username = "operator"
auth = "api_key"
''')
    secrets = write_private(xdg / "tnt" / "tnt.secrets.toml", '''schema_version = 1

[hosts.lab]
host_url = "wss://appliance-b.example.invalid/api/current"
api_key = "1-credential-for-b"
''')

    paths = prof.resolve_paths(env={"XDG_CONFIG_HOME": str(xdg)}, cwd=cwd)
    assert paths.config == config and paths.secrets == secrets
    hosts, _ = prof.load_profiles(paths.config)
    entries = prof.load_secrets(paths.secrets)
    with pytest.raises(ProfileError, match="bound to a different host URL"):
        prof.read_credential(hosts["lab"], entries)


def test_legacy_unbound_secret_is_refused_until_replaced(tmp_path):
    path = write_private(tmp_path / "tnt.secrets.toml", '''schema_version = 1
[hosts.lab]
api_key = "1-legacy-key"
''')
    hosts, _ = prof.load_profiles(write_private(tmp_path / "tnt.toml", config_body()))
    with pytest.raises(ProfileError, match="no endpoint binding"):
        prof.read_credential(hosts["lab"], prof.load_secrets(path))


def test_config_rejects_any_secret_material_before_host_validation(tmp_path):
    path = write_private(tmp_path / "tnt.toml", '''schema_version=1
[hosts.lab]
url="wss://lab.example.invalid/api/current"
username="operator"
auth="api_key"
api_key="do-not-echo-this"
''')
    with pytest.raises(ProfileError) as exc:
        prof.load_profiles(path)
    assert "credentials belong in tnt.secrets.toml" in str(exc.value)
    assert "do-not-echo-this" not in str(exc.value)


def test_secret_file_requires_exact_mode_owner_regular_file_and_no_symlink(tmp_path, monkeypatch):
    path = write_private(tmp_path / "tnt.secrets.toml", secrets_body())
    path.chmod(0o640)
    with pytest.raises(ProfileError, match="mode 0600"):
        prof.load_secrets(path)
    path.chmod(0o600)
    uid = path.stat().st_uid
    real_geteuid = os.geteuid
    monkeypatch.setattr(os, "geteuid", lambda: uid + 1)
    with pytest.raises(ProfileError, match="owned by uid"):
        prof.load_secrets(path)
    monkeypatch.setattr(os, "geteuid", real_geteuid)


def test_secret_file_symlink_is_refused(tmp_path):
    target = write_private(tmp_path / "secret-content", secrets_body())
    link = tmp_path / "tnt.secrets.toml"
    link.symlink_to(target)
    with pytest.raises(ProfileError, match="symlink|no-follow"):
        prof.load_secrets(link)


def test_private_open_refuses_missing_nofollow_support_and_non_regular_files(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.delattr(prof.os, "O_NOFOLLOW", raising=False)
        with pytest.raises(ProfileError, match="no-follow file opens"):
            prof._open_private(tmp_path / "unused", "config file")
    with pytest.raises(ProfileError, match="not a regular file"):
        prof._open_private(tmp_path, "config file")


def test_decode_errors_and_secret_detection_do_not_echo_source(tmp_path):
    path = tmp_path / "tnt.toml"
    path.write_bytes(b"schema_version=1\n# \xff credential-looking bytes")
    path.chmod(0o600)
    with pytest.raises(ProfileError, match="UnicodeDecodeError") as caught:
        prof.load_profiles(path)
    assert "credential-looking" not in str(caught.value)
    assert prof._contains_secret_key({"nested": [{"Password": "secret"}]})
    assert not prof._contains_secret_key({"description": ["api_key is mentioned"]})


@pytest.mark.parametrize("field,value,message", [
    ("ssh_user", "", "ssh_user"),
    ("ssh_key_files", "not-an-array", "ssh_key_files"),
    ("ssh_key_files", [""], "ssh_key_files"),
    ("ssh_known_hosts", "", "ssh_known_hosts"),
    ("description", 4, "description"),
    ("tags", [""], "tags"),
    ("tags", "not-an-array", "tags"),
])
def test_optional_host_fields_are_type_checked(tmp_path, field, value, message):
    import json

    rendered = json.dumps(value)
    path = write_private(tmp_path / "tnt.toml", f'''schema_version=1
[hosts.lab]
url="wss://lab.example.invalid/api/current"
username="operator"
{field}={rendered}
''')
    with pytest.raises(ProfileError, match=message):
        prof.load_profiles(path)


def test_secret_read_helpers_and_key_file_problem_cover_missing_and_invalid_files(tmp_path):
    config = write_private(tmp_path / "tnt.toml", config_body())
    hosts, _ = prof.load_profiles(config)
    lab = hosts["lab"]
    with pytest.raises(ProfileError, match="API key starts"):
        prof.read_credential(lab, {"lab": prof.SecretEntry(api_key=prof.Secret("/tmp/path"), host_url=lab.url)})
    with pytest.raises(ProfileError, match="no API key configured"):
        prof.read_credential(lab, {"lab": prof.SecretEntry()})
    with pytest.raises(ProfileError, match="API-key credentials requested"):
        prof.read_secret(hosts["throwaway"], {})
    with pytest.raises(ProfileError, match="password credentials requested"):
        prof.read_password(lab, {})
    with pytest.raises(ProfileError, match="does not match auth"):
        prof.read_password(hosts["throwaway"], {"throwaway": prof.SecretEntry(
            api_key=prof.Secret("1-key"), host_url=hosts["throwaway"].url)})
    with pytest.raises(ProfileError, match="no password configured"):
        prof.read_password(hosts["throwaway"], {"throwaway": prof.SecretEntry()})
    empty = write_private(tmp_path / "empty.key", "\n")
    with pytest.raises(ProfileError, match="API key file is empty"):
        prof._read_key_file(str(empty))
    absolute = write_private(tmp_path / "absolute.key", "/tmp/not-a-key")
    with pytest.raises(ProfileError, match="not API key material"):
        prof._read_key_file(str(absolute))
    invalid = write_private(tmp_path / "invalid.key", "valid")
    invalid.write_bytes(b"\xff")
    invalid.chmod(0o600)
    with pytest.raises(ProfileError, match="UnicodeDecodeError"):
        prof._read_key_file(str(invalid))
    assert prof.key_file_problem(empty) is None
    loose = write_private(tmp_path / "loose.key", "key")
    loose.chmod(0o640)
    assert "mode 0600" in prof.key_file_problem(loose)


@pytest.mark.parametrize("body,match", [
    ('schema_version=1\n[hosts.lab]\napi_key=""\n', "non-empty string"),
    ('schema_version=1\n[hosts.lab]\npassword="p"\napi_key="k"\n', "exactly one credential source"),
    ('schema_version=1\n[hosts.lab]\napi_key_file="relative"\n', "absolute path"),
    ('schema_version=1\n[hosts.lab]\napi_key="/tmp/key"\n', "key material"),
    ('schema_version=2\n[hosts.lab]\napi_key="x"\n', "schema_version must be 1"),
    ('schema_version=1\n[hosts."bad name"]\napi_key="x"\n', "host name"),
])
def test_invalid_secret_files(tmp_path, body, match):
    path = write_private(tmp_path / "tnt.secrets.toml", body)
    with pytest.raises(ProfileError, match=match):
        prof.load_secrets(path)


def test_secret_credentials_must_match_auth_and_host(tmp_path):
    config = write_private(tmp_path / "tnt.toml", config_body())
    secrets = write_private(tmp_path / "tnt.secrets.toml", 'schema_version=1\n[hosts.other]\napi_key="1-secret"\n')
    hosts, _ = prof.load_profiles(config)
    entries = prof.load_secrets(secrets)
    with pytest.raises(ProfileError, match="no credentials for host 'lab'"):
        prof.read_credential(hosts["lab"], entries)
    secrets.write_text('schema_version=1\n[hosts.lab]\npassword="x"\n')
    secrets.chmod(0o600)
    with pytest.raises(ProfileError, match="does not match auth"):
        prof.read_credential(hosts["lab"], prof.load_secrets(secrets))


def test_api_key_file_is_opened_without_symlink_and_requires_exact_0600(tmp_path):
    key = write_private(tmp_path / "key", "1-file-key\n")
    secrets = write_private(tmp_path / "tnt.secrets.toml", secrets_body(key))
    hosts, _ = prof.load_profiles(write_private(tmp_path / "tnt.toml", config_body()))
    assert prof.read_credential(hosts["lab"], prof.load_secrets(secrets)).reveal() == "1-file-key"
    key.chmod(0o400)
    with pytest.raises(ProfileError, match="mode 0600"):
        prof.read_credential(hosts["lab"], prof.load_secrets(secrets))
    key.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(key)
    secrets.write_text(secrets_body(link))
    secrets.chmod(0o600)
    with pytest.raises(ProfileError, match="symlink|no-follow"):
        prof.read_credential(hosts["lab"], prof.load_secrets(secrets))


def test_secret_repr_and_scrub_encodings():
    secret = prof.Secret("1-topsecret")
    assert "topsecret" not in repr(secret) and "topsecret" not in str(secret)
    assert secret.scrub("login 1-topsecret failed") == "login <redacted> failed"


def test_profile_repr_and_description_hide_ssh_key_paths():
    profile = prof.Profile("lab", "wss://lab.example.invalid/api/current", "operator",
                           ssh_key_files=("/private/ssh/key",), ssh_known_hosts="/private/known_hosts")
    assert "/private/ssh/key" not in repr(profile)
    assert "/private/known_hosts" not in repr(profile)
    assert "/private/ssh/key" not in repr(profile.describe())
    assert profile.describe()["ssh_key_files_configured"] is True


@pytest.mark.parametrize("url,match", [
    ("wss://user:pw@h.example.invalid/api/current", "must not carry credentials"),
    ("wss://user@h.example.invalid/x", "must not carry credentials"),
    ("wss://h.example.invalid/api/current?api_key=1-abc", "must not carry credentials"),
    ("wss://h.example.invalid/x#frag", "must not carry credentials"),
    ("wss://h.example.invalid:notaport/x", "valid URL"),
])
def test_invalid_urls_are_refused_without_echo(tmp_path, url, match):
    path = write_private(tmp_path / "tnt.toml", f'schema_version=1\n[hosts.a]\nurl="{url}"\nusername="u"\n')
    with pytest.raises(ProfileError, match=match) as exc:
        prof.load_profiles(path)
    assert "api_key=1-abc" not in str(exc.value)


@pytest.mark.parametrize("body,match", [
    ("schema_version=1\nunknown=3\n", "unknown top-level"),
    ("schema_version=1\n[hosts.lab]\nurl=\"wss://lab.example.invalid/api/current\"\nusername=\"u\"\nextra=3\n", "unknown key"),
])
def test_loader_rejects_unknown_config_keys(tmp_path, body, match):
    path = write_private(tmp_path / "tnt.toml", body)
    with pytest.raises(ProfileError, match=match):
        prof.load_profiles(path)


@pytest.mark.parametrize("body,match", [
    ("schema_version=1\nunknown=3\n", "unknown secrets top-level"),
    ("schema_version=1\nhosts=[\"lab\"]\n", r"\[hosts.NAME\] tables are required"),
    ("schema_version=1\n[hosts.lab]\n", "exactly one credential source"),
    ("schema_version=1\n[hosts.lab]\napi_key=3\n", "non-empty string"),
    ("schema_version=1\n[hosts.lab]\napi_key_file=3\n", "absolute path"),
    ("schema_version=1\n[hosts]\nlab=3\n", "expected a TOML table"),
    ("schema_version=1\n[hosts.lab]\nother=\"x\"\n", "unknown key"),
])
def test_loader_rejects_invalid_secrets_tables(tmp_path, body, match):
    path = write_private(tmp_path / "tnt.secrets.toml", body)
    with pytest.raises(ProfileError, match=match):
        prof.load_secrets(path)


def test_profile_io_errors_and_default_path_fallbacks(tmp_path, monkeypatch):
    import errno

    path = write_private(tmp_path / "tnt.toml", config_body())
    real_open = os.open

    def inaccessible(*args, **kwargs):
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(prof.os, "open", inaccessible)
    with pytest.raises(ProfileError, match="cannot open config file"):
        prof.load_profiles(path)
    monkeypatch.setattr(prof.os, "open", real_open)

    real_read = os.read
    monkeypatch.setattr(prof.os, "read", lambda *_: (_ for _ in ()).throw(OSError("read failed")))
    with pytest.raises(ProfileError, match="cannot read config file.*OSError"):
        prof.load_profiles(path)
    monkeypatch.setattr(prof.os, "read", real_read)

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert prof.default_config_path({}, cwd=tmp_path / "elsewhere") == tmp_path / "home" / ".config" / "tnt" / "tnt.toml"
    assert prof.default_secrets_path({}, cwd=tmp_path / "elsewhere") == tmp_path / "home" / ".config" / "tnt" / "tnt.secrets.toml"
    with pytest.raises(ProfileError, match="several hosts"):
        prof.pick({}, None, None)


def test_toml_decoder_rejects_non_table_and_password_lookup_requires_an_entry(tmp_path, monkeypatch):
    path = write_private(tmp_path / "tnt.toml", config_body())
    monkeypatch.setattr(prof.tomllib, "loads", lambda _text: [])
    with pytest.raises(ProfileError, match="must contain a TOML table"):
        prof.load_profiles(path)

    monkeypatch.undo()
    hosts, _ = prof.load_profiles(path)
    with pytest.raises(ProfileError, match="no credentials for host 'throwaway'"):
        prof.read_password(hosts["throwaway"], {})
