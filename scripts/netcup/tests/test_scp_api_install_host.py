"""Tests for scp-api-install-host.py: pure logic, settings loader, identity
auto-gen, the interactive fill-in helpers, and the HTTP client - all mocked/
local, no live netcup calls."""
from __future__ import annotations

import io
import json
import types
import urllib.error

import pytest

from conftest import FakeHTTPResponse


# --- pure helpers --------------------------------------------------------

def test_strip_jsonc_comments_line_and_block(install_host_mod):
    text = '{\n  "a": 1, // comment\n  "b": /* block */ 2\n}'
    parsed = json.loads(install_host_mod._strip_jsonc_comments(text))
    assert parsed == {"a": 1, "b": 2}


def test_strip_jsonc_comments_preserves_urls_in_strings(install_host_mod):
    text = '{"customScript": "curl -fsSL https://example.com/x.sh | bash"}'
    parsed = json.loads(install_host_mod._strip_jsonc_comments(text))
    assert "https://example.com/x.sh" in parsed["customScript"]


def test_redact_for_log_masks_sensitive_keys(install_host_mod):
    data = {"rootPassword": "secret", "nested": {"access_token": "tok"}, "ok": "visible"}
    redacted = install_host_mod._redact_for_log(data)
    assert redacted["rootPassword"] == "***REDACTED***"
    assert redacted["nested"]["access_token"] == "***REDACTED***"
    assert redacted["ok"] == "visible"


def test_redact_for_log_masks_custom_script_by_length(install_host_mod):
    redacted = install_host_mod._redact_for_log({"customScript": "abcde"})
    assert "REDACTED" in redacted["customScript"]
    assert "len=5" in redacted["customScript"]


def test_expand_payload_placeholders_substitutes_controller_ssh_pubkey(install_host_mod, monkeypatch):
    monkeypatch.setenv("CONTROLLER_SSH_PUBKEY", "ssh-ed25519 AAAAtest vbpub-controller-ephemeral")
    payload = {"customScript": "CONTROLLER_SSH_PUBKEY='{{CONTROLLER_SSH_PUBKEY}}' python3 -"}
    expanded = install_host_mod._expand_payload_placeholders(payload)
    assert expanded["customScript"] == "CONTROLLER_SSH_PUBKEY='ssh-ed25519 AAAAtest vbpub-controller-ephemeral' python3 -"


def test_expand_payload_placeholders_warns_when_controller_ssh_pubkey_missing(install_host_mod, monkeypatch, capsys):
    monkeypatch.delenv("CONTROLLER_SSH_PUBKEY", raising=False)
    payload = {"customScript": "CONTROLLER_SSH_PUBKEY='{{CONTROLLER_SSH_PUBKEY}}' python3 -"}
    install_host_mod._expand_payload_placeholders(payload)
    assert "CONTROLLER_SSH_PUBKEY not set" in capsys.readouterr().out


def test_normalize_ssh_public_key_drops_comment(install_host_mod):
    assert install_host_mod._normalize_ssh_public_key("ssh-ed25519 AAAA... user@host") == "ssh-ed25519 AAAA..."


def test_extract_primary_ipv4_from_ipv4_addresses(install_host_mod):
    assert install_host_mod._extract_primary_ipv4({"ipv4Addresses": [{"ip": "1.2.3.4"}]}) == "1.2.3.4"


def test_extract_primary_ipv4_from_server_live_info(install_host_mod):
    details = {"serverLiveInfo": {"interfaces": [{"ipv4Addresses": ["5.6.7.8"]}]}}
    assert install_host_mod._extract_primary_ipv4(details) == "5.6.7.8"


def test_extract_primary_ipv4_none_when_absent(install_host_mod):
    assert install_host_mod._extract_primary_ipv4({}) is None


def test_render_identity_file_path_substitutes_host_and_date(install_host_mod, monkeypatch):
    class _FixedDatetime(install_host_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 8)

    monkeypatch.setattr(install_host_mod, "datetime", _FixedDatetime)
    rendered = install_host_mod._render_identity_file_path(
        "~/.ssh/vbpub-netcup-{host}-{date}-ed25519", "v1001.vxxu.de"
    )
    assert rendered == "~/.ssh/vbpub-netcup-v1001.vxxu.de-20260908-ed25519"


def test_render_identity_file_path_falls_back_to_unknown_host(install_host_mod):
    rendered = install_host_mod._render_identity_file_path("~/.ssh/vbpub-netcup-{host}-{date}-ed25519", None)
    assert "unknown-host" in rendered
    assert "{host}" not in rendered


def test_render_identity_file_path_passthrough_when_no_placeholders(install_host_mod):
    literal = "~/.ssh/some-explicit-key"
    assert install_host_mod._render_identity_file_path(literal, "v1001.vxxu.de") == literal


def test_render_identity_file_path_sanitizes_unsafe_host_characters(install_host_mod):
    rendered = install_host_mod._render_identity_file_path("{host}", "v1001/../vxxu de")
    assert rendered == "v1001-..-vxxu-de"
    assert "/" not in rendered
    assert " " not in rendered


def test_refuse_unrendered_attach_only_identity_rejects_host_placeholder(install_host_mod):
    """Adversarial-review regression: --attach-only must never silently
    generate a fresh key at a literal '{host}'/'{date}' path -- that key
    would never match anything on the host being attached to, exactly the
    SSH-monitoring failure mode this redesign exists to close."""
    with pytest.raises(SystemExit, match="per-host/per-date TEMPLATE"):
        install_host_mod._refuse_unrendered_attach_only_identity(
            "~/.ssh/vbpub-netcup-{host}-{date}-ed25519"
        )


def test_refuse_unrendered_attach_only_identity_rejects_date_placeholder(install_host_mod):
    with pytest.raises(SystemExit, match="per-host/per-date TEMPLATE"):
        install_host_mod._refuse_unrendered_attach_only_identity("~/.ssh/key-{date}")


def test_refuse_unrendered_attach_only_identity_allows_resolved_path(install_host_mod):
    install_host_mod._refuse_unrendered_attach_only_identity(
        "~/.ssh/vbpub-netcup-v1001.vxxu.de-20260908-ed25519"
    )  # must not raise


def test_build_ssh_cmd_base_with_identity(install_host_mod):
    cmd = install_host_mod._build_ssh_cmd_base("1.2.3.4", "root", "/tmp/key")
    assert cmd[-1] == "root@1.2.3.4"
    assert "-i" in cmd and "/tmp/key" in cmd
    assert "IdentitiesOnly=yes" in cmd


def test_build_ssh_cmd_base_without_identity(install_host_mod):
    cmd = install_host_mod._build_ssh_cmd_base("1.2.3.4", "root")
    assert "-i" not in cmd


def test_parse_iso_ts_handles_z_suffix(install_host_mod):
    dt = install_host_mod._parse_iso_ts("2026-01-01T00:00:00Z")
    assert dt is not None and dt.year == 2026


def test_parse_iso_ts_none_for_invalid(install_host_mod):
    assert install_host_mod._parse_iso_ts("not-a-date") is None
    assert install_host_mod._parse_iso_ts(None) is None


def test_find_active_task_for_server_prefers_image_task(install_host_mod, fake_client):
    tasks = [
        {"uuid": "a", "state": "FINISHED", "name": "old"},
        {"uuid": "b", "state": "RUNNING", "name": "Some other task", "startedAt": "2026-01-01T00:00:00Z"},
        {"uuid": "c", "state": "RUNNING", "name": "Image install", "startedAt": "2026-01-02T00:00:00Z"},
    ]
    client = fake_client(get_responses=[tasks])
    result = install_host_mod._find_active_task_for_server(client, 1)
    assert result["uuid"] == "c"


def test_find_active_task_for_server_none_when_all_terminal(install_host_mod, fake_client):
    client = fake_client(get_responses=[[{"uuid": "a", "state": "FINISHED"}]])
    assert install_host_mod._find_active_task_for_server(client, 1) is None


# --- payload validation ---------------------------------------------------

def test_validate_payload_allows_missing_image_and_ssh_keys(install_host_mod):
    errors = install_host_mod._validate_installation_payload({"serverId": 1, "diskName": "vda"})
    assert errors == []


def test_validate_payload_rejects_bad_types(install_host_mod):
    errors = install_host_mod._validate_installation_payload(
        {"serverId": "not-an-int", "diskName": 5, "imageFlavourId": "x"}
    )
    joined = " ".join(errors)
    assert "serverId" in joined and "diskName" in joined and "imageFlavourId" in joined


def test_validate_payload_requires_server_or_hostname(install_host_mod):
    errors = install_host_mod._validate_installation_payload({"diskName": "vda"})
    assert any("serverId" in e or "hostname" in e for e in errors)


# --- settings loader (no defaults in Python; unused keys are errors too) --

def test_load_settings_happy_path(install_host_mod, tmp_path):
    toml_path = tmp_path / "s.toml"
    toml_path.write_text('[a]\nb = 1\nc = "x"\n')
    result = install_host_mod._load_settings(toml_path, {"a.b", "a.c"})
    assert result == {"a.b": 1, "a.c": "x"}


def test_load_settings_missing_key_errors(install_host_mod, tmp_path):
    toml_path = tmp_path / "s.toml"
    toml_path.write_text("[a]\nb = 1\n")
    with pytest.raises(SystemExit, match="missing required settings"):
        install_host_mod._load_settings(toml_path, {"a.b", "a.c"})


def test_load_settings_unknown_key_errors(install_host_mod, tmp_path):
    toml_path = tmp_path / "s.toml"
    toml_path.write_text("[a]\nb = 1\nextra = 2\n")
    with pytest.raises(SystemExit, match="unknown/unexpected settings"):
        install_host_mod._load_settings(toml_path, {"a.b"})


def test_real_settings_file_is_valid(install_host_mod):
    """The committed scp-api-install-host.toml must itself satisfy the schema."""
    assert install_host_mod.SETTINGS["ssh.user"] == "root"
    assert install_host_mod.SETTINGS["ssh.controller_fqdn"] == "automatic"


# --- local identity key auto-generation -----------------------------------

def test_ensure_local_identity_refuses_placeholder_fqdn(install_host_mod, tmp_path):
    key_path = tmp_path / "key"
    with pytest.raises(SystemExit, match="placeholder"):
        install_host_mod._ensure_local_identity_file_exists(str(key_path), "CHANGE-ME.example.invalid")
    assert not key_path.exists()


def test_ensure_local_identity_generates_key_when_missing(install_host_mod, tmp_path):
    key_path = tmp_path / "subdir" / "key"
    install_host_mod._ensure_local_identity_file_exists(str(key_path), "controller.example.com")
    assert key_path.exists()
    pub = key_path.with_suffix(".pub")
    assert pub.exists()
    comment = pub.read_text()
    assert "vbpub-controller-ephemeral" in comment
    assert "key@controller.example.com" in comment


def test_resolve_controller_fqdn_passes_through_explicit_value(install_host_mod):
    assert install_host_mod._resolve_controller_fqdn("controller.example.com") == "controller.example.com"


def test_resolve_controller_fqdn_automatic_prefers_reverse_dns_of_public_ip(install_host_mod, monkeypatch):
    monkeypatch.setattr(install_host_mod, "_detect_public_ip", lambda: "203.0.113.45")
    monkeypatch.setattr(install_host_mod, "_reverse_dns", lambda ip: "controller.example.net")
    assert install_host_mod._resolve_controller_fqdn("automatic") == "controller.example.net"


def test_resolve_controller_fqdn_automatic_falls_back_to_bare_ip_without_ptr(install_host_mod, monkeypatch):
    monkeypatch.setattr(install_host_mod, "_detect_public_ip", lambda: "203.0.113.45")
    monkeypatch.setattr(install_host_mod, "_reverse_dns", lambda ip: None)
    assert install_host_mod._resolve_controller_fqdn("automatic") == "203.0.113.45"


def test_resolve_controller_fqdn_automatic_falls_back_to_local_hostname_offline(install_host_mod, monkeypatch):
    monkeypatch.setattr(install_host_mod, "_detect_public_ip", lambda: None)
    monkeypatch.setattr(install_host_mod.socket, "getfqdn", lambda: "my-devcontainer")
    assert install_host_mod._resolve_controller_fqdn("automatic") == "my-devcontainer"


def test_ensure_local_identity_automatic_uses_reverse_dns(install_host_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(install_host_mod, "_detect_public_ip", lambda: "203.0.113.45")
    monkeypatch.setattr(install_host_mod, "_reverse_dns", lambda ip: "controller.example.net")
    key_path = tmp_path / "key"
    install_host_mod._ensure_local_identity_file_exists(str(key_path), "automatic")
    pub = key_path.with_suffix(".pub")
    comment = pub.read_text()
    assert "vbpub-controller-ephemeral" in comment
    assert "key@controller.example.net" in comment


def test_ensure_local_identity_automatic_refuses_when_everything_fails(install_host_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(install_host_mod, "_detect_public_ip", lambda: None)
    monkeypatch.setattr(install_host_mod.socket, "getfqdn", lambda: "")
    key_path = tmp_path / "key"
    with pytest.raises(SystemExit, match="placeholder"):
        install_host_mod._ensure_local_identity_file_exists(str(key_path), "automatic")
    assert not key_path.exists()


def test_detect_public_ip_returns_none_on_network_error(install_host_mod, monkeypatch):
    def fail(*a, **k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(install_host_mod.urllib.request, "urlopen", fail)
    assert install_host_mod._detect_public_ip() is None


def test_detect_public_ip_parses_plain_ip_body(install_host_mod, monkeypatch):
    monkeypatch.setattr(
        install_host_mod.urllib.request, "urlopen", lambda req, timeout=5.0: FakeHTTPResponse(b"203.0.113.45\n")
    )
    assert install_host_mod._detect_public_ip() == "203.0.113.45"


def test_reverse_dns_returns_none_when_lookup_fails(install_host_mod, monkeypatch):
    def fail(ip):
        raise OSError("no PTR record")

    monkeypatch.setattr(install_host_mod.socket, "gethostbyaddr", fail)
    assert install_host_mod._reverse_dns("203.0.113.45") is None


def test_ensure_local_identity_noop_when_exists(install_host_mod, tmp_path, monkeypatch):
    key_path = tmp_path / "key"
    key_path.write_text("existing")

    def fail(*a, **k):
        raise AssertionError("should not call ssh-keygen when the key already exists")

    monkeypatch.setattr(install_host_mod.subprocess, "run", fail)
    install_host_mod._ensure_local_identity_file_exists(str(key_path), "controller.example.com")


# --- interactive fill-in helpers ------------------------------------------

def test_resolve_image_flavour_uses_preselected(install_host_mod, fake_client):
    client = fake_client(allow=())
    result = install_host_mod._resolve_image_flavour(client, 1, {"id": 99, "name": "preset"}, interactive=False)
    assert result == {"id": 99, "name": "preset"}


def test_resolve_image_flavour_auto_selects_newest_noninteractive(install_host_mod, fake_client):
    flavours = [
        {"id": 1, "image": {"name": "Debian 12.0 UEFI amd64"}},
        {"id": 2, "image": {"name": "Debian 13.2 UEFI amd64"}},
        {"id": 3, "image": {"name": "Ubuntu 24.04 UEFI amd64"}},
    ]
    client = fake_client(get_responses=[flavours])
    result = install_host_mod._resolve_image_flavour(client, 1, None, interactive=False)
    assert result["id"] == 2  # newest Debian


def test_resolve_image_flavour_interactive_prompts(install_host_mod, fake_client, monkeypatch):
    flavours = [
        {"id": 1, "image": {"name": "Debian 12.0 UEFI amd64"}},
        {"id": 2, "image": {"name": "Debian 13.2 UEFI amd64"}},
    ]
    client = fake_client(get_responses=[flavours])
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")  # 2nd listed = older (sorted newest-first)
    result = install_host_mod._resolve_image_flavour(client, 1, None, interactive=True)
    assert result["id"] == 1


def test_resolve_ssh_key_ids_uses_preselected(install_host_mod, fake_client):
    client = fake_client(allow=())
    assert install_host_mod._resolve_ssh_key_ids(client, 1, [42], None, interactive=False) == [42]


def test_resolve_ssh_key_ids_auto_selects_first_noninteractive(install_host_mod, fake_client):
    keys = [{"id": 10, "name": "a"}, {"id": 20, "name": "b"}]
    client = fake_client(get_responses=[keys])
    assert install_host_mod._resolve_ssh_key_ids(client, 1, None, None, interactive=False) == [10]


def test_resolve_ssh_key_ids_none_when_no_keys(install_host_mod, fake_client):
    client = fake_client(get_responses=[[]])
    assert install_host_mod._resolve_ssh_key_ids(client, 1, None, None, interactive=False) is None


# --- HTTP client (mocked urllib) ------------------------------------------

def test_get_access_token_happy_path(install_host_mod, monkeypatch):
    body = json.dumps({"access_token": "tok123", "expires_in": 300}).encode()
    monkeypatch.setattr(install_host_mod.urllib.request, "urlopen", lambda req, timeout=30: FakeHTTPResponse(body))
    assert install_host_mod.get_access_token("refresh") == "tok123"


def test_client_get_retries_after_401(install_host_mod, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", None, io.BytesIO(b"{}"))
        return FakeHTTPResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(install_host_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(install_host_mod, "get_access_token", lambda rt: "new-token")
    client = install_host_mod.NetcupSCPClient("old-token", refresh_token="rt")
    result = client.get("/api/v1/tasks/x")
    assert result == {"ok": True}
    assert calls["n"] == 2


# --- install_from_payload: --dry-run must never mutate -------------------

def test_install_from_payload_dry_run_never_posts(install_host_mod, tmp_path, fake_client, capsys):
    payload = {
        "serverId": 12345,
        "hostname": "test.example.com",
        "imageFlavourId": 128,
        "diskName": "vda",
        "sshKeyIds": [1],
        "customScript": "echo hi",
    }
    payload_path = tmp_path / "target-host.jsonc"
    payload_path.write_text(json.dumps(payload))

    client = fake_client(allow=("get", "get_user_info"))  # post/patch NOT allowed
    args = types.SimpleNamespace(dry_run=True, yes=True, ssh_identity_file=None)

    install_host_mod.install_from_payload(client, str(payload_path), args)

    assert not any(c[0] == "post" for c in client.calls)
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()


def test_install_from_payload_rejects_invalid_payload(install_host_mod, tmp_path, fake_client):
    payload_path = tmp_path / "bad.jsonc"
    payload_path.write_text(json.dumps({"imageFlavourId": "not-an-int"}))
    client = fake_client(allow=())
    args = types.SimpleNamespace(dry_run=True, yes=True, ssh_identity_file=None)

    with pytest.raises(SystemExit):
        install_host_mod.install_from_payload(client, str(payload_path), args)


def test_install_from_payload_missing_file_exits_cleanly(install_host_mod, tmp_path, fake_client, capsys):
    """--payload pointing at a file that doesn't exist must fail with a clear
    message and sys.exit(1), never a raw traceback."""
    missing_path = tmp_path / "does-not-exist.jsonc"
    client = fake_client(allow=())
    args = types.SimpleNamespace(dry_run=True, yes=True, ssh_identity_file=None)

    with pytest.raises(SystemExit) as exc_info:
        install_host_mod.install_from_payload(client, str(missing_path), args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()
    assert str(missing_path) in err


# --- main() interactive-gather path: --dry-run must never touch disk -----
#
# Regression coverage for a real bug found 2026-09-08 live-testing against a
# real netcup host: step 8 (save_payload_with_comments, unconditional) ran
# BEFORE the dry-run early-return, so `--dry-run` (no --payload) silently
# overwrote an existing target-host.jsonc, and could even bake in the
# dry-run-only "-1" placeholder sshKeyId sentinel (from
# _ensure_netcup_ssh_key_id_for_identity's dry_run branch) as if it were a
# real, install-ready id. Fixed by moving the dry-run check before the save.

def _write_fake_identity(tmp_path):
    identity = tmp_path / "id_ed25519"
    identity.write_text("fake-private-key-material\n")
    identity.with_suffix(identity.suffix + ".pub").write_text(
        "ssh-ed25519 AAAAFAKEFAKEFAKE test@fake\n"
    )
    return identity


def _patch_main_for_interactive_dry_run(install_host_mod, monkeypatch, client, args):
    monkeypatch.setattr(install_host_mod, "SERVER_NAME", "test-server")
    monkeypatch.setattr(install_host_mod, "get_access_token", lambda rt: "fake-token")
    monkeypatch.setattr(install_host_mod, "NetcupSCPClient", lambda *a, **kw: client)
    monkeypatch.setattr(install_host_mod, "parse_args", lambda: args)
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "fake-refresh-token")


def _fake_gather_client(fake_client, *, user_id=7):
    servers = [{"id": 42}]
    server_details = {
        "serverLiveInfo": {"disks": [{"dev": "vda", "capacityInMiB": 524288}]},
        "hostname": "target.example",
    }
    flavours = [{"id": 2, "image": {"name": "Debian 13.2 UEFI amd64"}}]
    ssh_keys: list = []  # empty account: no match -> forces the dry-run "-1" sentinel path
    return fake_client(
        get_responses=[servers, server_details, flavours, ssh_keys, ssh_keys],
        user_info={"id": user_id},
        allow=("get", "get_user_info"),
    )


def test_main_interactive_dry_run_preserves_existing_target_host_jsonc(
    install_host_mod, tmp_path, fake_client, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    existing = tmp_path / "target-host.jsonc"
    existing_content = '{\n  "serverId": 1,\n  "hostname": "keep-me.example"\n}\n'
    existing.write_text(existing_content)

    identity_file = _write_fake_identity(tmp_path)
    client = _fake_gather_client(fake_client)
    args = types.SimpleNamespace(
        attach_only=False, payload=None, poweroff=False, dry_run=True, yes=True,
        ssh_identity_file=str(identity_file),
    )
    _patch_main_for_interactive_dry_run(install_host_mod, monkeypatch, client, args)

    install_host_mod.main()

    assert existing.read_text() == existing_content, "dry-run must not touch an existing target-host.jsonc"
    assert not any(c[0] == "post" for c in client.calls)
    out = capsys.readouterr().out
    assert "NOT saving to target-host.jsonc" in out


def test_main_interactive_dry_run_does_not_create_target_host_jsonc(
    install_host_mod, tmp_path, fake_client, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "target-host.jsonc").exists()

    identity_file = _write_fake_identity(tmp_path)
    client = _fake_gather_client(fake_client)
    args = types.SimpleNamespace(
        attach_only=False, payload=None, poweroff=False, dry_run=True, yes=True,
        ssh_identity_file=str(identity_file),
    )
    _patch_main_for_interactive_dry_run(install_host_mod, monkeypatch, client, args)

    install_host_mod.main()

    assert not (tmp_path / "target-host.jsonc").exists(), "dry-run must not create target-host.jsonc"
