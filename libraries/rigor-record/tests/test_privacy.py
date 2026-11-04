import glob
import json

import pytest

from conftest import GOLDEN
from rigor_record import privacy
from rigor_record.privacy import Finding, lint, lint_string

# One sample per denylist class: (rule that must fire, offending text). Every text is synthetic.
LEAKS = [
    ("local-path", "build in /home/alice/work"),
    ("local-path", "see /workspaces/vbpub/x"),
    ("local-path", "log at /tmp/run.log"),
    ("local-path", "mounted /run/user/1000"),
    ("abs-path", "uses /usr/local/bin/tool"),
    ("abs-path", "/etc"),
    ("abs-path", "cwd=/srv/data/x"),
    ("home-tilde", "see ~/notes"),
    ("home-tilde", "dir ~ here"),
    ("windows-path", "C:\\Users\\bob"),
    ("url", "https://example.org/x"),
    ("url", "file:///x"),
    ("at-sign", "mail me@example.org"),
    ("token-prefix", "ghp_" + "a" * 36),
    ("token-prefix", "github_pat_" + "A1" * 20),
    ("token-prefix", "gho_abcdef"),
    ("token-prefix", "xoxb-1234"),
    ("token-prefix", "AKIA" + "ABCDEFGHIJKLMNOP"),
    ("token-prefix", "eyJhbGciOiJIUzI1NiJ9.payload"),
    ("token-prefix", "sk-" + "a" * 20),
    ("token-like", "Zm9vYmFyQmF6MTIzNDU2Nzg5MEFiQ2RFZkdoSWpL"),
    ("secret-word", "Bearer: abc"),
    ("secret-word", "password = x"),
    ("env-assignment", "run with HOME=x"),
    ("env-assignment", "GH_TOKEN=abc"),
    ("uid", "owner uid=1000"),
    ("uid", "GID: 27"),
    ("ipv4", "host 10.1.2.3 up"),
    ("ipv4", "192.168.0.1"),
    ("ipv6", "addr ::1"),
    ("ipv6", "fe80::1 link"),
    ("ipv6", "2001:db8:0:0:0:0:0:1"),
    ("geekbench", "https://browser.geekbench.com/v7/cpu/1"),
    ("geekbench", "cdn.geekbench.com"),
    ("geekbench", "BROWSER.GEEKBENCH.COM"),
    ("control-char", "two\nlines"),
    ("control-char", "tab\there"),
]


@pytest.mark.parametrize("rule,text", LEAKS)
def test_each_denylist_class_is_rejected(rule, text):
    assert Finding("$", rule) in lint_string(text)


def test_every_rule_has_a_sample_or_a_config_test():
    configured = {"host", "user"}
    assert {rule for rule, _ in LEAKS} | configured == set(privacy.RULES)


CLEAN = [
    "pass", "100.0/100.0", "1040/1218 lines; P-xyz restores full coverage", "src/cgprofile/limits.py",
    "tests/test_mod.py::test_case[3]", "2026-11-04T18:02:11Z", "cgprofile-v2.0.0", "sha256:" + "ab" * 32,
    "ab" * 32, "7.1.0", "assay-judge-identity/14", "urn:assay:schema:verdict:18", "python:compare-swap",
    "swapping < and <= is unobservable: both branches clamp to the same bound", "ghcr.io/x/y:tag",
    "org.opencontainers.image.revision", "tester-unified:local", "an_ordinary_long_snake_case_identifier_name_here",
    "A" * 40, "1.2.3", "a/b", "x.y", "n/a",
]


@pytest.mark.parametrize("text", CLEAN)
def test_ordinary_values_are_not_flagged(text):
    assert lint_string(text) == []


def test_lowercase_hex_digests_are_not_token_like():
    assert lint_string("0123456789abcdef" * 4) == []
    assert lint_string("A1b2" * 10) != []  # mixed case plus digits, 40 chars


def test_at_sign_is_allowed_only_inside_a_cpu_model():
    model = "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz"
    assert lint_string(model, "$.machine_classes[0].cpu_model") == []
    assert Finding("$.other", "at-sign") in lint_string(model, "$.other")
    assert lint_string("CPU a@b", "$.cpu_model") != []
    assert lint_string("CPU @2.4", "$.cpu_model") != []


def test_configured_hosts_and_users():
    assert lint_string("built on build-7.lab.example") == []
    assert Finding("$", "host") in lint_string("built on Build-7.lab.example", hosts=["build-7.lab.example"])
    assert lint_string("x", hosts=[""]) == []
    assert Finding("$", "user") in lint_string("owner: Alice.", users=["alice"])
    assert lint_string("malice", users=["alice"]) == []
    assert lint_string("x", users=[""]) == []


def test_ipv6_needs_two_colons_and_a_valid_address():
    assert lint_string("18:02:11") == []
    assert lint_string("2026-11-04T18:02:11Z") == []
    assert lint_string("ab:cd") == []


def test_ipv4_needs_a_valid_address():
    assert lint_string("999.1.1.1") == []
    assert lint_string("1.2.3") == []
    assert lint_string("1.2.3.4.5") == []


def test_lint_walks_values_keys_and_lists_with_paths():
    doc = {"a": ["ok", {"b": "/home/x"}], "c": "fine"}
    assert lint(doc) == [Finding("$.a[1].b", "local-path"), Finding("$.a[1].b", "abs-path")]
    assert lint(["x", "me@host"]) == [Finding("$[1]", "at-sign")]
    assert lint(5) == [] and lint(None) == [] and lint(True) == []


def test_a_leaking_key_is_found_and_not_echoed():
    secret_key = "ghp_" + "z" * 30
    findings = lint({secret_key: "value /tmp/x"})
    assert Finding("$.<key>", "token-prefix") in findings
    assert Finding("$.<redacted-key>", "local-path") in findings
    assert all(secret_key not in str(f) for f in findings)


def test_findings_never_echo_the_matched_text():
    token = "ghp_" + "q" * 36
    text = "see /home/alice and " + token
    for f in lint({"k": text}):
        assert "alice" not in str(f) and token not in str(f)


def test_lint_passes_hosts_and_users_down():
    assert lint({"a": ["foo-host"]}, hosts=["foo-host"]) == [Finding("$.a[0]", "host")]
    assert lint({"a": "bob"}, users=["bob"]) == [Finding("$.a", "user")]
    assert lint({"bob": 1}, users=["bob"]) == [Finding("$.<key>", "user")]


def test_finding_str():
    assert str(Finding("$.a", "url")) == "$.a: privacy rule url"


def test_all_goldens_are_privacy_clean():
    paths = sorted(glob.glob(str(GOLDEN / "**" / "*.json"), recursive=True))
    assert len(paths) == 16  # 4 fragments, 11 records, the percentile vectors
    for path in paths:
        assert lint(json.load(open(path))) == [], path
