"""ciu-P33 / CIU-60 — generated identity facts in the worktree overlay,
plus `ciu env print`.

Covers:

* O1 — `ciu env generate` upserts a six-key `[ciu.instance.generated]` table
  into `ciu.global.worktree.toml.j2`, from the SAME in-memory values it wrote
  into `ciu.env`, idempotently.
* O2 — hand-authored content elsewhere in that file survives BYTE FOR BYTE
  (this is the oracle a `tomllib` + `tomli_w` full-file round-trip fails).
* O3 — those facts reach templates through the EXISTING worktree-overlay merge
  in `render_global_chain`, with no bespoke Jinja context injection.
* O4 — the write fires for the PRIMARY/main checkout too (no S16 record, no
  pre-existing overlay), not only for worktree instances.
* O5 — `ciu env print` emits `export KEY='value'` lines and nothing else.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli  # noqa: E402
from ciu import workspace_env  # noqa: E402
from ciu.config_model import render_global_chain, render_toml_template  # noqa: E402

OVERLAY = "ciu.global.worktree.toml.j2"

FACTS = {
    "repo_name": "vbpub",
    "instance_id": "ab12cd",
    "network": "vbpub-ab12cd-network",
    "physical_repo_root": "/host/checkouts/vbpub",
    "repo_root": "/workspaces/vbpub",
    "public_fqdn": "example.test",
}


def _hermetic_generate(monkeypatch, repo_root: Path) -> Path:
    """`generate_ciu_env` with the host-fact detectors pinned.

    Only the DETECTORS are pinned — the write paths under test run for real.
    """
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root", lambda root: repo_root
    )
    monkeypatch.setattr(
        workspace_env,
        "_detect_public_fqdn",
        lambda root, require_fqdn: {
            "PUBLIC_IP": "203.0.113.7",
            "PUBLIC_FQDN": "example.test",
            "PUBLIC_TLS_CRT_PEM": "",
            "PUBLIC_TLS_KEY_PEM": "",
        },
    )
    monkeypatch.setattr(workspace_env, "_detect_docker_gid", lambda: "999")
    monkeypatch.setattr(workspace_env, "_detect_host_mdt_tmp", lambda: "")
    monkeypatch.setattr(
        workspace_env, "_detect_governance_read_iops", lambda: ("200", "fallback")
    )
    return workspace_env.generate_ciu_env(repo_root)


def _generated_table(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["ciu"]["instance"][
        "generated"
    ]


# ---------------------------------------------------------------------------
# O1 — the table is written, from the SAME values, idempotently
# ---------------------------------------------------------------------------


def test_generate_writes_the_six_generated_keys(monkeypatch, tmp_path):
    env_path = _hermetic_generate(monkeypatch, tmp_path)
    overlay = tmp_path / OVERLAY

    assert overlay.exists()
    table = _generated_table(overlay)
    assert sorted(table) == sorted(workspace_env.GENERATED_FACTS_KEYS)

    # Same in-memory tuple as ciu.env: assert the two records AGREE, which is
    # the property a second, independent derivation could not guarantee.
    env = workspace_env.parse_workspace_env(env_path)
    assert table["repo_name"] == env["REPO_NAME"]
    assert table["instance_id"] == env["INSTANCE_ID"]
    assert table["network"] == env["DOCKER_NETWORK_INTERNAL"]
    assert table["physical_repo_root"] == env["PHYSICAL_REPO_ROOT"]
    assert table["repo_root"] == env["REPO_ROOT"] == str(tmp_path)
    assert table["public_fqdn"] == env["PUBLIC_FQDN"] == "example.test"


def test_generate_never_re_reads_ciu_env_to_build_the_table(monkeypatch, tmp_path):
    """The facts come from memory, not from a read-back of the file just
    written: deleting ciu.env mid-flight cannot change the table."""
    calls: list[Path] = []
    real = workspace_env.parse_workspace_env

    def spy(path):
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(workspace_env, "parse_workspace_env", spy)
    _hermetic_generate(monkeypatch, tmp_path)
    assert calls == []


def test_second_generate_is_byte_identical(monkeypatch, tmp_path):
    _hermetic_generate(monkeypatch, tmp_path)
    first = (tmp_path / OVERLAY).read_text(encoding="utf-8")
    _hermetic_generate(monkeypatch, tmp_path)
    second = (tmp_path / OVERLAY).read_text(encoding="utf-8")

    assert second == first
    # Upsert, not append: exactly one table header, ever.
    assert second.count("[ciu.instance.generated]") == 1


def test_upsert_replaces_a_stale_value_without_duplicating_the_table(tmp_path):
    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    workspace_env.upsert_generated_facts(
        tmp_path, {**FACTS, "instance_id": "ff99ee"}
    )
    body = (tmp_path / OVERLAY).read_text(encoding="utf-8")

    assert body.count("[ciu.instance.generated]") == 1
    assert 'instance_id = "ab12cd"' not in body
    assert _generated_table(tmp_path / OVERLAY)["instance_id"] == "ff99ee"


def test_incomplete_facts_are_refused_by_name(tmp_path):
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.upsert_generated_facts(
            tmp_path, {k: v for k, v in FACTS.items() if k != "public_fqdn"}
        )
    assert "public_fqdn" in str(exc.value)
    assert not (tmp_path / OVERLAY).exists()


def test_block_key_order_is_fixed_not_mapping_order(tmp_path):
    """A caller's dict ordering must not move bytes in the file."""
    reversed_facts = dict(reversed(list(FACTS.items())))
    lines = workspace_env.render_generated_facts_block(reversed_facts)
    keys = [line.split(" = ")[0] for line in lines if " = " in line]
    assert tuple(keys) == workspace_env.GENERATED_FACTS_KEYS


def test_unreadable_overlay_is_reported_not_swallowed(tmp_path, monkeypatch):
    overlay = tmp_path / OVERLAY
    overlay.write_text("[ciu.instance]\n", encoding="utf-8")

    def boom(*_a, **_kw):
        raise OSError("EIO")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.upsert_generated_facts(tmp_path, FACTS)
    assert OVERLAY in str(exc.value)


def test_unwritable_overlay_is_reported_and_leaves_no_temp_file(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("ENOSPC")

    monkeypatch.setattr(Path, "open", boom)
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.upsert_generated_facts(tmp_path, FACTS)
    assert OVERLAY in str(exc.value)
    assert list(tmp_path.iterdir()) == []


def test_temp_file_is_cleaned_up_when_the_replace_fails(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("EXDEV")

    monkeypatch.setattr(workspace_env.os, "replace", boom)
    with pytest.raises(workspace_env.WorkspaceEnvError):
        workspace_env.upsert_generated_facts(tmp_path, FACTS)
    assert list(tmp_path.iterdir()) == []


def test_temp_file_unlink_failure_does_not_mask_the_write_error(
    tmp_path, monkeypatch
):
    """The reported failure is the WRITE failure, never the cleanup's."""
    monkeypatch.setattr(
        workspace_env.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("EXDEV"))
    )
    monkeypatch.setattr(
        Path, "unlink", lambda *a, **k: (_ for _ in ()).throw(OSError("EACCES"))
    )
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.upsert_generated_facts(tmp_path, FACTS)
    assert "EXDEV" in str(exc.value)


# ---------------------------------------------------------------------------
# O2 — everything else in the file survives byte for byte
# ---------------------------------------------------------------------------

HAND_AUTHORED = """\
# Operator notes: this checkout points at the staging reference.
[ciu.instance]
service_profiles = ["core", "db"]

[ciu.instance.shared_infra]
ref_path = "/repo/.worktrees/primary-ref"
network = "repo-ab12cd-network"
services = ["api", "worker"]
ref_projects = ["idp-dev-idp"]

# An ordinary sparse override, nothing to do with [ciu.instance].
[deploy]
some_key = "operator override"   # a trailing inline comment

# A hand-written comment introducing the last table.
[topology.services.vault]
internal_host = "dstdns-98535c-vault"
"""


def test_hand_authored_content_survives_byte_for_byte(tmp_path):
    overlay = tmp_path / OVERLAY
    overlay.write_text(HAND_AUTHORED, encoding="utf-8")

    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    body = overlay.read_text(encoding="utf-8")

    # The generated block was appended; every ORIGINAL byte is still there,
    # in order, contiguously — comments, blank lines, inline comment and all.
    assert body.startswith(HAND_AUTHORED)
    assert "[ciu.instance.generated]" in body
    for line in HAND_AUTHORED.splitlines():
        assert line in body.splitlines()


def test_content_before_between_and_after_the_block_all_survive(tmp_path):
    """The generated block placed MID-file, with hand-authored content on
    both sides of it and a hand-written comment immediately below it."""
    overlay = tmp_path / OVERLAY
    seeded = (
        "# leading operator comment\n"
        '[ciu.instance]\nservice_profiles = ["core"]\n'
        "\n"
        "[ciu.instance.generated]\n"
        'repo_name = "STALE"\n'
        "\n"
        "# a hand-written comment introducing the next table\n"
        '[deploy]\nsome_key = "operator override"\n'
        "\n"
        "# a trailing hand-written comment at EOF\n"
    )
    overlay.write_text(seeded, encoding="utf-8")

    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    body = overlay.read_text(encoding="utf-8")
    lines = body.splitlines()

    # Literal surrounding text, asserted as text — not "the values round-trip".
    assert lines[0] == "# leading operator comment"
    assert lines[1] == "[ciu.instance]"
    assert lines[2] == 'service_profiles = ["core"]'
    assert lines[3] == ""
    assert lines[4] == "[ciu.instance.generated]"
    idx = lines.index("# a hand-written comment introducing the next table")
    assert lines[idx - 1] == ""
    assert lines[idx + 1] == "[deploy]"
    assert lines[idx + 2] == 'some_key = "operator override"'
    assert lines[idx + 3] == ""
    assert lines[idx + 4] == "# a trailing hand-written comment at EOF"
    assert lines[idx + 4] == lines[-1]

    assert "STALE" not in body
    assert body.count("[ciu.instance.generated]") == 1

    # And a mid-file block is idempotent too.
    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    assert overlay.read_text(encoding="utf-8") == body


def test_a_full_toml_round_trip_would_have_failed_this(tmp_path):
    """Guard the NEGATIVE of O2 explicitly: comments are not TOML data, so a
    parse-then-dump implementation cannot reproduce this assertion."""
    overlay = tmp_path / OVERLAY
    overlay.write_text(HAND_AUTHORED, encoding="utf-8")
    workspace_env.upsert_generated_facts(tmp_path, FACTS)

    body = overlay.read_text(encoding="utf-8")
    assert "# Operator notes: this checkout points at the staging reference." in body
    assert "# An ordinary sparse override, nothing to do with [ciu.instance]." in body
    assert 'some_key = "operator override"   # a trailing inline comment' in body
    assert "# A hand-written comment introducing the last table." in body


def test_append_does_not_double_the_blank_separator(tmp_path):
    """An existing overlay already ending in a blank line gains exactly one
    separator, not two."""
    overlay = tmp_path / OVERLAY
    overlay.write_text("[ciu.instance]\nservice_profiles = []\n\n", encoding="utf-8")

    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    lines = overlay.read_text(encoding="utf-8").splitlines()

    assert lines[:2] == ["[ciu.instance]", "service_profiles = []"]
    assert lines[2] == ""
    assert lines[3] == "[ciu.instance.generated]"


def test_a_block_butted_against_the_next_table_gains_a_separator(tmp_path):
    """Hand-authored file with NO blank line between the generated table and
    the next one: the replace must not fuse the block onto that header."""
    overlay = tmp_path / OVERLAY
    overlay.write_text(
        '[ciu.instance.generated]\nrepo_name = "STALE"\n'
        '[deploy]\nsome_key = "operator override"\n',
        encoding="utf-8",
    )

    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    body = overlay.read_text(encoding="utf-8")
    lines = body.splitlines()

    assert lines[-4] == 'public_fqdn = "example.test"'
    assert lines[-3] == ""
    assert lines[-2:] == ["[deploy]", 'some_key = "operator override"']
    assert "STALE" not in body

    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    assert overlay.read_text(encoding="utf-8") == body


def test_a_fresh_file_gets_the_shared_overlay_header(tmp_path):
    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    body = (tmp_path / OVERLAY).read_text(encoding="utf-8")
    assert body.startswith(
        "# Worktree-local sparse global override (S3.1b / S16).\n"
    )
    assert body.endswith("\n")


def test_the_owned_block_names_itself_as_ciu_owned(tmp_path):
    """Mirrors CIU-52's 'do not hand-edit' precedent, INSIDE the block so it
    is not duplicated on the next upsert."""
    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    workspace_env.upsert_generated_facts(tmp_path, FACTS)
    body = (tmp_path / OVERLAY).read_text(encoding="utf-8")
    assert body.count("# Do NOT hand-edit keys in THIS table") == 1


# ---------------------------------------------------------------------------
# O3 — the facts reach templates through the EXISTING merge
# ---------------------------------------------------------------------------


def test_generated_facts_land_in_the_merged_global_config(monkeypatch, tmp_path):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    _hermetic_generate(monkeypatch, tmp_path)

    merged = render_global_chain(tmp_path, tmp_path)
    generated = merged["ciu"]["instance"]["generated"]
    assert generated["physical_repo_root"] == str(tmp_path)
    assert generated["repo_root"] == str(tmp_path)
    assert sorted(generated) == sorted(workspace_env.GENERATED_FACTS_KEYS)


def test_a_jinja_template_can_read_the_facts_like_any_other_value(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    _hermetic_generate(monkeypatch, tmp_path)
    merged = render_global_chain(tmp_path, tmp_path)

    stack = tmp_path / "stack"
    stack.mkdir()
    template = stack / "ciu.defaults.toml.j2"
    template.write_text(
        "[app]\n"
        'mount = "{{ ciu.instance.generated.physical_repo_root }}"\n'
        'net = "{{ ciu.instance.generated.network }}"\n',
        encoding="utf-8",
    )
    rendered = render_toml_template(template, merged, environ={})
    assert rendered["app"]["mount"] == str(tmp_path)
    assert rendered["app"]["net"].endswith("-network")


def test_the_facts_are_not_injected_as_a_bespoke_context_field(
    monkeypatch, tmp_path
):
    """Negative of O3: with the OVERLAY FILE removed, the facts are gone. If
    some code path were injecting them into the Jinja context directly, they
    would survive the file's absence — that is the hazard this design rejects.
    """
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / OVERLAY).unlink()

    merged = render_global_chain(tmp_path, tmp_path)
    assert "generated" not in merged.get("ciu", {}).get("instance", {})


# ---------------------------------------------------------------------------
# O4 — the PRIMARY/main checkout, not just worktree instances
# ---------------------------------------------------------------------------


def test_primary_checkout_with_no_instance_record_is_covered(monkeypatch, tmp_path):
    """No S16 lifecycle record anywhere, no pre-existing overlay: this is the
    workspace the operator was standing in when they hit the original bug."""
    from ciu import worktree

    assert worktree.read_own_instance_record(tmp_path) is None
    assert not (tmp_path / OVERLAY).exists()

    _hermetic_generate(monkeypatch, tmp_path)

    assert (tmp_path / OVERLAY).exists()
    assert _generated_table(tmp_path / OVERLAY)["repo_root"] == str(tmp_path)


def test_write_does_not_consult_the_instance_record_at_all(monkeypatch, tmp_path):
    """Negative of O4: gating on `find_instance_record(...) is not None` would
    show up as a call. There is none."""
    from ciu import worktree

    seen: list[object] = []

    def spy(*args, **kwargs):
        seen.append(args)
        return None

    monkeypatch.setattr(worktree, "read_own_instance_record", spy)
    _hermetic_generate(monkeypatch, tmp_path)
    assert seen == []
    assert (tmp_path / OVERLAY).exists()


def test_a_worktree_instance_shaped_overlay_is_extended_not_replaced(
    monkeypatch, tmp_path
):
    """The other half of O4: a worktree instance whose overlay CIU-52 already
    wrote keeps every one of those values."""
    overlay = tmp_path / OVERLAY
    overlay.write_text(HAND_AUTHORED, encoding="utf-8")

    _hermetic_generate(monkeypatch, tmp_path)

    parsed = tomllib.loads(overlay.read_text(encoding="utf-8"))
    instance = parsed["ciu"]["instance"]
    assert instance["service_profiles"] == ["core", "db"]
    assert instance["shared_infra"]["ref_projects"] == ["idp-dev-idp"]
    assert sorted(instance["generated"]) == sorted(
        workspace_env.GENERATED_FACTS_KEYS
    )


# ---------------------------------------------------------------------------
# O5 — `ciu env print`
# ---------------------------------------------------------------------------


def _run_env_print(monkeypatch, tmp_path, argv=()):
    monkeypatch.chdir(tmp_path)
    return cli._env_print(list(argv))


def test_env_print_emits_export_lines_and_nothing_else(
    monkeypatch, tmp_path, capsys
):
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")

    rc = _run_env_print(monkeypatch, tmp_path)
    out = capsys.readouterr()

    assert rc == 0
    assert out.err == ""
    lines = out.out.splitlines()
    assert lines
    assert all(line.startswith("export ") for line in lines)
    # Every value is single-quoted — one shape, eyeball-able.
    for line in lines:
        assert line.split("=", 1)[1].startswith("'")
        assert line.endswith("'")
    for key in workspace_env.REQUIRED_KEYS_CORE:
        assert any(line.startswith(f"export {key}=") for line in lines)


def test_env_print_has_no_side_effects(monkeypatch, tmp_path, capsys):
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    _run_env_print(monkeypatch, tmp_path)
    capsys.readouterr()

    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert after == before


def test_env_print_refuses_loudly_when_ciu_env_is_missing(
    monkeypatch, tmp_path, capsys
):
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    rc = _run_env_print(monkeypatch, tmp_path)
    out = capsys.readouterr()

    assert rc == 1
    assert out.out == ""
    assert "ciu env generate" in out.err


def test_env_print_reports_a_bad_define_root(monkeypatch, tmp_path, capsys):
    rc = _run_env_print(
        monkeypatch, tmp_path, ["--define-root", str(tmp_path / "nope")]
    )
    out = capsys.readouterr()
    assert rc == 1
    assert "[ERROR]" in out.err


def test_env_print_honours_define_root(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _hermetic_generate(monkeypatch, repo)

    monkeypatch.chdir(elsewhere)
    rc = cli._env_print(["--define-root", str(repo)])
    out = capsys.readouterr()

    assert rc == 0
    assert f"export REPO_ROOT='{repo}'" in out.out


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plain", "'plain'"),
        ("", "''"),
        ("with space", "'with space'"),
        ("it's", "'it'\"'\"'s'"),
        ("$(rm -rf /)", "'$(rm -rf /)'"),
    ],
)
def test_shell_export_value_quoting(raw, expected):
    assert cli._shell_export_value(raw) == expected


def test_eval_of_env_print_populates_a_real_shell(monkeypatch, tmp_path):
    """O5's end: `eval "$(ciu env print)"` really does set the core keys."""
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    # An awkward value that only survives correct quoting.
    env_file = tmp_path / "ciu.env"
    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        + "export CIU_P33_PROBE=\"it's a $(nasty) value\"\n",
        encoding="utf-8",
    )

    src = str(Path(__file__).resolve().parents[2] / "src")
    runner = tmp_path / "print_env.py"
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {src!r})\n"
        "from ciu import cli\n"
        "sys.exit(cli._env_print([]))\n",
        encoding="utf-8",
    )
    script = (
        f'eval "$({shlex.quote(sys.executable)} {shlex.quote(str(runner))})" && '
        'printf "%s\\n%s\\n" "$DOCKER_NETWORK_INTERNAL" "$CIU_P33_PROBE"'
    )
    res = subprocess.run(
        ["bash", "-c", script], cwd=str(tmp_path), capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr
    network, probe = res.stdout.splitlines()
    assert network.endswith("-network")
    assert probe == "it's a $(nasty) value"


def test_env_print_help_is_reachable_and_names_eval(capsys):
    assert cli._wants_verb_help("env", ["print", "--help"]) is False
    with pytest.raises(SystemExit) as exc:
        cli._env_print(["--help"])
    assert exc.value.code == 0
    assert "--define-root" in capsys.readouterr().out


def test_ciu_env_print_is_reachable_from_the_public_dispatcher(
    monkeypatch, tmp_path, capsys
):
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ciu", "env", "print"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    assert "export REPO_ROOT=" in capsys.readouterr().out


def test_env_verb_help_text_names_print_and_is_honest_about_eval():
    text = cli._VERB_HELP["env"]
    assert "ciu env print" in text
    assert 'eval "$(ciu env print)"' in text
    # O5's negative: never documented as applying/sourcing into this shell.
    assert "ciu env apply" not in text
    assert "ciu env source" not in text
