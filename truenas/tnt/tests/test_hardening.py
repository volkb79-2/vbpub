"""Low-severity review items: profile URL hygiene, key-file ownership and read errors, secret scrubbing, version parsing,
docs hygiene, gate and dependency configuration, menu notes."""
import base64
import json
import os
import tomllib
from pathlib import Path
from urllib.parse import quote

import pytest

from tnt import adapters, profile as prof
from tnt.errors import ProfileError, UnsupportedVersion
from tnt.menu import walk

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("url", ["wss://user:pw@h.example.invalid/api/current", "wss://user@h.example.invalid/x",
                                 "wss://h.example.invalid/api/current?api_key=1-abc", "wss://h.example.invalid/x#frag",
                                 "wss://h.example.invalid:notaport/x"])
def test_urls_with_userinfo_query_fragment_or_bad_port_are_rejected_and_never_echoed(tmp_path, url):
    path = tmp_path / "p.toml"
    path.write_text(f'schema_version=1\n[profiles.a]\nurl = "{url}"\nusername = "u"\n')
    path.chmod(0o600)
    with pytest.raises(ProfileError) as err:
        prof.load_profiles(path)
    assert "pw" not in str(err.value) and "api_key=1-abc" not in str(err.value)


def test_a_clean_url_with_a_port_is_accepted(tmp_path):
    path = tmp_path / "p.toml"
    path.write_text('schema_version=1\n[profiles.a]\nurl = "wss://h.example.invalid:8443/api/current"\nusername = "u"\n')
    path.chmod(0o600)
    assert prof.load_profiles(path)[0]["a"].describe()["url"] == "wss://h.example.invalid:8443/api/current"


def test_key_file_must_be_owned_by_the_current_user(tmp_path, monkeypatch):
    key = tmp_path / "k"
    key.write_text("1-abc")
    key.chmod(0o600)
    assert prof.key_file_problem(str(key)) is None
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(key).st_uid + 1)
    assert "not by you" in prof.key_file_problem(str(key))


def test_key_file_read_errors_are_wrapped(tmp_path):
    bad = tmp_path / "k"
    bad.write_bytes(b"\xff\xfe\x00bad")
    bad.chmod(0o600)
    p = prof.Profile("a", "wss://h.example.invalid/x", "u", api_key_file=str(bad))
    with pytest.raises(ProfileError, match="cannot read the key file"):
        prof.read_secret(p, {})
    gone = prof.Profile("a", "wss://h.example.invalid/x", "u", api_key_file=str(tmp_path / "missing"))
    with pytest.raises(ProfileError, match="cannot stat"):
        prof.read_secret(gone, {})


def test_a_key_starting_with_slash_is_never_passed_on(tmp_path):
    p = prof.Profile("a", "wss://h.example.invalid/x", "u")
    with pytest.raises(ProfileError, match="starts with '/'"):
        prof.read_secret(p, {"TNT_API_KEY": "/etc/shadow"})
    f = tmp_path / "k"
    f.write_text("/etc/shadow")
    f.chmod(0o600)
    with pytest.raises(ProfileError, match="starts with '/'"):
        prof.read_secret(p, {"TNT_API_KEY_FILE": str(f)})


def test_scrub_redacts_encodings_and_the_secret_part_of_the_key():
    key = "12-AbCdEfGhIjKlMnOpQrStUvWxYz0123"
    secret = prof.Secret(key)
    material = key.split("-", 1)[1]
    forms = [key, material, quote(key, safe=""), key.encode().hex(), key.encode().hex().upper(), json.dumps(key)[1:-1],
             base64.b64encode(key.encode()).decode(), base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")]
    for form in forms:
        out = secret.scrub(f"error near {form} here")
        assert form not in out and "<redacted>" in out, form
    assert secret.scrub("nothing to hide") == "nothing to hide"
    assert prof.Secret("1-k").scrub("bad 1-k") == "bad <redacted>"  # a short key is still scrubbed exactly


@pytest.mark.parametrize("version", ["v127.0", "TrueNAS-127.0.1", "27.012", "1.2.3", "TrueNAS-27"])
def test_version_parsing_is_anchored(version):
    with pytest.raises(UnsupportedVersion):
        adapters.select(version)


@pytest.mark.parametrize("version", ["TrueNAS-SCALE-25.10.3", "TrueNAS-26.0.0-BETA.1"])
def test_non_v27_version_parsing(version):
    with pytest.raises(UnsupportedVersion):
        adapters.select(version)


def test_version_parsing_accepts_v27_shapes():
    version, major = "TrueNAS-27.0.0-RC.1", 27
    assert adapters.parse_major(version) == major


def test_docs_do_not_name_private_hosts():
    banned = ("r1002", "v1001", "nano1", "netcup")
    for rel in ("BACKLOG.md", "README.md", "CHANGES.md", "docs/DESIGN.md", "docs/ACTION-MATRIX.snapshot.md", "pyproject.toml"):
        text = (ROOT / rel).read_text().lower()
        assert not [b for b in banned if b in text], rel


def test_gate_has_a_coverage_floor():
    text = (ROOT / "run-gate.toml").read_text()
    assert "--cov-fail-under=" in text


def test_websockets_is_a_test_only_dependency():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert not any("websockets" in d for d in meta["dependencies"])
    assert not any("websockets" in d for d in meta["optional-dependencies"]["tui"])
    assert any("websockets" in d for d in meta["optional-dependencies"]["test"])


def test_menu_marks_conditional_entries_and_uses_upstream_route_keys():
    conditional = sorted(i.label for _, i in walk() if i.conditional)
    assert conditional == ["Apps", "Containers", "Enclosure", "KMIP", "Virtual Machines"]
    assert "reportsdashboard/cpu" in [i.key for _, i in walk()]
