from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from topos.cli import _filter_kwargs, parse_bpf_args


def test_filter_kwargs_converts_all_present_filters_to_collector_types() -> None:
    args = argparse.Namespace(
        entities=["systemd/*", "docker/*"],
        slice="user.slice",
        container_selectors=["web", "api-*"],
        metrics="compact",
    )

    result = _filter_kwargs(args)

    assert result == {
        "entities_globs": ("systemd/*", "docker/*"),
        "slice_names": ("user.slice",),
        "container_selectors": ("web", "api-*"),
        "metrics_mode": "compact",
    }
    assert all(isinstance(result[key], tuple) for key in ("entities_globs", "slice_names", "container_selectors"))


def test_filter_kwargs_preserves_absent_filters_as_none() -> None:
    result = _filter_kwargs(
        argparse.Namespace(
            entities=None,
            slice=None,
            container_selectors=None,
            metrics="full",
        )
    )

    assert result == {
        "entities_globs": None,
        "slice_names": None,
        "container_selectors": None,
        "metrics_mode": "full",
    }
    assert all(result[key] is None for key in ("entities_globs", "slice_names", "container_selectors"))


def test_bpf_gate_parser_requires_explicit_safe_subcommand() -> None:
    with pytest.raises(SystemExit) as raised:
        parse_bpf_args([])

    assert raised.value.code == 2


def test_bpf_gate_parser_preserves_explicit_probe_roots_and_json_mode() -> None:
    args = parse_bpf_args(
        ["gate", "--proc-root", "/fixture/proc", "--pin-root", "/fixture/bpf", "--json"]
    )

    assert args.command == "gate"
    assert args.proc_root == Path("/fixture/proc")
    assert args.pin_root == Path("/fixture/bpf")
    assert args.json is True
