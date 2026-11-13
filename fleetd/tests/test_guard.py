from pathlib import Path

import pytest

from fleetd.contracts import ContractError, GuardConfig
from fleetd.guard import parse_guard_toml, validate_guard


FIXTURE = Path(__file__).parent / "fixtures" / "guard.valid.toml"


def _raw():
    return parse_guard_toml(FIXTURE.read_text(encoding="utf-8")).to_dict()


def _invalid(data, message):
    with pytest.raises(ContractError, match=message):
        validate_guard(data)


def test_guard_fixture_parses_to_typed_rules():
    result = parse_guard_toml(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(result, GuardConfig)
    assert result.to_dict()["mode"] == "report"


def test_guard_mode_is_closed_and_allows_report_or_enforce():
    data = _raw()
    data["mode"] = "enforce"
    assert validate_guard(data).to_dict()["mode"] == "enforce"
    data["mode"] = "observe"
    _invalid(data, "mode: expected report or enforce")


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda data: data.update(extra=True), "unknown field.*extra"),
        (lambda data: data.pop("enabled"), "missing field.*enabled"),
        (lambda data: data.update(enabled=1), "enabled: expected boolean"),
        (lambda data: data.update(sample_interval_s=0), "sample_interval_s: must be at least 1"),
        (lambda data: data["trigger"].update(extra=True), "trigger: unknown field.*extra"),
        (lambda data: data["trigger"].update(soft_full_pct=101), "soft_full_pct: must be at most 100"),
        (lambda data: data["trigger"].update(soft_sustained_s=0), "soft_sustained_s: must be at least 1"),
        (lambda data: data["trigger"].update(useful_cpu_min=1.1), "useful_cpu_min: must be at most 1"),
        (lambda data: data["trigger"].update(hard_full_pct=9), "hard_full_pct: must be at least soft_full_pct"),
        (lambda data: data["trigger"].update(hard_sustained_s=0), "hard_sustained_s: must be at least 1"),
        (lambda data: data["thaw"].update(full_below_pct=-1), "full_below_pct: must be at least 0"),
        (lambda data: data["thaw"].update(sustained_s=0), "sustained_s: must be at least 1"),
        (lambda data: data["victim"].update(policy="largest-rss"), "expected least-progress-per-ram"),
        (lambda data: data["reclaim"].update(step_bytes="1t"), "invalid memory value"),
        (lambda data: data["reclaim"].update(swappiness=201), "swappiness: must be at most 200"),
        (lambda data: data["reclaim"].update(max_parked=-1), "max_parked: must be at least 0"),
        (lambda data: data["reclaim"].update(max_park_s=0), "max_park_s: must be at least 1"),
        (lambda data: data["log"].update(io_pressure="true"), "io_pressure: expected boolean"),
    ],
)
def test_guard_rejects_unknown_values_and_out_of_range_rules(edit, message):
    data = _raw()
    edit(data)
    _invalid(data, message)


def test_guard_rejects_wrong_types_and_missing_sections():
    _invalid(None, "expected object")
    data = _raw()
    del data["trigger"]
    _invalid(data, "missing field.*trigger")
    data = _raw()
    data["trigger"]["useful_cpu_min"] = False
    _invalid(data, "useful_cpu_min: expected number")
    data = _raw()
    data["trigger"]["soft_full_pct"] = "10"
    _invalid(data, "soft_full_pct: expected number")
    data = _raw()
    data["reclaim"]["swappiness"] = True
    _invalid(data, "swappiness: expected integer")
    data = _raw()
    data["reclaim"]["swappiness"] = -1
    _invalid(data, "swappiness: must be at least 0")


def test_guard_rejects_invalid_toml_syntax():
    with pytest.raises(ContractError, match="invalid TOML"):
        parse_guard_toml("mode = [")
