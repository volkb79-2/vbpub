"""CLI-boundary tests for ownership, drift, and ``show --json``."""

from __future__ import annotations

import json
import stat
import tomllib
from pathlib import Path

import pytest

from test_cli_runtime import answer_file, make_root, run_cli


ANSWERS = """\
[tls_edge]
domain = "edge.example.test"

[tls_edge.http3]
enabled = true

[tls_edge.calls]
enabled = true
public_port = 8443
"""

DRIFT_MESSAGE = (
    "consumer.env TLS_EDGE_DOMAIN differs from the site override; "
    "run tls-edge configure to re-sync"
)


def _expected_contract(root: Path, answers: str) -> dict[str, str]:
    from tls_edge_cli.contract import contract_for

    overlay = tomllib.loads(answers)
    return contract_for(root / "stack" / "ciu-stack", overlay)


def _configure(
    root: Path,
    answers_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, str]:
    return run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(root),
            "--answers",
            str(answers_path),
            "--yes",
        ],
    )


def _canonical_env(values: dict[str, str]) -> str:
    from tls_edge_cli.contract import CONTRACT_KEYS

    return "".join(f"{key}={values[key]}\n" for key in CONTRACT_KEYS)


def _hand_edit_domain(env_path: Path) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    edited = [
        "TLS_EDGE_DOMAIN=hand-edited.example.test"
        if line.startswith("TLS_EDGE_DOMAIN=")
        else line
        for line in lines
    ]
    env_path.write_text("\n".join(edited) + "\n", encoding="utf-8")


def test_configure_writes_the_full_contract_to_a_new_0644_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    env_path = root / "stack" / "consumer.env"
    assert env_path.read_text(encoding="utf-8") == _canonical_env(
        _expected_contract(root, ANSWERS)
    )
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o644


def test_configure_rewrites_owned_keys_in_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    env_path = root / "stack" / "consumer.env"
    env_path.write_text(
        "CUSTOM_BEFORE=one\n"
        "TLS_EDGE_DOMAIN=stale.example.test\n"
        "CUSTOM_AFTER=two\n",
        encoding="utf-8",
    )
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == [
        "CUSTOM_BEFORE=one",
        "TLS_EDGE_DOMAIN=edge.example.test",
        "CUSTOM_AFTER=two",
    ]


def test_configure_appends_missing_owned_keys_in_contract_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    env_path = root / "stack" / "consumer.env"
    env_path.write_text(
        "TLS_EDGE_DOMAIN=stale.example.test\n"
        "CUSTOM_CONSUMER_SETTING=kept\n",
        encoding="utf-8",
    )
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == [
        "TLS_EDGE_DOMAIN=edge.example.test",
        "CUSTOM_CONSUMER_SETTING=kept",
    ]
    assert lines[2:] == [
        f"{key}={_expected_contract(root, ANSWERS)[key]}"
        for key in (
            "TLS_EDGE_NETWORK",
            "TLS_EDGE_HTTPS_ENTRYPOINT",
            "TLS_EDGE_HEADERS_MIDDLEWARE",
            "TLS_EDGE_HTTP3",
            "TLS_EDGE_PUBLIC_PORTS",
        )
    ]


def test_configure_removes_duplicate_owned_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    env_path = root / "stack" / "consumer.env"
    env_path.write_text(
        "TLS_EDGE_DOMAIN=first.example.test\n"
        "CUSTOM_SETTING=kept\n"
        "TLS_EDGE_DOMAIN=second.example.test\n",
        encoding="utf-8",
    )
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    text = env_path.read_text(encoding="utf-8")
    assert text.count("TLS_EDGE_DOMAIN=") == 1
    assert "TLS_EDGE_DOMAIN=edge.example.test" in text
    assert "CUSTOM_SETTING=kept" in text


def test_configure_keeps_comments_blank_lines_and_unowned_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    env_path = root / "stack" / "consumer.env"
    preserved = (
        "# operator-owned consumer settings\n"
        "CUSTOM_CONSUMER_SETTING=keep-me\n"
        "\n"
        "ANOTHER_CONSUMER_SETTING=also-keep\n"
    )
    env_path.write_text(
        preserved
        + "TLS_EDGE_DOMAIN=stale.example.test\n",
        encoding="utf-8",
    )
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    assert env_path.read_text(encoding="utf-8").startswith(preserved)


def test_configure_keeps_the_existing_consumer_env_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    env_path = root / "stack" / "consumer.env"
    env_path.write_text(
        "TLS_EDGE_DOMAIN=stale.example.test\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_hand_edited_domain_is_refused_then_re_synced_by_configure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    env_path = root / "stack" / "consumer.env"
    expected = env_path.read_text(encoding="utf-8")
    _hand_edit_domain(env_path)

    result, output = run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 2, output
    assert DRIFT_MESSAGE in output

    empty_answers = answer_file(tmp_path / "empty.toml", "")
    result, output = _configure(root, empty_answers, monkeypatch)
    assert result == 0, output
    assert env_path.read_text(encoding="utf-8") == expected

    result, output = run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 0, output


def test_validate_ignores_an_absent_consumer_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    override = root / "stack" / "ciu-stack" / "ciu.toml.j2"
    override.write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n',
        encoding="utf-8",
    )
    result, output = run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 0, output


def test_validate_names_every_drifted_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    env_path = root / "stack" / "consumer.env"
    text = env_path.read_text(encoding="utf-8").replace(
        "TLS_EDGE_NETWORK=ingress_public\n",
        "",
    )
    env_path.write_text(
        text.replace(
            "TLS_EDGE_DOMAIN=edge.example.test",
            "TLS_EDGE_DOMAIN=hand-edited.example.test",
        ),
        encoding="utf-8",
    )
    result, output = run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 2, output
    assert (
        "consumer.env TLS_EDGE_DOMAIN, TLS_EDGE_NETWORK differ from the site "
        "override; run tls-edge configure to re-sync"
    ) in output


def test_show_reports_drift_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    _hand_edit_domain(root / "stack" / "consumer.env")

    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(root)],
    )
    assert result == 0, output
    assert "Consumer environment: drift (TLS_EDGE_DOMAIN)" in output


def test_show_reports_an_in_sync_consumer_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(root)],
    )
    assert result == 0, output
    assert "Consumer environment: in-sync." in output


@pytest.mark.parametrize("shape", ["directory", "symlink", "invalid-utf8"])
def test_show_refuses_an_unreadable_consumer_env_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    env_path = root / "stack" / "consumer.env"
    env_path.unlink()
    if shape == "directory":
        env_path.mkdir()
        expected = "consumer environment must be a regular file"
    elif shape == "symlink":
        env_path.symlink_to(tmp_path / "missing-target")
        expected = "consumer environment must be a regular file"
    else:
        env_path.write_bytes(b"\xff\xfe")
        expected = "cannot read consumer environment"
    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(root)],
    )
    assert result == 2, output
    assert expected in output


def test_show_json_has_the_exact_envelope_and_in_sync_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output

    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(root), "--json"],
    )
    assert result == 0, output
    payload = json.loads(output)
    assert set(payload) == {
        "kind",
        "format",
        "version",
        "schema",
        "stack_dir",
        "override",
        "contract",
        "ports",
        "consumer_env",
    }
    assert payload["kind"] == "tls-edge.show"
    assert payload["format"] == 1
    assert payload["version"] == "0.0.0"
    assert payload["schema"] == 1
    assert payload["stack_dir"] == str(root / "stack" / "ciu-stack")
    assert payload["override"] is True
    assert payload["contract"] == _expected_contract(root, ANSWERS)
    assert payload["ports"] == [
        {"port": 80, "protocol": "tcp", "purpose": "HTTP redirect and/or HTTP-01"},
        {"port": 443, "protocol": "tcp", "purpose": "HTTPS, HTTP/1.1 and HTTP/2"},
        {"port": 443, "protocol": "udp", "purpose": "HTTP/3 over QUIC"},
        {"port": 8443, "protocol": "tcp", "purpose": "Calls media"},
        {"port": 8443, "protocol": "udp", "purpose": "Calls media"},
    ]
    assert payload["consumer_env"] == {"state": "in-sync", "drift": []}


def test_show_json_reports_drift_and_absence_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", ANSWERS)
    result, output = _configure(root, answers_path, monkeypatch)
    assert result == 0, output
    env_path = root / "stack" / "consumer.env"
    while "\n\n" in env_path.read_text(encoding="utf-8"):
        env_path.write_text(
            env_path.read_text(encoding="utf-8").replace("\n\n", "\n"),
            encoding="utf-8",
        )
    _hand_edit_domain(env_path)

    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(root), "--json"],
    )
    assert result == 0, output
    payload = json.loads(output)
    assert payload["consumer_env"] == {
        "state": "drift",
        "drift": ["TLS_EDGE_DOMAIN"],
    }

    missing_key_env = env_path.read_text(encoding="utf-8").replace(
        "TLS_EDGE_NETWORK=ingress_public\n",
        "",
    )
    env_path.write_text(missing_key_env, encoding="utf-8")
    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(root), "--json"],
    )
    assert result == 0, output
    payload = json.loads(output)
    assert payload["consumer_env"] == {
        "state": "drift",
        "drift": ["TLS_EDGE_DOMAIN", "TLS_EDGE_NETWORK"],
    }

    env_path.unlink()
    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(root), "--json"],
    )
    assert result == 0, output
    payload = json.loads(output)
    assert payload["consumer_env"] == {"state": "absent", "drift": []}
