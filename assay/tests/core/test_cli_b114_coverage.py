"""Public CLI regressions for persisted B117 campaign admission facts."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from conftest import GitRepo, R0_LANE

from assay import cli
from assay.errors import Outcome


def _lane_config(repo: GitRepo) -> Path:
    config = repo.write(
        "assay.toml",
        R0_LANE.replace(
            'argv = ["pytest", "tests/unit", "-q"]',
            'argv = ["/bin/true"]',
        ),
    )
    repo.commit_all("declare R0 campaign lane")
    return config


def _init_args(config: Path, target: Path) -> list[str]:
    return [
        "campaign", "init", "--campaign", "b110-phase1",
        "--lane", "package", "--hours", "1", "--file", str(config),
        "--out", str(target),
    ]


def _invoke(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(args, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_campaign_init_persists_exact_identity_and_accepts_same_identity(
    git_repo: GitRepo, tmp_path: Path
):
    config = _lane_config(git_repo)
    target = tmp_path / "deadline.json"
    args = _init_args(config, target)
    code, out, err = _invoke(args)
    assert code == Outcome.PASS.exit_code, err
    assert out.strip() == str(target)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["schema"] == "assay-campaign-deadline/1"
    assert document["commit"] == git_repo.head()
    assert document["git_tree"] == git_repo.git("rev-parse", "HEAD^{tree}").strip()
    assert document["lanes"] == ["package"]
    assert document["plan_sha256"] == {"package": None}
    original = target.read_bytes()

    code, _, err = _invoke(args)
    assert code == Outcome.PASS.exit_code, err
    assert target.read_bytes() == original


def test_campaign_init_and_deadline_read_accept_a_pinned_proc_fd_root(
    git_repo: GitRepo, tmp_path: Path
):
    config = _lane_config(git_repo)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        root = Path(f"/proc/self/fd/{descriptor}")
        target = root / "deadline.json"
        state_dir = root / "state"
        args = _init_args(config, target)
        args.extend(("--state-dir", str(state_dir)))

        code, out, err = _invoke(args)

        assert code == Outcome.PASS.exit_code, err
        assert out.strip() == str(target)
        assert (tmp_path / "deadline.json").is_file()
        document, _raw, _expires = cli._parse_campaign_deadline(
            target, lane="package"
        )
        assert document["schema"] == "assay-campaign-deadline/1"
        assert document["lanes"] == ["package"]
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("override", "diagnosis"),
    [
        ({"--campaign": "bad/name"}, "--campaign must match"),
        ({"--hours": "0"}, "--hours must be a decimal"),
        ({"--hours": "25"}, "--hours must be a decimal"),
        ({"--hours": "nan"}, "--hours must be a decimal"),
        ({"--wheel-sha256": "WRONG"}, "--wheel-sha256 must be 64 lowercase"),
    ],
)
def test_campaign_init_refuses_invalid_policy_before_writing(
    git_repo: GitRepo, tmp_path: Path, override, diagnosis
):
    config = _lane_config(git_repo)
    target = tmp_path / "deadline.json"
    args = _init_args(config, target)
    for option, value in override.items():
        if option in args:
            args[args.index(option) + 1] = value
        else:
            args.extend((option, value))
    code, out, err = _invoke(args)
    assert code == Outcome.ERROR.exit_code
    assert diagnosis in err
    assert out == ""
    assert not target.exists()


def test_campaign_init_refuses_duplicate_lane_before_writing(
    git_repo: GitRepo, tmp_path: Path
):
    config = _lane_config(git_repo)
    target = tmp_path / "deadline.json"
    args = _init_args(config, target)
    args.extend(("--lane", "package"))
    code, _, err = _invoke(args)
    assert code == Outcome.ERROR.exit_code
    assert "--lane values must be unique" in err
    assert not target.exists()


def test_campaign_init_accepts_explicit_future_utc_expiry_and_refuses_expired_one(
    git_repo: GitRepo, tmp_path: Path
):
    config = _lane_config(git_repo)
    target = tmp_path / "future.json"
    args = _init_args(config, target)
    args[args.index("--hours"):args.index("--hours") + 2] = [
        "--expires-at", "2099-01-01T00:00:00Z"
    ]
    code, _, err = _invoke(args)
    assert code == Outcome.PASS.exit_code, err
    assert json.loads(target.read_text(encoding="utf-8"))["expires_at_utc"] == (
        "2099-01-01T00:00:00Z"
    )

    expired = tmp_path / "expired.json"
    args[args.index("--out") + 1] = str(expired)
    args[args.index("--expires-at") + 1] = "2000-01-01T00:00:00Z"
    code, _, err = _invoke(args)
    assert code == Outcome.ERROR.exit_code
    assert "--expires-at must be strictly in the future" in err
    assert not expired.exists()


@pytest.mark.parametrize(
    ("defect", "diagnosis"),
    [
        ("duplicate-key", "not valid unique-key JSON"),
        ("non-utf8", "not valid unique-key JSON"),
        ("unknown-field", "must contain exactly the declared fields"),
        ("schema", "has an unknown schema"),
        ("campaign", "has an invalid campaign name"),
        ("commit", "has an invalid commit"),
        ("git_tree", "has an invalid git_tree"),
        ("lanes", "lanes must be sorted and unique"),
        ("missing-lane", "does not include lane 'package'"),
        ("assay_version", "has an invalid assay_version"),
        ("wheel_sha256", "has an invalid wheel_sha256"),
        ("plan-keys", "plan_sha256 keys must exactly match lanes"),
        ("plan-digest", "has an invalid plan_sha256"),
        ("utc-format", "must be UTC YYYY-MM-DDTHH:MM:SSZ"),
        ("invalid-date", "is not a valid UTC time"),
        ("reversed-expiry", "expires before it was created"),
    ],
)
def test_campaign_init_refuses_corrupted_existing_deadline_without_replacing_it(
    git_repo: GitRepo, tmp_path: Path, defect, diagnosis
):
    config = _lane_config(git_repo)
    target = tmp_path / "deadline.json"
    args = _init_args(config, target)
    code, _, err = _invoke(args)
    assert code == Outcome.PASS.exit_code, err
    document = json.loads(target.read_text(encoding="utf-8"))
    if defect == "duplicate-key":
        raw = json.dumps(document)[:-1].encode() + b', "campaign": "another"}'
    elif defect == "non-utf8":
        raw = b"\xff"
    else:
        if defect == "unknown-field":
            document["unreviewed"] = True
        elif defect == "schema":
            document["schema"] = "other/1"
        elif defect == "campaign":
            document["campaign"] = "bad/name"
        elif defect == "commit":
            document["commit"] = "wrong"
        elif defect == "git_tree":
            document["git_tree"] = "wrong"
        elif defect == "lanes":
            document["lanes"] = ["package", "package"]
        elif defect == "missing-lane":
            document["lanes"] = ["other"]
            document["plan_sha256"] = {"other": None}
        elif defect == "assay_version":
            document["assay_version"] = ""
        elif defect == "wheel_sha256":
            document["wheel_sha256"] = "wrong"
        elif defect == "plan-keys":
            document["plan_sha256"] = {"other": None}
        elif defect == "plan-digest":
            document["plan_sha256"]["package"] = "wrong"
        elif defect == "utc-format":
            document["created_at_utc"] = "2026-10-07T12:00:00+00:00"
        elif defect == "invalid-date":
            document["created_at_utc"] = "2026-13-07T12:00:00Z"
        elif defect == "reversed-expiry":
            document["expires_at_utc"] = document["created_at_utc"]
        raw = json.dumps(document).encode("utf-8")
    target.write_bytes(raw)

    code, out, err = _invoke(args)
    assert code == Outcome.ERROR.exit_code
    assert diagnosis in err
    assert out == ""
    assert target.read_bytes() == raw


@pytest.mark.parametrize("record_kind", ["matching", "unbound", "malformed", "duplicate-key"])
def test_campaign_init_checks_existing_candidate_state_before_reuse(
    git_repo: GitRepo, tmp_path: Path, record_kind
):
    config = _lane_config(git_repo)
    target = tmp_path / "deadline.json"
    args = _init_args(config, target)
    code, _, err = _invoke(args)
    assert code == Outcome.PASS.exit_code, err
    original = target.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    record = state_dir / ("a" * 64 + ".json")
    if record_kind == "matching":
        record.write_text(json.dumps({"campaign_deadline_sha256": digest}), encoding="utf-8")
    elif record_kind == "unbound":
        record.write_text(json.dumps({"campaign_deadline_sha256": "b" * 64}), encoding="utf-8")
    elif record_kind == "malformed":
        record.write_bytes(b"\xff")
    else:
        record.write_text(
            '{"campaign_deadline_sha256":"%s","campaign_deadline_sha256":"%s"}' % (digest, digest),
            encoding="utf-8",
        )
    code, _, err = _invoke([*args, "--state-dir", str(state_dir)])
    if record_kind == "matching":
        assert code == Outcome.PASS.exit_code, err
    else:
        assert code == Outcome.ERROR.exit_code
        assert "not bound to the campaign deadline" in err
    assert target.read_bytes() == original


def test_campaign_init_refuses_non_directory_state_without_overwriting_deadline(
    git_repo: GitRepo, tmp_path: Path
):
    config = _lane_config(git_repo)
    target = tmp_path / "deadline.json"
    args = _init_args(config, target)
    code, _, err = _invoke(args)
    assert code == Outcome.PASS.exit_code, err
    original = target.read_bytes()
    state = tmp_path / "state-file"
    state.write_text("not a directory", encoding="utf-8")
    code, _, err = _invoke([*args, "--state-dir", str(state)])
    assert code == Outcome.ERROR.exit_code
    assert "is not a directory" in err
    assert target.read_bytes() == original
