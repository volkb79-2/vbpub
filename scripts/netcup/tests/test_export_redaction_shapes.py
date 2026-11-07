"""Shape tests for evidence-export redaction and its fail-closed second pass.

Every secret shape the fix-5 review measured as leaking, plus the shapes the
operator listed, must (a) be redacted by the first pass, (b) be caught by the
independent second pass when it is NOT redacted, and (c) stay redacted when the
controller re-runs the redaction (idempotence).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

NETCUP_DIR = Path(__file__).resolve().parents[1]
SECRET = "S1leak"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, NETCUP_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


redaction = _load("netcup_export_redaction_under_test", "export_redaction.py")

SHAPES = {
    # systemd units (/etc/systemd/system is allowlisted)
    "unit-env-unquoted": f"[Service]\nEnvironment=GITHUB_TOKEN={SECRET}\n",
    "unit-env-double-quoted": f'[Service]\nEnvironment="API_KEY={SECRET}"\n',
    "unit-env-single-quoted": f"[Service]\nEnvironment='DB_PASSWORD={SECRET}'\n",
    "unit-env-second-of-two": f'[Service]\nEnvironment="A=1" "SECRET={SECRET}"\n',
    "unit-env-after-plain": f"[Service]\nEnvironment=PLAIN=1 MM_TOKEN={SECRET}\n",
    "unit-env-camel": f"[Service]\nEnvironment=clientSecret={SECRET}\n",
    # YAML structure
    "yaml-nested-password": f"db:\n  password: {SECRET}\n",
    "yaml-nested-token": f"mattermost:\n  token: {SECRET}\n",
    "yaml-nested-secret": f"auth:\n  secret: {SECRET}\n",
    "yaml-list-passwd": f"users:\n  - name: x\n    passwd: {SECRET}\n",
    "yaml-block-scalar": f"password: |\n  {SECRET}\n  {SECRET}-line2\nnext: 1\n",
    "yaml-folded-scalar": f"token: >-\n  {SECRET}\n",
    "yaml-secret-parent-children": f"credentials:\n  user: x\n  value: {SECRET}\n",
    # chpasswd
    "chpasswd-yaml": f"chpasswd:\n  list: |\n    root:{SECRET}\n  expire: false\n",
    "chpasswd-pipe": f'echo "root:{SECRET}" | chpasswd\n',
    "chpasswd-herestring": f"chpasswd <<< 'root:{SECRET}'\n",
    "chpasswd-heredoc": f"chpasswd <<EOF\nroot:{SECRET}\nadmin:{SECRET}2\nEOF\necho after\n",
    # URL userinfo, any scheme
    "url-postgresql": f"DATABASE_URL=postgresql://user:{SECRET}@db:5432/x\n",
    "url-redis-empty-user": f"cache=redis://:{SECRET}@cache:6379/0\n",
    "url-amqp": f"amqp://guest:{SECRET}@mq/\n",
    "url-mysql": f"mysql://root:{SECRET}@127.0.0.1/db\n",
    "url-custom-scheme": f"x-custom+v2://u:{SECRET}@h\n",
    "url-password-with-at": f"postgresql://u:{SECRET}@{SECRET}2@h/db\n",
    # command-line options
    "flag-p-space": f"-p {SECRET}\n",
    "flag-p-attached": f"mysql -u root -p{SECRET}\n",
    "flag-sshpass": f"sshpass -p {SECRET} ssh x\n",
    "flag-password-space": f"cmd --password {SECRET} --other\n",
    "flag-password-equals": f"cmd --password={SECRET}\n",
    "flag-password-quoted-space": f'cmd --password "{SECRET} with space"\n',
    "flag-db-password": f"cmd --db-password={SECRET}\n",
    "flag-client-secret": f"cmd --client-secret {SECRET}\n",
    "flag-token-space": f"--token {SECRET}\n",
    "flag-passphrase": f"cmd --passphrase {SECRET}\n",
    # camelCase keys, JSON and plain text
    "camel-json-accessToken": f'{{"accessToken": "{SECRET}"}}\n',
    "camel-json-apiToken": f'{{"apiToken": "{SECRET}"}}\n',
    "camel-json-clientSecret": f'{{"clientSecret": "{SECRET}"}}\n',
    "camel-json-dbPassword": f'{{"dbPassword": "{SECRET}"}}\n',
    "camel-text-dbPassword": f"dbPassword: {SECRET}\n",
    "camel-text-clientSecret": f"clientSecret = {SECRET}\n",
    "key-passphrase-lower": f"passphrase: {SECRET}\n",
    "key-passphrase-upper": f"PASSPHRASE={SECRET}\n",
    "key-dbpass": f"DBPASS={SECRET}\n",
    "key-rootpass": f"ROOTPASS={SECRET}\n",
    "key-sshpass": f"SSHPASS={SECRET}\n",
    "key-pwd": f"PWD={SECRET}\n",
    "key-presharedkey": f"PresharedKey = {SECRET}\n",
    "yaml-sequence-at-parent-indent": f"secrets:\n- {SECRET}\nkeep: harmless\n",
    "yaml-multiword-plain-value": f"password: my secret phrase\n",
    "netrc-password": f"machine example.invalid login operator password {SECRET}\n",
    "curl-user-short": f"curl -u operator:{SECRET} https://example.invalid/\n",
    "curl-user-long": f"curl --user operator:{SECRET} https://example.invalid/\n",
    "webhook-path-raw": f"https://chat.example/hooks/{SECRET}\n",
    "webhook-path-key": f"mattermost_webhook_url: https://chat.example/hooks/{SECRET}\n",
    "json-in-list": f'{{"items":[{{"password":"{SECRET}"}}]}}\n',
    "json-secret-in-string": f'{{"note": "token={SECRET}"}}\n',
    # env style
    "env-export-glued": f"export GITHUBTOKEN={SECRET}\n",
    "env-pat-word": f"MY_PAT_VALUE={SECRET}\n",
    "env-quoted-space": f'TOKEN="{SECRET} with space"\n',
    "env-unquoted-space": f"PASSWORD=first {SECRET} tail\n",
    "camel-short-word-pass": f"dbPass: {SECRET}\n",
    "camel-short-word-pat": f"ghPat={SECRET}\n",
    "single-line-object-under-secret-key": f'credentials: {{"user": "u", "value": "{SECRET}"}}\n',
    "single-line-array-under-secret-key": f'"secrets": ["a", "{SECRET}"]\n',
    "toml-password": f'[db]\npassword = "{SECRET}"\n',
    "colon-space-before": f"password : {SECRET}\n",
    # headers, bearer, raw token formats
    "auth-token-scheme": f"Authorization: token {SECRET}\n",
    "auth-basic": f"Authorization: Basic {SECRET}\n",
    "bearer-bare": f"bearer {SECRET}\n",
    "curl-api-key-header": f"curl -H 'X-Api-Key: {SECRET}' http://x\n",
    "raw-ghp": f"cloned using ghp_{SECRET}abcdefghijklmnopqrstuvwxyz0123456789 ok\n",
    "raw-github-pat": f"github_pat_{SECRET}abcdefghijklmnopqrstuvwxyz0123 end\n",
    "raw-slack": f"xoxb-{SECRET}-1234567890-abcdef\n",
}


def _all(text: str) -> bytes:
    return text.encode()


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_first_pass_redacts_shape(name):
    cleaned = redaction.redact_bytes("etc/example/shape.conf", _all(SHAPES[name]))
    assert SECRET.encode() not in cleaned, cleaned.decode()
    assert redaction.MARKER.encode() in cleaned or b"://" in cleaned


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_second_pass_independently_detects_the_unredacted_shape(name):
    assert redaction.has_unredacted(SHAPES[name]), "second pass shares the first pass blind spot"


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_redacted_output_passes_second_pass_and_is_idempotent(name):
    once = redaction.redact_text(SHAPES[name]) if not name.startswith(("camel-json", "json-")) else None
    if once is None:
        cleaned = redaction.redact_bytes("etc/x/f.json", SHAPES[name].encode()).decode()
        assert not redaction.has_unredacted(cleaned)
        assert redaction.redact_bytes("etc/x/f.json", cleaned.encode()).decode() == cleaned
        return
    assert not redaction.has_unredacted(once), once
    assert redaction.redact_text(once) == once


def test_second_pass_does_not_share_the_first_passes_regexes():
    first = {name for name in dir(redaction) if name.endswith("_RE") and not name.startswith("_STEM")}
    source = Path(redaction.__file__).read_text(encoding="utf-8")
    second = source[source.index("def has_unredacted"):source.index("# File-level entry point")]
    for name in first:
        assert name not in second, f"second pass uses first-pass pattern {name}"
    for helper in ("redact_text", "_redact_line", "key_is_sensitive", "_KEY_AT_RE", "_VALUE_RE"):
        assert helper not in second


@pytest.mark.parametrize(
    "text",
    [
        "mkdir -p /var/lib/x\nssh -p 22 host\nps -p 1234 -o comm\n",
        "# rg-<token> -- the admission capacity object\n",
        "Description=gate containers and lane leaves\nExecStart=/usr/bin/foo --verbose --no-color\n",
        "PATH=/usr/local/bin:/usr/bin\nHOST_ROLE=dedicated-dev\nCOMMENT=harmless\n",
        "Environment=FOO=bar BAZ=qux\n",
        "url=https://host.example/path?x=1\nrepo=git://github.com/volkb79-2/vbpub\n",
        "users:\n  - name: runner\n    shell: /bin/bash\n",
    ],
)
def test_benign_content_is_not_mangled_or_refused(text):
    cleaned = redaction.redact_bytes("etc/example/benign.conf", text.encode()).decode()
    assert cleaned == text
    assert not redaction.has_unredacted(text)


def test_url_userinfo_is_removed_keeping_scheme_and_host():
    cleaned = redaction.redact_text(f"postgresql://u:{SECRET}@db:5432/x\n")
    assert cleaned == "postgresql://db:5432/x\n"


def test_yaml_block_ends_at_the_next_sibling_key():
    cleaned = redaction.redact_text(f"password: |\n  {SECRET}\nnext: keep\n")
    assert "next: keep" in cleaned and SECRET not in cleaned


def test_has_unredacted_flags_unmasked_block_children_and_heredoc_bodies():
    assert redaction.has_unredacted(f"credentials:\n  value: {SECRET}\n")
    assert redaction.has_unredacted(f"chpasswd <<EOF\nroot:{SECRET}\nEOF\n")
    assert not redaction.has_unredacted(
        f"credentials:\n  {redaction.MARKER}\nchpasswd <<EOF\n{redaction.MARKER}\nEOF\n"
    )


def test_private_key_block_is_replaced_and_detected():
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"
    assert redaction.has_unredacted(pem)
    cleaned = redaction.redact_text("before\n" + pem + "after\n")
    assert "abc" not in cleaned and redaction.PRIVATE_KEY_MARKER in cleaned
    assert not redaction.has_unredacted(cleaned)


@pytest.mark.parametrize(
    ("shape", "raw"),
    [
        ("base64", lambda size: b"A" * size),
        ("yaml-key-pairs", lambda size: b"a:" * (size // 2)),
        ("repeated-http-scheme", lambda size: (b"http://" * (size // 7)) + b"x" * (size % 7)),
        ("spaces", lambda size: b" " * size),
    ],
)
def test_one_megabyte_single_lines_take_the_deterministic_line_cap_path(shape, raw, monkeypatch):
    size = 1024 * 1024
    content = raw(size)
    assert len(content) == size, shape

    class RegexMustNotRun:
        def sub(self, *_args, **_kwargs):
            pytest.fail(f"regex scanner ran before the {shape} line cap")

    monkeypatch.setattr(redaction, "PRIVATE_KEY_RE", RegexMustNotRun())
    with pytest.raises(ValueError, match=r"line exceeds the 65536-byte redaction limit"):
        redaction.redact_bytes("etc/example/large.txt", content)


def test_total_file_budget_rejects_many_short_lines_before_scanning(monkeypatch):
    content = b"x\n" * (1024 * 1024 // 2 + 1)

    class RegexMustNotRun:
        def sub(self, *_args, **_kwargs):
            pytest.fail("regex scanner ran before the total file budget")

    monkeypatch.setattr(redaction, "PRIVATE_KEY_RE", RegexMustNotRun())
    with pytest.raises(ValueError, match=r"file exceeds the 1048576-byte redaction limit"):
        redaction.redact_bytes("etc/example/many-lines.txt", content)


def test_second_pass_uses_one_stem_lookup_for_a_long_identifier(monkeypatch):
    original = redaction._STEM_SCAN

    class CountedStemScan:
        steps = 0

        def search(self, value):
            self.steps += 1
            return original.search(value)

        def finditer(self, value):
            for match in original.finditer(value):
                self.steps += 1
                if self.steps > 2:
                    pytest.fail("second pass revisited sensitive stems in one identifier")
                yield match

    counted = CountedStemScan()
    monkeypatch.setattr(redaction, "_STEM_SCAN", counted)
    text = "token_" * 10000 + "\n"
    assert len(text.encode()) < redaction.MAX_REDACTION_LINE_BYTES
    assert not redaction.has_unredacted(text)
    assert counted.steps <= 2


def test_incomplete_pem_headers_are_scanned_once_per_file_line(monkeypatch):
    class CountedPattern:
        def __init__(self, pattern):
            self.pattern = pattern
            self.calls = 0

        def search(self, text, position=0):
            self.calls += 1
            return self.pattern.search(text, position)

    start = CountedPattern(redaction.PRIVATE_KEY_RE)
    end = CountedPattern(redaction.PRIVATE_KEY_END_RE)
    monkeypatch.setattr(redaction, "PRIVATE_KEY_RE", start)
    monkeypatch.setattr(redaction, "PRIVATE_KEY_END_RE", end)
    header = b"-----BEGIN PRIVATE KEY-----\n"
    content = header * (redaction.MAX_REDACTION_FILE_BYTES // len(header))
    line_count = len(content.splitlines())

    with pytest.raises(ValueError, match="unredacted"):
        redaction.redact_bytes("etc/example/incomplete.pem", content)
    assert start.calls == 1
    assert end.calls <= line_count


def test_unredactable_content_is_refused_not_exported(monkeypatch):
    monkeypatch.setattr(redaction, "redact_text", lambda text: text)
    with pytest.raises(ValueError, match="unredacted"):
        redaction.redact_bytes("etc/x/f.conf", f"password: {SECRET}\n".encode())


def test_remote_collector_embeds_the_same_redaction_source():
    exporter = _load("netcup_pre_reinstall_export_for_shapes", "pre-reinstall-export.py")
    collector = exporter.remote_collector("v1001")
    assert (NETCUP_DIR / "export_redaction.py").read_text(encoding="utf-8") in collector
    assert "__REDACTION_SOURCE__" not in collector
    compile(collector, "collector", "exec")


@pytest.mark.parametrize("name", ["unit-env-unquoted", "unit-env-double-quoted", "yaml-nested-password", "chpasswd-yaml"])
def test_remote_collector_redacts_shapes_in_allowlisted_systemd_units(tmp_path, name):
    from test_pre_reinstall_export import _run_collector

    def populate(root):
        directory = root / "etc/systemd/system"
        directory.mkdir(parents=True)
        (directory / "example.service").write_text(SHAPES[name], encoding="utf-8")

    names, files, _stderr = _run_collector(tmp_path, populate)
    exported = files["etc/systemd-system/example.service"]
    assert SECRET.encode() not in exported
    assert redaction.MARKER.encode() in exported
    assert SECRET.encode() not in b"\n".join(files.values())
