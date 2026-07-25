"""Exact residual coverage for kill validation and Docker owner safety.

Every Docker-facing path uses an injected inspect seam. No test invokes Docker,
systemd, a signal, or another host mutation.
"""

from __future__ import annotations

import pytest

from topos.actions.catalog import DOCKER_EXECUTABLE
from topos.actions.kill_ops import (
    build_kill_argv,
    build_kill_preview,
    render_kill_preview,
    validate_signal,
)
from topos.actions.owner_safety import (
    OwnerDetection,
    OwnerSafetyRefusal,
    _owner_message,
    detect_owner,
    evaluate,
)


FULL_ID = "0123456789abcdef" * 4


@pytest.mark.parametrize("signal", [None, ""])
def test_signal_must_be_a_nonempty_string(signal: object) -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_signal(signal)  # type: ignore[arg-type]
    assert str(exc_info.value) == "signal must be a non-empty string"


def test_unknown_sig_prefixed_signal_has_exact_refusal() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_signal("SIGSTOP")
    assert str(exc_info.value) == "signal must not include SIG prefix: 'SIGSTOP'"


@pytest.mark.parametrize("target", [None, ""])
def test_kill_target_must_be_a_nonempty_string(target: object) -> None:
    from topos.actions.catalog import ActionKind

    with pytest.raises(ValueError) as exc_info:
        build_kill_argv(ActionKind.DOCKER_KILL, target, "TERM")  # type: ignore[arg-type]
    assert str(exc_info.value) == "target must be a non-empty string"


def test_build_kill_argv_rejects_a_nonkill_action_kind() -> None:
    from topos.actions.catalog import ActionKind

    with pytest.raises(ValueError) as exc_info:
        build_kill_argv(ActionKind.DOCKER_STOP, "worker", "TERM")
    assert str(exc_info.value) == "invalid kill kind: ActionKind.DOCKER_STOP"


def test_build_kill_preview_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError) as exc_info:
        build_kill_preview("pod-kill", "worker", "TERM")
    assert str(exc_info.value) == "invalid kill kind: 'pod-kill'"


def test_render_kill_preview_includes_the_exact_force_warning() -> None:
    plan = build_kill_preview("docker-kill", "worker", "KILL", force=True)
    assert render_kill_preview(plan) == "\n".join(
        [
            "Action: docker-kill",
            "Target: worker",
            "Signal: KILL",
            "WARNING: KILL signal causes data loss",
            "Force: yes",
            f"Command argv: {[DOCKER_EXECUTABLE, 'kill', '--signal', 'KILL', 'worker']}",
            "Description: Send a signal to a container or systemd unit.",
            "Mode: preview only; no command was executed",
        ]
    )


def _evaluate(raw: object, *, protected: tuple[str, ...] = ()) -> OwnerSafetyRefusal | None:
    calls: list[str] = []

    def inspect(target: str) -> object:
        calls.append(target)
        return raw

    result = evaluate(
        "docker-stop",
        "accepted-target",
        inspect=inspect,
        protected_services=protected,
    )
    assert calls == ["accepted-target"]
    return result


def test_nonstring_owner_detail_is_discarded_exactly() -> None:
    assert detect_owner(
        {
            "ciu.managed": "true",
            "ciu.stack": 42,  # type: ignore[dict-item]
        }
    ) == OwnerDetection(owner="ciu", ambiguous=False, detail="")


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, None),
        ({"Labels": None}, None),
        (
            {"Labels": {"ignored": object(), "com.docker.compose.project": "fleet"}},
            OwnerSafetyRefusal(
                reason="owner-managed",
                message=(
                    "container is managed by Docker Compose; "
                    "use 'docker compose -p fleet stop' instead of raw docker stop"
                ),
            ),
        ),
        (
            "malformed",
            OwnerSafetyRefusal(
                reason="inspect-failed",
                message=(
                    "could not establish the identity of container 'accepted-target' "
                    "from docker inspect; refusing the mutation "
                    "(no name-only fallback)"
                ),
            ),
        ),
        (
            {"Labels": []},
            OwnerSafetyRefusal(
                reason="inspect-failed",
                message=(
                    "could not establish the identity of container 'accepted-target' "
                    "from docker inspect; refusing the mutation "
                    "(no name-only fallback)"
                ),
            ),
        ),
    ],
)
def test_config_and_label_shapes_have_exact_verdicts(
    config: object,
    expected: OwnerSafetyRefusal | None,
) -> None:
    raw = [{"Id": FULL_ID, "Name": "/worker", "Config": config}]
    assert _evaluate(raw) == expected


def test_compose_with_no_safe_display_detail_uses_project_instruction() -> None:
    raw = [
        {
            "Id": FULL_ID,
            "Name": "/worker",
            "Config": {"Labels": {"com.docker.compose.project": "!!!"}},
        }
    ]
    assert _evaluate(raw) == OwnerSafetyRefusal(
        reason="owner-managed",
        message=(
            "container is managed by Docker Compose; "
            "use 'docker compose' in the project instead of raw docker stop"
        ),
    )


def test_defensive_unknown_owner_message_is_exact() -> None:
    assert _owner_message(
        "docker-restart",
        OwnerDetection(owner="future-owner", ambiguous=False),
    ) == "container is owner-managed; refusing raw docker restart"


def test_nameless_identity_is_matched_by_canonical_id() -> None:
    raw = [{"Id": FULL_ID, "Name": None, "Config": {"Labels": {}}}]
    assert _evaluate(raw, protected=(FULL_ID,)) == OwnerSafetyRefusal(
        reason="protected",
        message=(
            "container 'accepted-target' resolves to a protected service; "
            "refusing the mutation (matched by canonical id/name)"
        ),
    )


def test_default_owner_inspect_delegates_once_and_returns_payload(monkeypatch) -> None:
    from topos.actions import owner_safety
    from topos.collect import dockerjoin

    expected = [{"Id": FULL_ID, "Name": "/worker"}]
    calls: list[str] = []

    def inspect(target: str) -> object:
        calls.append(target)
        return expected

    monkeypatch.setattr(dockerjoin, "default_docker_inspect", inspect)

    assert owner_safety.default_owner_inspect("accepted-target") is expected
    assert calls == ["accepted-target"]
