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
    [{"base": "0.0.0.0/0", "size": 24}],                    # unspecified
    [{"base": "127.0.0.0/8", "size": 24}],                  # loopback
    [{"base": "169.254.0.0/16", "size": 24}],               # link-local
    [{"base": "224.0.0.0/4", "size": 24}],                  # multicast
    [{"base": "8.8.0.0/16", "size": 24}],                   # public
    [{"base": "::ffff:10.0.0.0/104", "size": 120}],         # IPv4-mapped v6
    [{"base": "fe80::/10", "size": 64}],                    # v6 link-local
    [{"base": "::1/128", "size": 128}],                     # v6 loopback
    [{"base": "2a00::/16", "size": 48}],                    # v6 public
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


# --- host-subnet overlap check (fix round 1) ----------------------------------

ADDR = ("/usr/sbin/ip", "-j", "addr", "show")
ROUTE = ("/usr/sbin/ip", "-j", "route", "show")


def _host(installer, addrs=(), routes=()):
    installer.actions.outputs[ADDR] = json.dumps([
        {"ifname": "eth0", "addr_info": [
            {"family": "inet6" if ":" in a else "inet", "local": a.split("/")[0], "prefixlen": int(a.split("/")[1])}
            for a in addrs
        ]}
    ])
    installer.actions.outputs[ROUTE] = json.dumps([{"dst": d} for d in routes])


def _real(tmp_path, **kw):
    return make_installer(tmp_path, dry_run=False, **kw)


def test_pool_clear_of_host_networks_is_written(tmp_path):
    installer = _real(tmp_path)
    _host(installer, addrs=["10.0.0.5/24", "127.0.0.1/8", "fe80::1/64"],
          routes=["default", "10.0.0.0/24", "172.17.0.0/16"])
    installer._configure_docker_daemon()
    assert json.loads(installer.actions.files["/etc/docker/daemon.json"])["default-address-pools"] == DEFAULT


@pytest.mark.parametrize("addrs, routes, needle", [
    (["10.240.5.9/24"], [], "address 10.240.5.9/24 on eth0"),
    ([], ["10.240.128.0/17"], "route 10.240.128.0/17"),
    ([], ["10.0.0.0/8"], "route 10.0.0.0/8"),
    ([], ["10.240.1.1"], "route 10.240.1.1"),
])
def test_pool_overlapping_host_v4_fails_naming_pool_and_conflict(tmp_path, addrs, routes, needle):
    from debian_install_v2.installer import InstallerError

    installer = _real(tmp_path)
    _host(installer, addrs, routes)
    with pytest.raises(InstallerError, match="10.240.0.0/16") as exc:
        installer._configure_docker_daemon()
    assert needle in str(exc.value)
    assert "/etc/docker/daemon.json" not in installer.actions.files


def test_v6_pool_overlapping_host_v6_address_fails(tmp_path):
    from debian_install_v2.installer import InstallerError

    installer = _real(tmp_path, docker_default_address_pools=[{"base": "fd00:1::/48", "size": 64}])
    _host(installer, addrs=["fd00:1::5/64"])
    with pytest.raises(InstallerError, match="fd00:1::/48"):
        installer._configure_docker_daemon()


def test_v4_pool_is_not_compared_with_v6_host_addresses(tmp_path):
    installer = _real(tmp_path)
    _host(installer, addrs=["fd00::5/8"])
    installer._configure_docker_daemon()


def test_empty_pools_skip_the_host_check(tmp_path):
    installer = _real(tmp_path, docker_default_address_pools=[])
    _host(installer, addrs=["10.240.5.9/24"])
    installer._configure_docker_daemon()
    assert not any(a.argv[:2] == ("/usr/sbin/ip", "-j") for a in installer.actions.planned)


def test_unparseable_ip_output_fails_closed(tmp_path):
    from debian_install_v2.installer import InstallerError

    installer = _real(tmp_path)
    installer.actions.outputs[ADDR] = "not json"
    with pytest.raises(InstallerError, match="ip -j addr"):
        installer._configure_docker_daemon()


def test_unparseable_ip_route_output_fails_closed(tmp_path):
    from debian_install_v2.installer import InstallerError

    installer = _real(tmp_path)
    _host(installer)
    installer.actions.outputs[ROUTE] = "not json"
    with pytest.raises(InstallerError, match="ip -j route"):
        installer._configure_docker_daemon()
    assert "/etc/docker/daemon.json" not in installer.actions.files


def _bridge_host(installer, ifname):
    installer.actions.outputs[ADDR] = json.dumps([
        {"ifname": ifname, "addr_info": [{"family": "inet", "local": "10.240.0.1", "prefixlen": 24}]}
    ])
    installer.actions.outputs[ROUTE] = json.dumps([{"dst": "10.240.0.0/24", "dev": ifname}])


@pytest.mark.parametrize("ifname", ["br-abc123", "docker0", "veth9f2"])
def test_rerun_with_docker_bridge_inside_the_pool_passes(tmp_path, ifname):
    installer = _real(tmp_path)
    _bridge_host(installer, ifname)
    installer._configure_docker_daemon()
    assert json.loads(installer.actions.files["/etc/docker/daemon.json"])["default-address-pools"] == DEFAULT


def test_non_docker_interface_inside_the_pool_still_fails(tmp_path):
    from debian_install_v2.installer import InstallerError

    installer = _real(tmp_path)
    _bridge_host(installer, "eth1")
    with pytest.raises(InstallerError, match="10.240.0.0/16"):
        installer._configure_docker_daemon()


def test_literal_zero_prefix_routes_are_skipped_like_default(tmp_path):
    installer = _real(tmp_path)
    _host(installer, routes=["0.0.0.0/0"])
    installer._configure_docker_daemon()
    assert "/etc/docker/daemon.json" in installer.actions.files
