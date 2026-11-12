"""Contracts and hostile-input guards for the ``Pam_env`` overlay helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import Edit, Request, apply, augtool, plan
from augeaswrap.formats.pam_env import (
    environment_item,
    environment_op,
    pam_env_item,
    pam_env_op,
)


def hand_environment(**changes):
    item = {"kind": "environment", "name": "SITE_MODE", "value": "production"}
    item.update(changes)
    return item


def hand_conf(**changes):
    item = {"kind": "conf", "name": "REMOTEHOST", "default": "localhost"}
    item.update(changes)
    return item


def environment_tree(rows):
    base = "/files/etc/environment"
    lines = []
    for label, fields in rows:
        for name, value in fields.items():
            lines.append(f"{base}/{label}/{name} = {json.dumps(value)}")
    return "\n".join(lines) + ("\n" if lines else "")


def environment_state(rows):
    base = "/files/etc/environment"
    matches = {
        base + "/*[kind='environment']": tuple(
            f"{base}/{label}" for label, fields in rows
            if fields.get("kind") == "environment"
        )
    }
    return environment_tree(rows), matches


def conf_tree(rows):
    base = "/files/etc/security/pam_env.conf"
    lines = []
    for label, fields in rows:
        for name, value in fields.items():
            lines.append(f"{base}/{label}/{name} = {json.dumps(value)}")
    return "\n".join(lines) + ("\n" if lines else "")


def conf_state(rows):
    base = "/files/etc/security/pam_env.conf"
    matches = {
        base + "/*[kind='conf']": tuple(
            f"{base}/{label}" for label, fields in rows if fields.get("kind") == "conf"
        )
    }
    return conf_tree(rows), matches


class ScriptedAugtool:
    """Return a fresh parsed tree after each staged helper mutation."""

    def __init__(self, states, mutations):
        self.states = states
        self.mutations = list(mutations)
        self.calls = []

    def __call__(self, *, path, lens, source, script, save_new=False):
        self.calls.append((source, script, save_new))
        if save_new:
            if not self.mutations:
                raise AssertionError("unexpected augtool mutation")
            return augtool.AugtoolOutput("", "", self.mutations.pop(0))
        if 'print "/files' in script:
            tree = self.states[source][0]
            return augtool.AugtoolOutput(tree, "")
        query = next((line[6:] for line in script.splitlines() if line.startswith("match ")), None)
        if query is None:
            raise AssertionError(f"unexpected augtool script: {script!r}")
        expression = json.loads(query)
        matches = self.states[source][1].get(expression, ())
        output = "\n".join(matches) + ("\n" if matches else "")
        return augtool.AugtoolOutput(output, "")


def use_scripted_augtool(monkeypatch, runner):
    monkeypatch.setattr(augtool.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(augtool, "run", runner)


def fake_root(tmp_path, content):
    root = tmp_path / "root"
    target = root / "etc/environment"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    return root


def fake_conf_root(tmp_path, content):
    root = tmp_path / "root"
    target = root / "etc/security/pam_env.conf"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    return root


def make_edit(ops):
    return Edit(
        file="/etc/environment",
        lens="Pam_env",
        state="present",
        ops=ops,
        create=False,
        on_change="none",
    )


def make_conf_edit(ops):
    return Edit(
        file="/etc/security/pam_env.conf",
        lens="Pam_env",
        state="present",
        ops=ops,
        create=False,
        on_change="none",
    )


def diff_once(resource, root):
    ctx = SimpleNamespace(root=root)
    return resource.diff(ctx, resource.observe(ctx))[0]


def mutation_scripts(runner):
    return [script for _source, script, save_new in runner.calls if save_new]


def test_environment_helpers_keep_lens_order_and_safe_value_encodings():
    first = environment_item("SITE_MODE", "production")
    second = environment_item("GREETING", "hello world")
    third = environment_item("CSV_VALUES", "one,two,three")
    fourth = environment_item("QUOTED", 'say "hello"')
    fifth = environment_item("SINGLE", "can't stop")
    sixth = environment_item("EMPTY", "")
    seventh = environment_item("EQUALS", "a=b")
    operation = environment_op([first, second, third, fourth, fifth, sixth, seventh])

    assert list(first.items()) == [
        ("kind", "environment"),
        ("name", "SITE_MODE"),
        ("value", "production"),
    ]
    assert operation == {
        "records": "*[kind='environment']",
        "key": "name",
        "exclusive": False,
        "items": [
            first,
            {"kind": "environment", "name": "GREETING", "value": '"hello world"'},
            third,
            {"kind": "environment", "name": "QUOTED", "value": "'say \"hello\"'"},
            {"kind": "environment", "name": "SINGLE", "value": "\"can't stop\""},
            {"kind": "environment", "name": "EMPTY", "value": '""'},
            {"kind": "environment", "name": "EQUALS", "value": '"a=b"'},
        ],
    }
    assert environment_op([])["items"] == []


def test_pam_env_helpers_keep_optional_field_order_and_quote_safely():
    default = pam_env_item("REMOTEHOST", default="localhost", override="@{PAM_RHOST}")
    override_only = pam_env_item("DISPLAY", override="${DISPLAY}")
    comma = pam_env_item("LIST", default="a,b,c")
    spaced = pam_env_item("LESS", default="M q e h15 z23 b80")
    equals = pam_env_item("URL", default="a=b")
    operation = pam_env_op([default, override_only, comma, spaced, equals])

    assert list(default.items()) == [
        ("kind", "conf"),
        ("name", "REMOTEHOST"),
        ("default", "localhost"),
        ("override", "@{PAM_RHOST}"),
    ]
    assert list(override_only.items()) == [
        ("kind", "conf"),
        ("name", "DISPLAY"),
        ("override", "${DISPLAY}"),
    ]
    assert operation == {
        "records": "*[kind='conf']",
        "key": "name",
        "exclusive": False,
        "items": [
            default,
            override_only,
            comma,
            {"kind": "conf", "name": "LESS", "default": '"M q e h15 z23 b80"'},
            {"kind": "conf", "name": "URL", "default": '"a=b"'},
        ],
    }
    assert pam_env_op([])["items"] == []


def test_pam_env_conf_helpers_treat_values_as_raw_linux_pam_expressions():
    supported = pam_env_item(
        "ESCAPED",
        default=r"\$literal",
        override=r"\@literal and \\slash",
    )
    operation = pam_env_op([supported])
    assert operation["items"][0]["default"] == r"\$literal"
    assert operation["items"][0]["override"] == r'"\@literal and \\slash"'

    # /etc/environment has no Linux-PAM expansion pass, so its backslashes are
    # ordinary literal bytes.
    environment = environment_item("WINDOWS_PATH", r"C:\tools")
    assert environment_op([environment])["items"] == [environment]


@pytest.mark.parametrize("value", [r"C:\tools", r"\q", "\\"])
def test_pam_env_conf_helpers_refuse_unsupported_backslash_sequences(value):
    with pytest.raises(ValueError, match="unsupported backslash escape"):
        pam_env_item("UNSAFE", default=value)
    with pytest.raises(ValueError, match="unsupported backslash escape"):
        pam_env_op([hand_conf(default=value)])


@pytest.mark.parametrize("value", ["line\nbreak", "carriage\rreturn", "nul\x00byte"])
def test_constructors_refuse_line_structure_values(value):
    with pytest.raises(ValueError, match="NUL, newline"):
        environment_item("SAFE_NAME", value)
    with pytest.raises(ValueError, match="NUL, newline"):
        pam_env_item("SAFE_NAME", default=value)


def test_constructors_refuse_names_and_unrepresentable_quotes():
    with pytest.raises(ValueError, match="letters, digits"):
        environment_item("not/a/name", "value")
    with pytest.raises(ValueError, match="letters, digits"):
        pam_env_item("name with space")
    with pytest.raises(ValueError, match="both quote characters"):
        environment_op([hand_environment(value='both "double" and \'single\'')])
    with pytest.raises(ValueError, match="double quote"):
        pam_env_op([hand_conf(default='say "hello"')])
    with pytest.raises(ValueError, match="must not contain #"):
        environment_op([hand_environment(value="before#comment")])
    with pytest.raises(ValueError, match="unsupported whitespace"):
        pam_env_op([hand_conf(default="vertical\vtab")])


@pytest.mark.parametrize("kind", ["environment", "conf"])
@pytest.mark.parametrize("shape", ["missing", "extra", "wrong-order", "wrong-kind"])
def test_wrappers_revalidate_hand_built_mapping_shape(kind, shape):
    item = hand_environment() if kind == "environment" else hand_conf()
    wrapper = environment_op if kind == "environment" else pam_env_op
    if shape == "missing":
        item.pop("name")
    elif shape == "extra":
        item["evil"] = "value"
    elif shape == "wrong-order":
        item = dict(reversed(list(item.items())))
    else:
        item["kind"] = "other"

    expected = "kind must" if shape == "wrong-kind" else "fields must be"
    with pytest.raises(ValueError, match=expected):
        wrapper([item])


@pytest.mark.parametrize("wrapper", [environment_op, pam_env_op])
def test_wrappers_refuse_bad_sequences_duplicates_and_non_mappings(wrapper):
    kind = "environment" if wrapper is environment_op else "conf"
    item = hand_environment() if kind == "environment" else hand_conf()
    with pytest.raises(ValueError, match="must be a sequence"):
        wrapper("not a sequence")
    with pytest.raises(ValueError, match="must be mappings"):
        wrapper([None])
    with pytest.raises(ValueError, match="duplicate pam_env"):
        wrapper([item, dict(item)])


@pytest.mark.parametrize(
    ("wrapper", "field", "value"),
    [
        (environment_op, "name", "bad-name"),
        (environment_op, "name", ""),
        (environment_op, "value", 1),
        (pam_env_op, "name", "bad-name"),
        (pam_env_op, "default", 1),
        (pam_env_op, "override", 1),
    ],
)
def test_wrappers_revalidate_hand_built_names_and_value_types(wrapper, field, value):
    item = hand_environment() if wrapper is environment_op else hand_conf()
    item[field] = value
    with pytest.raises(ValueError):
        wrapper([item])


def test_environment_op_append_reobserves_before_second_sequence_path(monkeypatch, tmp_path):
    source = b"# kept\n"
    after_first = b"one environment record\n"
    final = b"two environment records\n"
    first = {"kind": "environment", "name": "ONE", "value": "first"}
    second = {"kind": "environment", "name": "TWO", "value": '"hello world"'}
    runner = ScriptedAugtool(
        {
            source: environment_state([]),
            after_first: environment_state([("1", first)]),
            final: environment_state(
                [("1", first), ("2", second)]
            ),
        },
        [after_first, final],
    )
    use_scripted_augtool(monkeypatch, runner)
    operation = environment_op(
        [
            environment_item("ONE", "first"),
            environment_item("TWO", "hello world"),
        ]
    )
    change = diff_once(make_edit([operation]), fake_root(tmp_path, source))

    assert mutation_scripts(runner) == [
        'load\nset "/files/etc/environment/01[1]/kind" "environment"\n'
        'set "/files/etc/environment/01[1]/name" "ONE"\n'
        'set "/files/etc/environment/01[1]/value" "first"\n'
        "print /augeas//error\nsave\nprint /augeas//error\n",
        'load\nins 01 after "/files/etc/environment/1"\n'
        'set "/files/etc/environment/01[1]/kind" "environment"\n'
        'set "/files/etc/environment/01[1]/name" "TWO"\n'
        'set "/files/etc/environment/01[1]/value" "\\"hello world\\""\n'
        "print /augeas//error\nsave\nprint /augeas//error\n",
    ]
    assert change.after["sha256"] == hashlib.sha256(final).hexdigest()


def test_conf_operation_preserves_raw_escapes_in_generated_augtool_value(monkeypatch, tmp_path):
    source = b"# kept\n"
    final = b"PATH DEFAULT=C:\\\\tools\n"
    first = {"kind": "conf", "name": "PATH", "default": r"C:\\tools"}
    runner = ScriptedAugtool(
        {
            source: conf_state([]),
            final: conf_state([("1", first)]),
        },
        [final],
    )
    use_scripted_augtool(monkeypatch, runner)
    change = diff_once(
        make_conf_edit([pam_env_op([pam_env_item("PATH", default=r"C:\\tools")])]),
        fake_conf_root(tmp_path, source),
    )

    script = mutation_scripts(runner)[0]
    assert (
        r'set "/files/etc/security/pam_env.conf/01[1]/default" "C:\\\\tools"'
        in script
    )
    assert change.after["sha256"] == hashlib.sha256(final).hexdigest()


def test_edit_plan_and_apply_use_the_pam_env_overlay_lens(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"OLD=value\n")
    input_item = environment_item("SITE_MODE", "production")
    output = b"OLD=value\nSITE_MODE=production\n"
    runner = ScriptedAugtool(
        {
            b"OLD=value\n": environment_state(
                [("1", {"kind": "environment", "name": "OLD", "value": "value"})]
            ),
            output: environment_state(
                [
                    ("1", {"kind": "environment", "name": "OLD", "value": "value"}),
                    ("2", {"kind": "environment", "name": "SITE_MODE", "value": "production"}),
                ]
            ),
        },
        [output, output, output],
    )
    use_scripted_augtool(monkeypatch, runner)
    resource = Edit(
        file="/etc/environment",
        lens="Pam_env",
        state="present",
        ops=[environment_op([input_item])],
        create=False,
        on_change="none",
    )
    request = Request("pam env test 1.0", (resource,))
    planned = plan(request, root=root)
    assert planned.has_changes
    assert planned.changes[0].resource.lens == "Pam_env"

    apply(planned)
    assert (root / "etc/environment").read_bytes() == output
