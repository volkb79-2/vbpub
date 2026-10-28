from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path

import pytest

from tnt.errors import ProfileError
from tnt.host_store import HostStore, _inline_comment, _line_table_name, _patch_table, _secret_values, _serialized, _set_default, _stage, _table_bounds, _table_header, _toml_value, _validate_pair, load_host_store
from tnt.profile import ConfigPaths, Profile, Secret, SecretEntry, load_profiles, load_secrets


def setup_store(tmp_path):
    config = tmp_path / "tnt.toml"
    secrets = tmp_path / "tnt.secrets.toml"
    config.write_text('''# keep this operator note
schema_version = 1
default_host = "lab"

# primary host comment
[hosts.lab]
url = "wss://lab.example.invalid/api/current"
username = "operator"
auth = "api_key"
tls = "verify"
description = "Lab"
''')
    config.chmod(0o600)
    secrets.write_text('''# secret file comment
schema_version = 1

# lab key comment
[hosts.lab]
api_key = "1-old-key"
''')
    secrets.chmod(0o600)
    return ConfigPaths(config, secrets), HostStore(ConfigPaths(config, secrets))


def host(name, url=None, **kw):
    return Profile(name=name, url=url or f"wss://{name}.example.invalid/api/current",
                   username="operator", **kw)


def test_add_edit_rename_and_default_are_atomic_private_and_keep_comments(tmp_path):
    paths, store = setup_store(tmp_path)
    added = host("backup", auth="password", description="Backup", tags=("archive",))
    store.add(added, SecretEntry(password=Secret("fake-password")))
    assert store.set_default("backup")
    edited = host("backup", "wss://backup-2.example.invalid/api/current", auth="password",
                  description="Updated", tags=("archive", "cold"))
    store.edit("backup", edited)
    store.rename("backup", "vault")
    assert store.set_default("vault")
    hosts, default = load_profiles(paths.config)
    entries = load_secrets(paths.secrets)
    assert default == "vault" and "backup" not in hosts and hosts["vault"].description == "Updated"
    assert entries["vault"].password.reveal() == "fake-password"
    assert "# keep this operator note" in paths.config.read_text()
    assert "# primary host comment" in paths.config.read_text()
    assert "# secret file comment" in paths.secrets.read_text()
    assert paths.config.stat().st_mode & 0o777 == 0o600
    assert paths.secrets.stat().st_mode & 0o777 == 0o600


def test_remove_clears_default_and_secret_entry(tmp_path):
    paths, store = setup_store(tmp_path)
    store.remove("lab")
    hosts, default = load_profiles(paths.config)
    assert hosts == {} and default is None
    assert "[hosts.lab]" not in paths.secrets.read_text()


def test_edit_can_clear_optional_host_facts(tmp_path):
    paths, store = setup_store(tmp_path)
    store.edit("lab", host("lab", ssh_user="admin", ssh_key_files=("/tmp/key",),
                             ssh_known_hosts="/tmp/known_hosts", description="Old description", tags=("old",)))
    store.edit("lab", host("lab"))
    loaded = load_profiles(paths.config)[0]["lab"]
    assert loaded.ssh_user is None and loaded.ssh_key_files == () and loaded.ssh_known_hosts is None
    assert loaded.description == "" and loaded.tags == ()
    assert "ssh_key_files" not in paths.config.read_text()


def test_host_edits_are_serialized_without_lost_updates(tmp_path):
    paths, store = setup_store(tmp_path)
    barrier = __import__("threading").Barrier(2)

    def add(name):
        barrier.wait()
        store.add(host(name, auth="api_key", description=name), SecretEntry(api_key=Secret(f"1-{name}-key")))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(add, name) for name in ("one", "two")]
        for future in futures:
            future.result()
    assert set(load_profiles(paths.config)[0]) == {"lab", "one", "two"}
    assert set(load_secrets(paths.secrets)) == {"lab", "one", "two"}


def test_pair_write_rolls_back_first_file_if_second_replace_fails(tmp_path, monkeypatch):
    paths, store = setup_store(tmp_path)
    before_config = paths.config.read_bytes()
    before_secrets = paths.secrets.read_bytes()
    real_replace = __import__("os").replace
    failed = False

    def fail_secrets_once(source, target):
        nonlocal failed
        if Path(target) == paths.secrets and not failed:
            failed = True
            raise OSError("synthetic replace failure")
        return real_replace(source, target)

    monkeypatch.setattr("tnt.host_store.os.replace", fail_secrets_once)
    with pytest.raises(ProfileError, match="transaction failed and was rolled back"):
        store.add(host("new"), SecretEntry(api_key=Secret("1-new-key")))
    assert paths.config.read_bytes() == before_config
    assert paths.secrets.read_bytes() == before_secrets
    assert set(load_profiles(paths.config)[0]) == {"lab"}


def test_store_refuses_symlinked_config_and_secrets_files(tmp_path):
    paths, store = setup_store(tmp_path)
    target = tmp_path / "copy"
    target.write_bytes(paths.config.read_bytes())
    target.chmod(0o600)
    paths.config.unlink()
    paths.config.symlink_to(target)
    with pytest.raises(ProfileError, match="symlink|no-follow"):
        store.add(host("new"), SecretEntry(api_key=Secret("1-new-key")))


def test_set_default_rejects_unknown_host(tmp_path):
    _, store = setup_store(tmp_path)
    with pytest.raises(ProfileError, match="unknown host"):
        store.set_default("missing")


def test_toml_patch_helpers_preserve_inline_comments_and_quote_host_names():
    assert _toml_value("a\\b\"c") == '"a\\\\b\\\"c"'
    assert _toml_value(True) == "true"
    assert _toml_value(("a", "b")) == '["a", "b"]'
    with pytest.raises(ProfileError, match="unsupported TOML value"):
        _toml_value(1)
    assert _table_header("host_name") == "[hosts.host_name]"
    assert _table_header("host.name") == '[hosts."host.name"]'
    assert _line_table_name('[hosts."host.name"] # note') == "host.name"
    assert _line_table_name("[hosts.host_name]") == "host_name"
    assert _line_table_name("[other.host]") is None
    text = 'schema_version = 1\n\n[hosts.lab]\nurl = "wss://old" # operator note\n'
    assert _table_bounds(text.splitlines(keepends=True), "missing") is None
    changed = _patch_table(text, "lab", {"url": "wss://new", "description": "new"})
    assert 'url = "wss://new" # operator note' in changed
    assert 'description = "new"' in changed
    assert _inline_comment('value = "# inside" # outside') == " # outside"
    assert _patch_table(text, "missing", {}, remove=True) == text
    with pytest.raises(ProfileError, match="table .* is missing"):
        _patch_table(text, "missing", {})


def test_toml_patch_helpers_create_remove_rename_and_clear_optional_fields():
    created = _patch_table("schema_version = 1\n", "with.dot", {"url": "wss://x"}, create=True)
    assert '[hosts."with.dot"]' in created and 'url = "wss://x"' in created
    original = created + "\n[hosts.other]\nurl = \"wss://other\"\n"
    with pytest.raises(ProfileError, match="already exists"):
        _patch_table(original, "with.dot", {}, rename_to="other")
    cleared = _patch_table(original, "with.dot", {"url": None}, rename_to="renamed")
    assert "[hosts.renamed]" in cleared and "url =" not in cleared.split("[hosts.other]")[0]
    removed = _patch_table(cleared, "renamed", {}, remove=True)
    assert "renamed" not in removed and "other" in removed
    assert _set_default("schema_version = 1\n", None) == "schema_version = 1\n"
    assert 'default_host = "lab"' in _set_default("schema_version = 1\n[hosts.lab]\n", "lab")
    duplicate = 'schema_version = 1\ndefault_host = "old" # keep\ndefault_host="duplicate"\n[hosts.lab]\n'
    changed = _set_default(duplicate, "lab")
    assert changed.count("default_host") == 1 and 'default_host = "lab" # keep' in changed


def test_add_can_create_a_missing_pair_and_rejects_duplicate_or_invalid_records(tmp_path):
    paths = ConfigPaths(tmp_path / "nested" / "tnt.toml", tmp_path / "nested" / "tnt.secrets.toml")
    store = HostStore(paths)
    store.add(host("fresh"), SecretEntry(api_key=Secret("1-fresh-key")), make_default=True)
    assert paths.config.stat().st_mode & 0o777 == 0o600
    assert paths.secrets.stat().st_mode & 0o777 == 0o600
    assert load_host_store(paths)[1] == "fresh"
    with pytest.raises(ProfileError, match="already exists"):
        store.add(host("fresh"), SecretEntry(api_key=Secret("1-fresh-key")))
    with pytest.raises(ProfileError, match="exactly one"):
        store.add(host("other"), SecretEntry())
    with pytest.raises(ProfileError, match="TLS or auth"):
        store.add(host("other", tls="off"), SecretEntry(api_key=Secret("1-other-key")))


def test_edit_and_rename_refuse_bad_names_and_preserve_secret_when_not_replaced(tmp_path):
    paths, store = setup_store(tmp_path)
    with pytest.raises(ProfileError, match="use hosts rename"):
        store.edit("lab", host("renamed"))
    with pytest.raises(ProfileError, match="unknown host"):
        store.edit("missing", host("missing"))
    with pytest.raises(ProfileError, match="unknown host"):
        store.rename("missing", "other")
    with pytest.raises(ProfileError, match="already exists"):
        store.rename("lab", "lab")
    with pytest.raises(ProfileError, match="original host"):
        store.rename("lab", "new", profile=host("not-lab"))
    store.edit("lab", host("lab", description="changed"), SecretEntry(api_key=Secret("1-new-key")))
    assert load_secrets(paths.secrets)["lab"].api_key.reveal() == "1-new-key"
    with pytest.raises(ProfileError, match="auth mode"):
        store.edit("lab", host("lab", auth="password"))


def test_rename_without_existing_secret_can_add_credential_and_remove_unknown_refuses(tmp_path):
    paths, store = setup_store(tmp_path)
    # A valid host can temporarily lack a credential; rename can pair one at the new name.
    store.remove("lab")
    store.add(host("old"), SecretEntry(api_key=Secret("1-old-key")))
    store.rename("old", "new", credential=SecretEntry(api_key=Secret("1-new-key")))
    assert "new" in load_host_store(paths)[0]
    assert load_secrets(paths.secrets)["new"].api_key.reveal() == "1-new-key"
    with pytest.raises(ProfileError, match="unknown host"):
        store.remove("absent")


def test_rename_adds_a_missing_secret_entry_and_set_default_can_clear(tmp_path):
    config = tmp_path / "tnt.toml"
    secrets = tmp_path / "tnt.secrets.toml"
    config.write_text('''schema_version = 1
[hosts.old]
url = "wss://old.example.invalid/api/current"
username = "operator"
''')
    config.chmod(0o600)
    secrets.write_text("schema_version = 1\n")
    secrets.chmod(0o600)
    paths = ConfigPaths(config, secrets)
    store = HostStore(paths)
    store.rename("old", "new", credential=SecretEntry(api_key=Secret("1-new-key")))
    assert load_secrets(secrets)["new"].api_key.reveal() == "1-new-key"
    assert store.set_default(None) is False
    assert load_host_store(paths)[1] is None


def test_pair_transaction_removes_new_file_when_second_replace_fails(tmp_path, monkeypatch):
    paths = ConfigPaths(tmp_path / "new" / "tnt.toml", tmp_path / "new" / "tnt.secrets.toml")
    store = HostStore(paths)
    real_replace = os.replace

    def fail_secrets(source, target):
        if Path(target) == paths.secrets:
            raise OSError("synthetic failure")
        return real_replace(source, target)

    monkeypatch.setattr("tnt.host_store.os.replace", fail_secrets)
    with pytest.raises(ProfileError, match="rolled back"):
        store.add(host("new"), SecretEntry(api_key=Secret("1-new-key")))
    assert not paths.config.exists() and not paths.secrets.exists()


def test_load_host_store_checks_missing_or_orphaned_and_wrong_auth_secrets(tmp_path):
    config = tmp_path / "tnt.toml"
    secrets = tmp_path / "tnt.secrets.toml"
    config.write_text('schema_version=1\n[hosts.lab]\nurl="wss://lab.example.invalid/api/current"\nusername="u"\n')
    config.chmod(0o600)
    paths = ConfigPaths(config, secrets)
    assert load_host_store(paths)[2] == {}
    with pytest.raises(ProfileError, match="no secrets file"):
        load_host_store(paths, require_secrets=True)
    secrets.write_text('schema_version=1\n[hosts.ghost]\napi_key="1-ghost-key"\n')
    secrets.chmod(0o600)
    with pytest.raises(ProfileError, match="not in the paired config"):
        load_host_store(paths)
    secrets.write_text('schema_version=1\n[hosts.lab]\npassword="pw"\n')
    secrets.chmod(0o600)
    with pytest.raises(ProfileError, match="does not match auth"):
        load_host_store(paths)


def test_locked_host_files_refuse_symlinked_lock_and_rollback_failure(tmp_path, monkeypatch):
    paths, store = setup_store(tmp_path)
    target = tmp_path / "lock-target"
    target.write_text("")
    (tmp_path / ".tnt.toml.lock").symlink_to(target)
    with pytest.raises(ProfileError, match="cannot lock host files"):
        store.add(host("new"), SecretEntry(api_key=Secret("1-new-key")))
    (tmp_path / ".tnt.toml.lock").unlink()

    before = paths.config.read_bytes()
    real_replace = os.replace
    config_replacements = 0

    def fail_commit_and_rollback(source, target_path):
        nonlocal config_replacements
        source_path = Path(source)
        if Path(target_path) == paths.secrets:
            raise OSError("fail second replace")
        if Path(target_path) == paths.config and source_path.name.startswith(".tnt.toml."):
            config_replacements += 1
            if config_replacements == 2:
                raise OSError("fail rollback")
        return real_replace(source, target_path)

    monkeypatch.setattr("tnt.host_store.os.replace", fail_commit_and_rollback)
    with pytest.raises(ProfileError, match="rollback also failed"):
        store.add(host("new"), SecretEntry(api_key=Secret("1-new-key")))
    assert paths.config.read_bytes() != before


def test_lock_failure_after_open_closes_descriptor_and_optional_host_removal_keeps_default(tmp_path, monkeypatch):
    from tnt import host_store

    paths, store = setup_store(tmp_path)
    with pytest.raises(ProfileError, match="host name"):
        store.rename("lab", "bad/name")

    store.add(host("secondary"), SecretEntry(api_key=Secret("1-secondary-key")))
    paths.secrets.write_text("schema_version = 1\n")
    paths.secrets.chmod(0o600)
    store.remove("secondary")
    hosts, default = load_profiles(paths.config)
    assert default == "lab" and set(hosts) == {"lab"}

    real_open = os.open
    opened = []

    def track_open(path, flags, mode=0o777):
        fd = real_open(path, flags, mode)
        opened.append(fd)
        return fd

    def lock_failure(*_args):
        raise OSError("lock busy")

    monkeypatch.setattr(host_store.os, "open", track_open)
    monkeypatch.setattr(host_store.fcntl, "flock", lock_failure)
    with pytest.raises(ProfileError, match="cannot lock host files"):
        with _serialized(paths, create_parents=False):
            pytest.fail("flock failure must abort before entering the transaction")
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize("config_text,secrets_text,match", [
    ("schema_version=2\n", "schema_version=1\n", "config schema_version"),
    ("schema_version=1\n[hosts.lab]\nurl=\"wss://x\"\nusername=\"u\"\napi_key=\"x\"\n",
     "schema_version=1\n", "credentials belong in tnt.secrets"),
    ("schema_version=1\n[hosts.lab]\nurl=\"wss://x\"\nusername=\"u\"\n",
     "schema_version=1\n[hosts.lab]\napi_key=\"/tmp/key\"\n", "must be key material"),
    ("schema_version=1\nunknown=3\n", "schema_version=1\n", "unknown top-level"),
    ("schema_version=1\n", "schema_version=1\nunknown=3\n", "unknown top-level"),
    ("not TOML", "schema_version=1\n", "valid TOML"),
])
def test_pair_validator_rejects_invalid_schema_or_credentials(config_text, secrets_text, match):
    with pytest.raises(ProfileError, match=match):
        _validate_pair(config_text, secrets_text)


def test_toml_patch_escaped_quotes_duplicate_fields_and_newline_edges():
    assert _inline_comment(r'value = "a\\\"#inside" # outside') == " # outside"
    original = 'schema_version = 1\n[hosts.lab]\nurl = "first" # keep this\nurl = "duplicate"\n[hosts.other]\nurl = "other"\n'
    patched = _patch_table(original, "lab", {"url": "updated"})
    assert patched.count("url =") == 2
    assert 'url = "updated" # keep this' in patched
    cleared = _patch_table(patched, "lab", {"url": None, "description": None})
    assert "url =" not in cleared.split("[hosts.other]")[0]
    assert _patch_table("", "plain", {"url": "wss://host"}, create=True) == (
        '[hosts.plain]\nurl = "wss://host"\n')
    assert _patch_table("schema_version=1", "plain", {}, create=True).endswith("\n[hosts.plain]\n")
    assert _secret_values(None) == {"api_key": None, "api_key_file": None, "password": None}


def test_store_file_decode_and_serialization_preflight_errors(tmp_path, monkeypatch):
    from tnt import host_store

    paths = ConfigPaths(tmp_path / "tnt.toml", tmp_path / "tnt.secrets.toml")
    paths.config.write_bytes(b"\xff")
    paths.config.chmod(0o600)
    with pytest.raises(ProfileError, match="cannot decode config file"):
        HostStore(paths).add(host("new"), SecretEntry(api_key=Secret("1-new-key")))

    absent = ConfigPaths(tmp_path / "absent" / "tnt.toml", tmp_path / "absent" / "tnt.secrets.toml")
    # Read-only operations do not create lock directories for paths that do not exist.
    with _serialized(absent, create_parents=False):
        pass
    assert not absent.config.parent.exists()

    (tmp_path / "unsafe").mkdir()
    regular, store = setup_store(tmp_path / "unsafe")
    with monkeypatch.context() as patch:
        patch.setattr(host_store.os, "O_NOFOLLOW", None, raising=False)
        with pytest.raises(ProfileError, match="no-follow file opens"):
            store.add(host("unsafe"), SecretEntry(api_key=Secret("1-unsafe-key")))

    real_fstat = os.fstat
    seen = []

    def report_directory(fd):
        seen.append(fd)
        return type("FakeStat", (), {"st_mode": 0o040755, "st_uid": os.geteuid()})()

    with monkeypatch.context() as patch:
        patch.setattr(host_store.os, "fstat", report_directory)
        with pytest.raises(ProfileError, match="unsafe host-file lock"):
            with _serialized(regular, create_parents=False):
                pytest.fail("unsafe lock should be refused before entering the critical section")
    with pytest.raises(OSError):
        real_fstat(seen[0])


def test_stage_failure_removes_partial_file(tmp_path, monkeypatch):
    from tnt import host_store

    path = tmp_path / "private.toml"
    monkeypatch.setattr(host_store.os, "fdopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        _stage(path, b"schema_version=1\n")
    assert list(tmp_path.glob(".private.toml.*.tmp")) == []


@pytest.mark.parametrize("config_text,secrets_text,match", [
    ("schema_version=1\nhosts=[]\n", "schema_version=1\n", "hosts must be TOML tables"),
    ("schema_version=1\nhosts.lab=\"bad\"\n", "schema_version=1\n", "host 'lab' is not a table"),
    ("schema_version=1\n", "schema_version=2\n", "secrets schema_version"),
    ("schema_version=1\n", "schema_version=1\nhosts=[]\n", "hosts must be TOML tables"),
    ("schema_version=1\n", "schema_version=1\nhosts.ghost.api_key=\"1-key\"\n", "do not match a configured host"),
    ("schema_version=1\n[hosts.lab]\nurl=\"wss://lab.example.invalid/api/current\"\nusername=\"u\"\n",
     "schema_version=1\nhosts.lab=\"bad\"\n", "do not match a configured host"),
    ("schema_version=1\ndefault_host=\"missing\"\n", "schema_version=1\n", "default host 'missing'"),
    ("schema_version=1\n[hosts.lab]\nurl=\"wss://lab.example.invalid/api/current\"\nusername=\"u\"\n",
     "schema_version=1\n[hosts.lab]\napi_key=\"\"\n", "non-empty credential"),
    ("schema_version=1\n[hosts.lab]\nurl=\"wss://lab.example.invalid/api/current\"\nusername=\"u\"\n",
     "schema_version=1\n[hosts.lab]\napi_key_file=\"relative\"\n", "must be absolute"),
    ("schema_version=1\n[hosts.lab]\nurl=\"wss://lab.example.invalid/api/current\"\nusername=\"u\"\n",
     "schema_version=1\n[hosts.lab]\npassword=\"p\"\n", "do not match its auth mode"),
])
def test_pair_validator_rejects_invalid_host_and_secret_tables(config_text, secrets_text, match):
    with pytest.raises(ProfileError, match=match):
        _validate_pair(config_text, secrets_text)


@pytest.mark.parametrize("operation,which", [
    ("add", "config"), ("edit", "config"), ("rename", "config"),
    ("rename", "secrets"), ("remove", "config"), ("remove", "secrets"), ("set_default", "config"),
])
def test_store_operations_refuse_malformed_toml(tmp_path, operation, which):
    paths, store = setup_store(tmp_path)
    path = paths.config if which == "config" else paths.secrets
    path.write_text("not valid TOML =")
    path.chmod(0o600)
    if operation == "add":
        call = lambda: store.add(host("new"), SecretEntry(api_key=Secret("1-new-key")))
    elif operation == "edit":
        call = lambda: store.edit("lab", host("lab"))
    elif operation == "rename":
        call = lambda: store.rename("lab", "renamed")
    elif operation == "remove":
        call = lambda: store.remove("lab")
    else:
        call = lambda: store.set_default("lab")
    with pytest.raises(ProfileError, match="malformed|valid TOML"):
        call()
