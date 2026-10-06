"""LT-KEY B: docker_default_address_pools (mdt MDT-002)."""
from __future__ import annotations

import json

import pytest

from debian_install_v2.config import Config, ConfigError, load_config, validate_config
from debian_install_v2.tests.test_r1_remaining import make_installer

DEFAULT = [{"base": "10.240.0.0/16", "size": 24}]


def _written(installer):
    return json.loads(installer.actions.dry_run_writes["/etc/docker/daemon.json"])


def test_default_is_the_mdt_pool():
    assert Config().docker_default_address_pools == DEFAULT


def test_default_pool_rendered_in_daemon_json(tmp_path):
    installer = make_installer(tmp_path)
    installer._configure_docker_daemon()
    assert _written(installer)["default-address-pools"] == DEFAULT


def test_daemon_json_golden(tmp_path):
    installer = make_installer(tmp_path)
    installer._configure_docker_daemon()
    assert installer.actions.dry_run_writes["/etc/docker/daemon.json"] == (
        "{\n"
        '  "live-restore": true,\n'
        '  "log-driver": "journald",\n'
        '  "default-address-pools": [\n'
        "    {\n"
        '      "base": "10.240.0.0/16",\n'
        '      "size": 24\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )


def test_custom_pools_rendered_in_order(tmp_path):
    pools = [{"base": "10.250.0.0/16", "size": 24}, {"base": "fd00:1::/48", "size": 64}]
    installer = make_installer(tmp_path, docker_default_address_pools=pools)
    installer._configure_docker_daemon()
    assert _written(installer)["default-address-pools"] == pools


def test_empty_list_omits_the_key(tmp_path):
    installer = make_installer(tmp_path, docker_default_address_pools=[])
    installer._configure_docker_daemon()
    assert "default-address-pools" not in _written(installer)


def test_empty_list_removes_a_stale_key_on_rerun(tmp_path, monkeypatch):
    from debian_install_v2.tests.test_r1_remaining import _patch_daemon_json_exists

    installer = make_installer(tmp_path, dry_run=False, docker_default_address_pools=[])
    _patch_daemon_json_exists(monkeypatch, {"default-address-pools": DEFAULT, "dns": ["1.1.1.1"]})
    installer._configure_docker_daemon()
    written = json.loads(installer.actions.files["/etc/docker/daemon.json"])
    assert "default-address-pools" not in written and written["dns"] == ["1.1.1.1"]


def test_loads_from_config_json_and_empty_list_is_valid():
    assert load_config(raw_json='{"docker_default_address_pools": []}').docker_default_address_pools == []
    config = load_config(raw_json='{"docker_default_address_pools":[{"base":"172.30.0.0/16","size":28}]}')
    assert config.docker_default_address_pools == [{"base": "172.30.0.0/16", "size": 28}]


@pytest.mark.parametrize("bad", [
    "10.240.0.0/16",                                        # not a list
    {"base": "10.240.0.0/16", "size": 24},                  # dict, not list
    ["10.240.0.0/16"],                                      # entry not an object
    [{"base": "10.240.0.0/16"}],                            # missing size
    [{"base": "10.240.0.0/16", "size": 24, "x": 1}],        # extra key
    [{"base": 5, "size": 24}],                              # base not a string
    [{"base": "not-a-net", "size": 24}],                    # unparsable
    [{"base": "10.240.0.1/16", "size": 24}],                # host bits set
    [{"base": "10.240.0.0/16", "size": "24"}],              # size string
    [{"base": "10.240.0.0/16", "size": True}],              # size bool
    [{"base": "10.240.0.0/16", "size": 15}],                # size < prefix
    [{"base": "10.240.0.0/16", "size": 31}],                # v4 size > 30
    [{"base": "fd00::/48", "size": 129}],                   # v6 size > 128
    [{"base": "fd00::/48", "size": 40}],                    # v6 size < prefix
    [{"base": "10.240.0.0/16", "size": 24}, {"base": "10.240.1.0/24", "size": 28}],  # overlap
    [{"base": f"10.{n}.0.0/16", "size": 24} for n in range(17)],                    # > 16
])
def test_invalid_pools_rejected(bad):
    with pytest.raises(ConfigError, match="docker_default_address_pools"):
        validate_config(Config(docker_default_address_pools=bad))


def test_exactly_sixteen_disjoint_pools_accepted():
    validate_config(Config(
        docker_default_address_pools=[{"base": f"10.{n}.0.0/16", "size": 24} for n in range(16)]
    ))


def test_v4_and_v6_bases_do_not_count_as_overlapping():
    validate_config(Config(docker_default_address_pools=[
        {"base": "10.240.0.0/16", "size": 24}, {"base": "fd00::/16", "size": 64},
    ]))


def test_wizard_accepts_a_json_list_and_retries_bad_json(tmp_path):
    from debian_install_v2.wizard import WIZARD_SECTIONS, _ask_field

    field = next(f for s in WIZARD_SECTIONS for f in s.fields if f.name == "docker_default_address_pools")
    answers = iter(["[not json", '[{"base": "10.9.0.0/16", "size": 20}]'])
    seen_defaults = []

    class Q:
        @staticmethod
        def text(prompt, *, default=""):
            seen_defaults.append(default)

            class A:
                @staticmethod
                def ask(**kw):
                    return next(answers)
            return A

    class RT:
        class output:
            warnings = []
            @classmethod
            def warn(cls, m):
                cls.warnings.append(m)

    value = _ask_field(Q, field, DEFAULT, RT)
    assert value == [{"base": "10.9.0.0/16", "size": 20}]
    assert json.loads(seen_defaults[0]) == DEFAULT and len(RT.output.warnings) == 1
