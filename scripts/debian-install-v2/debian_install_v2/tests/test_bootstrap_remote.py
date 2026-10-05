"""bootstrap-remote.py — the stdlib-only remote-install wrapper.

Filename has a hyphen (matches the sibling debian-install-v2.py entrypoint
convention), so it's loaded by path via importlib, same as
test_inuse_partition_editor.py does for inuse_partition_editor.py.
"""
from __future__ import annotations

import importlib.util
import io
import json
import shlex
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "bootstrap-remote.py"


def load_module():
    spec = importlib.util.spec_from_file_location("bootstrap_remote", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return load_module()


def test_manual_bootstrap_recipe_uses_native_python_fetch_without_pipeline(mod):
    assert "urllib.request.urlopen(url, timeout=60)" in mod.__doc__
    assert "| python3" not in mod.__doc__
    assert "bootstrap download failed" in mod.__doc__
    recipe = mod.__doc__.split("Usage (root):\n", 1)[1].split("\n\nEnv vars", 1)[0]
    tokens = shlex.split(recipe.replace("\\\n", " "))
    source = tokens[tokens.index("python3") + 2]
    compile(source, "<bootstrap usage recipe>", "exec")


# --- env -> config translation -----------------------------------------

def test_build_config_maps_named_env_vars(mod, monkeypatch):
    monkeypatch.setenv("SWAP_DISK_TOTAL_GB", "64")
    monkeypatch.setenv("SWAP_FILE_COUNT", "4")
    monkeypatch.setenv("ZSWAP_COMPRESSOR", "zstd")
    monkeypatch.setenv("VM_SWAPPINESS", "50")
    monkeypatch.setenv("NEVER_REBOOT", "no")
    monkeypatch.setenv("AUTO_REBOOT_AFTER_STAGE1", "yes")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    config = mod.build_config()
    assert config == {
        "swap_disk_total_gb": 64,
        "swap_file_count": 4,
        "zswap_compressor": "zstd",
        "vm_swappiness": 50,
        "never_reboot": False,
        "auto_reboot_after_stage1": True,
        "telegram_bot_token": "123:token",
        "telegram_chat_id": "456",
    }


def test_build_config_maps_controller_ssh_pubkey(mod, monkeypatch):
    monkeypatch.setenv("CONTROLLER_SSH_PUBKEY", "ssh-ed25519 AAAAtest vbpub-controller-ephemeral")
    config = mod.build_config()
    assert config == {"controller_ssh_pubkey": "ssh-ed25519 AAAAtest vbpub-controller-ephemeral"}


def test_build_config_empty_when_nothing_set(mod, monkeypatch):
    for name in list(mod._STRING_FIELDS) + list(mod._INT_FIELDS) + list(mod._BOOL_FIELDS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("VBPUB_CONFIG_EXTRA_JSON", raising=False)
    assert mod.build_config() == {}


def test_auto_tristate_is_rejected_not_silently_guessed(mod, monkeypatch):
    monkeypatch.setenv("AUTO_REBOOT_AFTER_STAGE1", "auto")
    with pytest.raises(SystemExit, match="not yes/no"):
        mod.build_config()


@pytest.mark.parametrize("value", ["maybe", "y", "sometimes"])
def test_bool_env_rejects_unrecognized_values(mod, monkeypatch, value):
    monkeypatch.setenv("NEVER_REBOOT", value)
    with pytest.raises(SystemExit, match="not yes/no"):
        mod.build_config()


def test_int_env_rejects_non_numeric(mod, monkeypatch):
    monkeypatch.setenv("SWAP_FILE_COUNT", "eight")
    with pytest.raises(SystemExit, match="not an integer"):
        mod.build_config()


def test_extra_json_wins_over_named_vars(mod, monkeypatch):
    monkeypatch.setenv("SWAP_FILE_COUNT", "8")
    monkeypatch.setenv("VBPUB_CONFIG_EXTRA_JSON", json.dumps({"swap_file_count": 16, "credential_mode": "systemd"}))
    config = mod.build_config()
    assert config["swap_file_count"] == 16
    assert config["credential_mode"] == "systemd"


def test_extra_json_must_be_an_object(mod, monkeypatch):
    monkeypatch.setenv("VBPUB_CONFIG_EXTRA_JSON", "[1,2,3]")
    with pytest.raises(SystemExit, match="must be a JSON object"):
        mod.build_config()


@pytest.mark.parametrize("name", ["SWAP_ARCH", "SWAP_TOTAL_GB", "SWAP_FILES", "USE_PARTITION"])
def test_build_config_rejects_v1_obsolete_env_var_names(mod, monkeypatch, name):
    # config.py's own OBSOLETE_VARIABLES check only inspects the JSON config
    # FILE's keys -- it never sees an env var that build_config() simply
    # never read. Setting the literal v1 name (the likely mistake) must be
    # rejected here, not silently ignored in favor of v2's default.
    monkeypatch.setenv(name, "64")
    with pytest.raises(SystemExit, match=name):
        mod.build_config()


# --- fetch_subtree -------------------------------------------------------

def _fake_tarball(files: dict[str, bytes], *, executable: set[str] = frozenset()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name=f"vbpub-main/{name}")
            info.size = len(content)
            info.mode = 0o755 if name in executable else 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def test_fetch_subtree_extracts_only_the_matching_subtree(mod, monkeypatch, tmp_path):
    tarball = _fake_tarball({
        "scripts/debian-install-v2/debian-install-v2.py": b"#!/usr/bin/env python3\n",
        "scripts/debian-install-v2/debian_install_v2/installer.py": b"# installer\n",
        "scripts/debian-install-v2/VERSION": b"2.0.0\n",
        # The library is no longer extracted from the source tree: it arrives
        # as a released wheel (install_wheel), never as a source subtree.
        "libraries/cli-extended/src/cli_extended/__init__.py": b"# shared CLI\n",
        "libraries/cli-extended/README.md": b"not runtime\n",
        "scripts/other-tool/README.md": b"unrelated\n",
        "README.md": b"repo root readme\n",
    }, executable={"scripts/debian-install-v2/debian-install-v2.py"})

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(tarball))

    install_dir = tmp_path / "install"
    mod.fetch_subtree("https://github.com/volkb79-2/vbpub", "main", install_dir, debug=False)

    assert (install_dir / "debian-install-v2.py").read_bytes() == b"#!/usr/bin/env python3\n"
    assert (install_dir / "debian_install_v2" / "installer.py").is_file()
    assert (install_dir / "VERSION").read_bytes() == b"2.0.0\n"
    assert not (install_dir / "cli_extended").exists()
    assert not (install_dir / "other-tool").exists()
    assert not (install_dir / "README.md").exists()
    assert sorted(path.name for path in install_dir.iterdir()) == [
        "VERSION", "debian-install-v2.py", "debian_install_v2",
    ]
    mode = (install_dir / "debian-install-v2.py").stat().st_mode
    assert mode & 0o111


def test_fetch_subtree_raises_when_nothing_matches(mod, monkeypatch, tmp_path):
    tarball = _fake_tarball({"README.md": b"nothing relevant here\n"})
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(tarball))
    with pytest.raises(SystemExit, match="required source tree"):
        mod.fetch_subtree("https://github.com/volkb79-2/vbpub", "main", tmp_path / "install", debug=False)


def test_fetch_subtree_does_not_require_the_library_source_tree(mod, monkeypatch, tmp_path):
    tarball = _fake_tarball({
        "scripts/debian-install-v2/debian-install-v2.py": b"#!/usr/bin/env python3\n",
    })
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(tarball))
    mod.fetch_subtree("https://github.com/volkb79-2/vbpub", "main", tmp_path / "install", debug=False)
    assert (tmp_path / "install" / "debian-install-v2.py").is_file()


def test_fetch_subtree_refuses_tar_slip_path_traversal(mod, monkeypatch, tmp_path):
    # A member name embedding ".." after the matched subtree prefix would
    # otherwise resolve outside install_dir the moment target.write_bytes()
    # touches the real filesystem -- this process runs as root (adversarial
    # review finding).
    tarball = _fake_tarball({"scripts/debian-install-v2/../../../etc/cron.d/evil": b"* * * * * root pwned\n"})
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(tarball))
    install_dir = tmp_path / "install"
    with pytest.raises(SystemExit, match="unsafe path"):
        mod.fetch_subtree("https://github.com/volkb79-2/vbpub", "main", install_dir, debug=False)
    assert not (tmp_path / "etc").exists()
    assert not Path("/etc/cron.d/evil").exists()


def test_fetch_subtree_reports_a_truncated_download_cleanly(mod, monkeypatch, tmp_path):
    class _TruncatedResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    # Valid gzip header, then nothing -- tarfile raises ReadError, not
    # URLError, when the stream is corrupt/truncated mid-download.
    import gzip
    truncated = gzip.compress(b"scripts/debian-install-v2/x")[:8]
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _TruncatedResponse(truncated))
    with pytest.raises(SystemExit, match="corrupt or truncated download"):
        mod.fetch_subtree("https://github.com/volkb79-2/vbpub", "main", tmp_path / "install", debug=False)


def test_remote_invocation_dispatches_install_verb_with_explicit_acceptance(
    mod, monkeypatch, tmp_path
):
    install_dir = tmp_path / "install"
    monkeypatch.setenv("INSTALL_DIR", str(install_dir))
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("DEBUG_MODE", raising=False)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)

    def fake_fetch(repo_url, branch, target, *, debug):
        events.append("fetch")
        target.mkdir(parents=True)
        (target / "debian-install-v2.py").write_text("# entrypoint\n", encoding="utf-8")

    events = []
    monkeypatch.setattr(
        mod, "resolve_wheel",
        lambda *, debug: events.append("resolve") or ("https://example.test/cli_extended-9.9.9-py3-none-any.whl", "0" * 64),
    )
    monkeypatch.setattr(
        mod, "download_wheel",
        lambda url, sha256, *, debug: events.append(("download", url, sha256)) or ("cli_extended-9.9.9-py3-none-any.whl", b"data"),
    )
    monkeypatch.setattr(
        mod, "write_wheel",
        lambda name, data, target, *, debug: events.append(("write", name, data, target)),
    )
    captured = {}

    def fake_run(argv, *, check):
        assert check is False
        captured["argv"] = argv
        captured["config"] = json.loads(Path(argv[argv.index("--config") + 1]).read_text())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod, "fetch_subtree", fake_fetch)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert mod.main() == 0
    assert events == [
        "resolve",
        ("download", "https://example.test/cli_extended-9.9.9-py3-none-any.whl", "0" * 64),
        "fetch",
        ("write", "cli_extended-9.9.9-py3-none-any.whl", b"data", install_dir),
    ]
    assert captured["argv"][2:4] == ["install", "--config"]
    assert captured["argv"][-1] == "--yes"
    assert captured["config"] == {}
    assert not (install_dir / "remote-install-config.json").exists()


# --- released cli-extended wheel (CX-D3) ----------------------------------

import hashlib
import urllib.error
import zipfile

WHEEL_NAME = "cli_extended-0.2.0-py3-none-any.whl"
WHEEL_URL = f"https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.2.0/{WHEEL_NAME}"
USER_AGENT = "vbpub-bootstrap-remote"


def _wheel_bytes(members: dict[str, bytes] | None = None) -> bytes:
    members = {"cli_extended/__init__.py": b"# library\n"} if members is None else members
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _FakeNet:
    """A fake urllib.request.urlopen: URL -> bytes, or an exception to raise."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float, str]] = []

    def __call__(self, request, timeout=None):
        self.calls.append((request.full_url, timeout, request.get_header("User-agent")))
        outcome = self.responses[request.full_url]
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(outcome)


@pytest.fixture()
def net(mod, monkeypatch):
    for name in ("CLI_EXTENDED_WHEEL_URL", "CLI_EXTENDED_WHEEL_SHA256", "CLI_EXTENDED_LATEST_URL"):
        monkeypatch.delenv(name, raising=False)

    def install(responses):
        fake = _FakeNet(responses)
        monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
        return fake

    return install


def _manifest(**fields) -> bytes:
    payload = {"project": "cli-extended", "version": "0.2.0", "tag": "cli-extended-v0.2.0",
               "asset": WHEEL_NAME, "sha256": "a" * 64, "url": WHEEL_URL, "note": "n"}
    payload.update(fields)
    return json.dumps(payload).encode()


def test_pinned_env_pair_is_used_without_any_network_call(mod, monkeypatch, net):
    fake = net({})
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_URL", WHEEL_URL)
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_SHA256", "ABCDEF" + "0" * 58)
    assert mod.resolve_wheel() == (WHEEL_URL, "abcdef" + "0" * 58)
    assert fake.calls == []


@pytest.mark.parametrize("present", ["CLI_EXTENDED_WHEEL_URL", "CLI_EXTENDED_WHEEL_SHA256"])
def test_half_pinned_wheel_is_an_error_before_any_network_call(mod, monkeypatch, net, present):
    fake = net({})
    monkeypatch.setenv(present, "x")
    with pytest.raises(SystemExit) as raised:
        mod.resolve_wheel()
    assert str(raised.value) == (
        "bootstrap-remote: CLI_EXTENDED_WHEEL_URL and CLI_EXTENDED_WHEEL_SHA256 must be set "
        "together (or both unset to use the latest release)"
    )
    assert fake.calls == []


def test_latest_pointer_default_path_reads_url_and_sha256(mod, net):
    fake = net({mod.LATEST_URL_DEFAULT: _manifest(sha256="B" * 64)})
    assert mod.resolve_wheel() == (WHEEL_URL, "b" * 64)
    assert fake.calls == [(
        "https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json",
        60,
        USER_AGENT,
    )]


def test_latest_pointer_url_is_overridable_by_env(mod, monkeypatch, net):
    fake = net({"https://mirror.example.test/latest.json": _manifest()})
    monkeypatch.setenv("CLI_EXTENDED_LATEST_URL", "https://mirror.example.test/latest.json")
    assert mod.resolve_wheel() == (WHEEL_URL, "a" * 64)
    assert [call[0] for call in fake.calls] == ["https://mirror.example.test/latest.json"]


@pytest.mark.parametrize("field", ["url", "sha256"])
@pytest.mark.parametrize("value", [None, "", 7])
def test_latest_pointer_missing_or_empty_field_names_the_field(mod, net, field, value):
    manifest = json.loads(_manifest())
    if value is None:
        del manifest[field]
    else:
        manifest[field] = value
    net({mod.LATEST_URL_DEFAULT: json.dumps(manifest).encode()})
    with pytest.raises(SystemExit) as raised:
        mod.resolve_wheel()
    assert str(raised.value) == (
        f"bootstrap-remote: release manifest {mod.LATEST_URL_DEFAULT} has no usable {field!r} field"
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [(b"not json", "is not valid JSON"), (b'["url"]', "must be a JSON object")],
)
def test_latest_pointer_must_be_a_json_object(mod, net, body, message):
    net({mod.LATEST_URL_DEFAULT: body})
    with pytest.raises(SystemExit, match=message):
        mod.resolve_wheel()


def test_latest_pointer_fetch_failure_is_a_bootstrap_error(mod, net):
    net({mod.LATEST_URL_DEFAULT: urllib.error.URLError("no route")})
    with pytest.raises(SystemExit, match="could not fetch the cli-extended release manifest .*no route"):
        mod.resolve_wheel()


@pytest.mark.parametrize("bad", ["abc", "g" * 64, "a" * 63, "a" * 65])
def test_sha256_must_be_64_hex_digits(mod, monkeypatch, net, bad):
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_URL", WHEEL_URL)
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_SHA256", bad)
    with pytest.raises(SystemExit, match="is not 64 hexadecimal digits"):
        mod.resolve_wheel()


def test_install_wheel_writes_the_verified_wheel_under_its_release_name(mod, net, tmp_path):
    data = _wheel_bytes()
    fake = net({WHEEL_URL + "?download=1": data})
    install_dir = tmp_path / "install"
    written = mod.install_wheel(WHEEL_URL + "?download=1", _sha(data), install_dir, debug=False)
    assert written == install_dir / WHEEL_NAME
    assert written.read_bytes() == data
    assert [path.name for path in install_dir.iterdir()] == [WHEEL_NAME]
    assert fake.calls == [(WHEEL_URL + "?download=1", 60, USER_AGENT)]


def test_install_wheel_sha256_mismatch_reports_both_digests_and_writes_nothing(mod, net, tmp_path):
    data = _wheel_bytes()
    net({WHEEL_URL: data})
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    stale = install_dir / "cli_extended-0.1.0-py3-none-any.whl"
    stale.write_bytes(b"old")
    with pytest.raises(SystemExit) as raised:
        mod.install_wheel(WHEEL_URL, "0" * 64, install_dir, debug=False)
    assert str(raised.value) == (
        f"bootstrap-remote: cli-extended wheel sha256 mismatch for {WHEEL_URL}: "
        f"expected {'0' * 64}, got {_sha(data)}"
    )
    assert sorted(path.name for path in install_dir.iterdir()) == [stale.name]
    assert stale.read_bytes() == b"old"


def test_install_wheel_mismatch_does_not_even_create_the_install_dir(mod, net, tmp_path):
    net({WHEEL_URL: _wheel_bytes()})
    install_dir = tmp_path / "install"
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        mod.install_wheel(WHEEL_URL, "0" * 64, install_dir, debug=False)
    assert not install_dir.exists()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"this is not a zip archive", "is not a zip archive"),
        (_wheel_bytes({"cli_extended/other.py": b"x"}), "does not contain cli_extended/__init__.py"),
        (_wheel_bytes({"vendored/cli_extended/__init__.py": b"x"}), "does not contain cli_extended/__init__.py"),
    ],
)
def test_install_wheel_rejects_a_payload_that_is_not_the_library(mod, net, tmp_path, data, message):
    net({WHEEL_URL: data})
    install_dir = tmp_path / "install"
    with pytest.raises(SystemExit, match=message):
        mod.install_wheel(WHEEL_URL, _sha(data), install_dir, debug=False)
    assert not install_dir.exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/cli_extended-0.2.0.tar.gz",
        "https://example.test/cli_extended-0.2.0.whl/download",
        "https://example.test/other-0.2.0-py3-none-any.whl",
        "https://example.test/cli_extended-0.2.0-py3-none-any.whl.zip",
        "https://example.test/cli_extended-1%2F..%2Fevil.whl",
        "https://example.test/",
    ],
)
def test_install_wheel_refuses_a_url_that_is_not_a_cli_extended_wheel_name(mod, net, tmp_path, url):
    fake = net({})
    with pytest.raises(SystemExit, match="must end in a cli_extended-\\*.whl filename"):
        mod.install_wheel(url, "0" * 64, tmp_path / "install", debug=False)
    assert fake.calls == []
    assert not (tmp_path / "install").exists()


def test_install_wheel_download_failure_is_a_bootstrap_error(mod, net, tmp_path):
    net({WHEEL_URL: urllib.error.HTTPError(WHEEL_URL, 404, "Not Found", {}, None)})
    with pytest.raises(SystemExit, match="could not fetch the cli-extended wheel .*404"):
        mod.install_wheel(WHEEL_URL, "0" * 64, tmp_path / "install", debug=False)


def test_install_wheel_leaves_only_one_cli_extended_wheel(mod, net, tmp_path):
    data = _wheel_bytes()
    net({WHEEL_URL: data})
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "cli_extended-0.1.0-py3-none-any.whl").write_bytes(b"older release")
    (install_dir / "cli_extended-0.1.5-py3-none-any.whl").write_bytes(b"another")
    (install_dir / WHEEL_NAME).write_bytes(b"same name, stale bytes")
    (install_dir / "other_tool-1.0-py3-none-any.whl").write_bytes(b"unrelated wheel")
    (install_dir / "cli_extended.txt").write_bytes(b"unrelated file")
    mod.install_wheel(WHEEL_URL, _sha(data), install_dir, debug=False)
    assert sorted(path.name for path in install_dir.iterdir()) == [
        WHEEL_NAME, "cli_extended.txt", "other_tool-1.0-py3-none-any.whl",
    ]
    assert (install_dir / WHEEL_NAME).read_bytes() == data


def test_debug_mode_narrates_the_wheel_steps_on_stderr(mod, net, tmp_path, capsys):
    data = _wheel_bytes()
    net({mod.LATEST_URL_DEFAULT: _manifest(sha256=_sha(data)), WHEEL_URL: data})
    url, sha256 = mod.resolve_wheel(debug=True)
    mod.install_wheel(url, sha256, tmp_path / "install", debug=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"[bootstrap-remote] reading release manifest {mod.LATEST_URL_DEFAULT}\n" in captured.err
    assert f"[bootstrap-remote] downloading {WHEEL_URL}\n" in captured.err
    assert f"[bootstrap-remote] wrote {tmp_path / 'install' / WHEEL_NAME}\n" in captured.err


def test_main_fetches_tree_and_released_wheel_end_to_end_with_faked_urllib(
    mod, monkeypatch, net, tmp_path
):
    data = _wheel_bytes()
    tarball = _fake_tarball({"scripts/debian-install-v2/debian-install-v2.py": b"# entrypoint\n"})
    fake = net({
        "https://github.com/volkb79-2/vbpub/archive/refs/heads/main.tar.gz": tarball,
        mod.LATEST_URL_DEFAULT: _manifest(sha256=_sha(data)),
        WHEEL_URL: data,
    })
    install_dir = tmp_path / "install"
    monkeypatch.setenv("INSTALL_DIR", str(install_dir))
    for name in ("DRY_RUN", "DEBUG_MODE", "REPO_URL", "REPO_BRANCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(mod.subprocess, "run", lambda argv, *, check: SimpleNamespace(returncode=7))

    assert mod.main() == 7
    assert [call[0] for call in fake.calls] == [
        mod.LATEST_URL_DEFAULT,
        WHEEL_URL,
        "https://github.com/volkb79-2/vbpub/archive/refs/heads/main.tar.gz",
    ]
    assert sorted(path.name for path in install_dir.iterdir()) == [WHEEL_NAME, "debian-install-v2.py"]


def test_main_with_a_bad_wheel_digest_writes_nothing_to_the_install_dir(
    mod, monkeypatch, net, tmp_path
):
    data = _wheel_bytes()
    tarball = _fake_tarball({"scripts/debian-install-v2/debian-install-v2.py": b"# entrypoint\n"})
    fake = net({
        "https://github.com/volkb79-2/vbpub/archive/refs/heads/main.tar.gz": tarball,
        mod.LATEST_URL_DEFAULT: _manifest(sha256="0" * 64),
        WHEEL_URL: data,
    })
    install_dir = tmp_path / "install"
    monkeypatch.setenv("INSTALL_DIR", str(install_dir))
    for name in ("DRY_RUN", "DEBUG_MODE", "REPO_URL", "REPO_BRANCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        mod.main()
    assert [call[0] for call in fake.calls] == [mod.LATEST_URL_DEFAULT, WHEEL_URL]
    assert not install_dir.exists()


# --- https only ------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["http", "file", "ftp", "HTTP", ""])
def test_pinned_wheel_url_must_be_https(mod, monkeypatch, net, scheme):
    fake = net({})
    url = f"{scheme}://example.test/{WHEEL_NAME}" if scheme else f"/tmp/{WHEEL_NAME}"
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_URL", url)
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_SHA256", "a" * 64)
    with pytest.raises(SystemExit) as raised:
        mod.resolve_wheel()
    assert str(raised.value) == f"bootstrap-remote: CLI_EXTENDED_WHEEL_URL {url!r} must be an https:// URL"
    assert fake.calls == []


@pytest.mark.parametrize("scheme", ["http", "file"])
def test_latest_pointer_url_must_be_https(mod, monkeypatch, net, scheme):
    fake = net({})
    url = f"{scheme}://example.test/latest.json"
    monkeypatch.setenv("CLI_EXTENDED_LATEST_URL", url)
    with pytest.raises(SystemExit, match="CLI_EXTENDED_LATEST_URL .* must be an https:// URL"):
        mod.resolve_wheel()
    assert fake.calls == []


@pytest.mark.parametrize("scheme", ["http", "file"])
def test_manifest_url_field_must_be_https(mod, net, scheme):
    fake = net({mod.LATEST_URL_DEFAULT: _manifest(url=f"{scheme}://example.test/{WHEEL_NAME}")})
    with pytest.raises(SystemExit, match="the 'url' field of release manifest .* must be an https:// URL"):
        mod.resolve_wheel()
    assert [call[0] for call in fake.calls] == [mod.LATEST_URL_DEFAULT]


def test_an_uppercase_https_scheme_is_accepted_for_the_pointer_url(mod, monkeypatch, net):
    # urlparse normalizes the scheme to lowercase, so no explicit .lower() is needed.
    url = "HTTPS://mirror.example.test/latest.json"
    fake = net({url: _manifest()})
    monkeypatch.setenv("CLI_EXTENDED_LATEST_URL", url)
    assert mod.resolve_wheel() == (WHEEL_URL, "a" * 64)
    assert [call[0] for call in fake.calls] == [url]


def test_https_scheme_check_accepts_an_https_pin(mod, monkeypatch, net):
    net({})
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_URL", WHEEL_URL)
    monkeypatch.setenv("CLI_EXTENDED_WHEEL_SHA256", "a" * 64)
    assert mod.resolve_wheel() == (WHEEL_URL, "a" * 64)


# --- size caps and truncated reads ------------------------------------------


def test_wheel_at_the_cap_is_accepted_and_one_byte_more_is_refused(mod, net, monkeypatch, tmp_path):
    wheel = _wheel_bytes()
    net({WHEEL_URL: wheel})
    monkeypatch.setattr(mod, "WHEEL_MAX_BYTES", len(wheel))
    assert mod.download_wheel(WHEEL_URL, _sha(wheel), debug=False) == (WHEEL_NAME, wheel)
    monkeypatch.setattr(mod, "WHEEL_MAX_BYTES", len(wheel) - 1)
    with pytest.raises(SystemExit) as raised:
        mod.download_wheel(WHEEL_URL, _sha(wheel), debug=False)
    assert str(raised.value) == (
        f"bootstrap-remote: the cli-extended wheel {WHEEL_URL} is larger than the "
        f"{len(wheel) - 1}-byte cap"
    )


def test_manifest_over_the_cap_is_refused(mod, net, monkeypatch):
    assert mod.WHEEL_MAX_BYTES == 16 * 1024 * 1024
    assert mod.MANIFEST_MAX_BYTES == 1024 * 1024
    monkeypatch.setattr(mod, "MANIFEST_MAX_BYTES", 100)
    body = _manifest()
    assert len(body) > 100
    net({mod.LATEST_URL_DEFAULT: body})
    with pytest.raises(SystemExit) as raised:
        mod.resolve_wheel()
    assert str(raised.value) == (
        f"bootstrap-remote: the cli-extended release manifest {mod.LATEST_URL_DEFAULT} "
        "is larger than the 100-byte cap"
    )


def test_download_reads_at_most_cap_plus_one_byte(mod, monkeypatch):
    seen = []

    class _Recorder(io.BytesIO):
        def read(self, size=-1):
            seen.append(size)
            return super().read(size)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _Recorder(b"abc"))
    assert mod._download("https://example.test/x", "thing", 10) == b"abc"
    assert seen == [11]


def test_incomplete_read_is_a_bootstrap_error(mod, net):
    import http.client

    net({WHEEL_URL: http.client.IncompleteRead(b"par", 100)})
    with pytest.raises(SystemExit, match="could not fetch the cli-extended wheel .*IncompleteRead"):
        mod.download_wheel(WHEEL_URL, "0" * 64, debug=False)


def test_a_truncated_body_raised_during_read_is_a_bootstrap_error(mod, monkeypatch):
    import http.client

    class _Broken:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self, size=-1):
            raise http.client.IncompleteRead(b"par", 100)

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _Broken())
    with pytest.raises(SystemExit, match="could not fetch thing https://example.test/x"):
        mod._download("https://example.test/x", "thing", 10)


# --- ordering, atomicity, directories ---------------------------------------


def test_wheel_is_replaced_atomically_before_other_wheels_are_deleted(mod, monkeypatch, tmp_path):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    older = install_dir / "cli_extended-0.1.0-py3-none-any.whl"
    older.write_bytes(b"older")
    replaced = []

    def failing_replace(source, destination):
        replaced.append((Path(source).name, Path(destination).name))
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        mod.write_wheel(WHEEL_NAME, b"new", install_dir, debug=False)
    assert replaced == [(WHEEL_NAME + ".tmp", WHEEL_NAME)]
    assert older.read_bytes() == b"older"


def test_write_wheel_goes_through_a_temp_sibling_and_keeps_only_the_new_wheel(mod, tmp_path):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "cli_extended-0.1.0-py3-none-any.whl").write_bytes(b"older")
    target = mod.write_wheel(WHEEL_NAME, b"new", install_dir, debug=False)
    assert target == install_dir / WHEEL_NAME
    assert target.read_bytes() == b"new"
    assert [path.name for path in install_dir.iterdir()] == [WHEEL_NAME]


def test_a_directory_matching_the_wheel_glob_is_refused_and_nothing_changes(mod, tmp_path):
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    older = install_dir / "cli_extended-0.1.0-py3-none-any.whl"
    older.write_bytes(b"older")
    (install_dir / "cli_extended-0.0.1-py3-none-any.whl").mkdir()
    with pytest.raises(SystemExit) as raised:
        mod.write_wheel(WHEEL_NAME, b"new", install_dir, debug=False)
    assert str(raised.value) == (
        "bootstrap-remote: refusing to replace cli-extended wheel(s) that are directories: "
        f"{install_dir / 'cli_extended-0.0.1-py3-none-any.whl'}"
    )
    assert older.read_bytes() == b"older"
    assert not (install_dir / WHEEL_NAME).exists()
    assert not (install_dir / (WHEEL_NAME + ".tmp")).exists()


def test_a_directory_with_the_new_wheels_own_name_is_refused(mod, tmp_path):
    install_dir = tmp_path / "install"
    (install_dir / WHEEL_NAME).mkdir(parents=True)
    with pytest.raises(SystemExit, match="that are directories"):
        mod.write_wheel(WHEEL_NAME, b"new", install_dir, debug=False)
