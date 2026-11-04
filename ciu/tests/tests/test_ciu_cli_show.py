"""``ciu show profiles|layouts`` (decision C6): read-only listings, both with ``--json``."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, deploy  # noqa: E402

CONFIG = {
    "deploy": {
        "profiles": {
            "core": {"phases": [1], "stacks": ["infra/a"], "compose_profiles": ["db"],
                     "topology_overrides": {"public_fqdn": "x", "alpha": 1}},
            "empty": None,
        },
        "layouts": {
            "dev": {"environment": "dev", "hosts": {"box": {"bundles": ["core"]}}},
            "bare": {"hosts": {}},
        },
    }
}


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.fixture
def layouts_root(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    holder = {"config": CONFIG}
    monkeypatch.setattr(cli, "_load_remote_config", lambda _root: holder["config"])
    return holder


def test_show_layouts_lists_what_is_declared(layouts_root, monkeypatch, capsys):
    assert _run(monkeypatch, ["show", "layouts"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "dev: environment=dev hosts=[box]",
        "bare: hosts=[(no hosts)]",
    ]


def test_show_layouts_without_any_says_so(layouts_root, monkeypatch, capsys):
    layouts_root["config"] = {"deploy": {}}
    assert _run(monkeypatch, ["show", "layouts"]) == 0
    assert capsys.readouterr().out.strip() == "(no layouts declared)"


def test_show_layouts_json_is_one_versioned_document(layouts_root, monkeypatch, capsys):
    assert _run(monkeypatch, ["show", "layouts", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document == {
        "schema_version": 1,
        "layouts": {
            "bare": {"environment": "", "hosts": []},
            "dev": {"environment": "dev", "hosts": ["box"]},
        },
    }


def test_show_profiles_json_is_one_versioned_document(capsys):
    assert deploy.action_list_profiles(CONFIG, json_output=True) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 1
    assert document["profiles"]["core"] == {
        "phases": [1], "stacks": ["infra/a"], "compose_profiles": ["db"],
        "topology_overrides": ["alpha", "public_fqdn"],
    }
    assert document["profiles"]["empty"] == {
        "phases": [], "stacks": [], "compose_profiles": [], "topology_overrides": [],
    }


def test_show_profiles_text_form_is_unchanged(capsys):
    assert deploy.action_list_profiles({"deploy": {"profiles": {}}}) == 0
    assert "(none defined" in capsys.readouterr().out
