"""Tests for target resolution and membership tracking.

Everything here runs against the fake cgroup tree from ``conftest.py`` — never
the live host. The cases that matter are the ones a gate actually produces: a
slice whose children appear after the profiler starts, and a container id that
must be located without asking the Docker daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import targets as t
from tests.conftest import cgroup_files, write_cgroup


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    """Never let a bare-name spec fall through to the real docker daemon.

    ``parse_target``'s no-scheme inference tries ``container:`` first when
    ``docker_bin()`` is truthy (DESIGN.md hard rule: never call the real
    docker binary from a test). This host happens to have a real docker CLI
    on PATH, so without this every test that resolves a bare name (e.g.
    ``"dev-background.slice"``) would issue a real `docker inspect` against
    the live daemon. Individual tests that want to exercise the
    docker-present branches re-patch ``t.docker_bin`` locally.
    """
    monkeypatch.setattr(t, "docker_bin", lambda: None)


class TestSliceNaming:
    def test_nested_slice_expands_to_every_ancestor(self):
        # systemd encodes the hierarchy in the unit name; every '-' prefix is a
        # real parent cgroup that must exist in the path.
        assert t.slice_to_path("dev-background.slice") == "/dev.slice/dev-background.slice"

    def test_three_levels(self):
        assert t.slice_to_path("a-b-c.slice") == "/a.slice/a-b.slice/a-b-c.slice"

    def test_suffix_is_optional(self):
        assert t.slice_to_path("dev-background") == "/dev.slice/dev-background.slice"

    def test_root_slice(self):
        assert t.slice_to_path("-.slice") == "/"

    def test_round_trip(self):
        assert t.path_to_slice("/dev.slice/dev-background.slice") == "dev-background.slice"
        assert t.path_to_slice("/dev.slice/docker-x.scope") is None

    def test_bare_dash_slice_stem_is_rejected(self):
        # "-.slice" is handled specially above; a name that reduces to an
        # empty stem any other way (nothing before ".slice") is not usable.
        with pytest.raises(t.TargetError):
            t.slice_to_path(".slice")


class TestSplitSpecAndOptions:
    def test_unrecognized_scheme_is_treated_as_no_scheme(self):
        # A colon that isn't one of the known schemes (e.g. a Windows-style
        # path, or a typo) must fall back to the whole string as the value,
        # not be silently swallowed as a scheme.
        assert t._split_spec("http://example") == ("", "http://example")

    def test_no_colon_at_all(self):
        assert t._split_spec("dev-background.slice") == ("", "dev-background.slice")

    def test_options_skip_an_empty_item_from_a_stray_comma(self):
        value, options = t._parse_options("dev.slice@follow,,as=x")
        assert value == "dev.slice"
        assert options == {"follow": "1", "as": "x"}


class TestDockerLookups:
    """resolve_container / resolve_label, with docker mocked at the
    subprocess boundary — never the real daemon (DESIGN.md hard rule)."""

    def test_resolve_container_success(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(
            t, "_docker_json",
            lambda *args: [{"Id": "a" * 64, "Name": "/my-container"}],
        )
        cid, name = t.resolve_container("my-container")
        assert cid == "a" * 64
        assert name == "my-container"  # leading "/" stripped

    def test_resolve_container_falls_back_to_the_ref_when_name_is_absent(self, monkeypatch):
        monkeypatch.setattr(t, "_docker_json", lambda *args: [{"Id": "b" * 64}])
        cid, name = t.resolve_container("b" * 12)
        assert name == "b" * 12

    def test_resolve_container_no_match_raises(self, monkeypatch):
        monkeypatch.setattr(t, "_docker_json", lambda *args: None)
        with pytest.raises(t.TargetError, match="no container matches"):
            t.resolve_container("nope")

    def test_docker_json_returns_none_when_docker_missing(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: None)
        assert t._docker_json("inspect", "x") is None

    def test_docker_json_returns_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(
            t.subprocess, "run",
            lambda *a, **k: subprocess_completed(returncode=1, stdout=""),
        )
        assert t._docker_json("inspect", "x") is None

    def test_docker_json_returns_none_on_invalid_json(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(
            t.subprocess, "run",
            lambda *a, **k: subprocess_completed(returncode=0, stdout="not json"),
        )
        assert t._docker_json("inspect", "x") is None

    def test_resolve_label_needs_docker(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: None)
        with pytest.raises(t.TargetError, match="docker CLI not found"):
            t.resolve_label("app=gate")

    def test_resolve_label_lists_matching_containers_and_skips_blank_lines(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(
            t.subprocess, "run",
            lambda *a, **k: subprocess_completed(
                returncode=0,
                stdout="\n" + ("a" * 64) + "\tfirst\n" + ("b" * 64) + "\tsecond\n",
            ),
        )
        monkeypatch.setattr(t, "resolve_container", lambda cid: (cid, f"resolved-{cid[:4]}"))
        out = t.resolve_label("app=gate")
        assert out == [("a" * 64, "resolved-aaaa"), ("b" * 64, "resolved-bbbb")]

    def test_resolve_label_falls_back_to_ps_name_when_inspect_name_is_empty(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(
            t.subprocess, "run",
            lambda *a, **k: subprocess_completed(returncode=0, stdout=("c" * 64) + "\tps-name\n"),
        )
        monkeypatch.setattr(t, "resolve_container", lambda cid: (cid, ""))
        out = t.resolve_label("app=gate")
        assert out == [("c" * 64, "ps-name")]

    def test_resolve_label_no_matches_raises(self, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(t.subprocess, "run", lambda *a, **k: subprocess_completed(0, ""))
        with pytest.raises(t.TargetError, match="no running container carries label"):
            t.resolve_label("app=nope")


def subprocess_completed(returncode: int, stdout: str):
    import subprocess as _sp

    return _sp.CompletedProcess(args=["docker"], returncode=returncode, stdout=stdout, stderr="")


class TestParseTarget:
    def test_cgroup_scheme(self, cgroup_root: Path):
        resolved = t.parse_target("cgroup:/dev.slice/dev-background.slice", str(cgroup_root))
        assert len(resolved) == 1
        assert resolved[0].cgroup == "/dev.slice/dev-background.slice"
        assert resolved[0].kind == "cgroup"

    def test_slice_scheme(self, cgroup_root: Path):
        resolved = t.parse_target("slice:dev-background.slice", str(cgroup_root))
        assert resolved[0].cgroup == "/dev.slice/dev-background.slice"

    def test_bare_slice_name_is_inferred(self, cgroup_root: Path):
        resolved = t.parse_target("dev-background.slice", str(cgroup_root))
        assert resolved[0].cgroup == "/dev.slice/dev-background.slice"

    def test_containerid_needs_no_docker(self, cgroup_root: Path):
        # This is the form the outer process hands the helper, which has the
        # host cgroup tree but no Docker socket.
        resolved = t.parse_target("containerid:" + "a" * 64, str(cgroup_root))
        assert resolved[0].cgroup.endswith("docker-" + "a" * 64 + ".scope")
        assert resolved[0].container_id == "a" * 64

    def test_containerid_rejects_a_name(self, cgroup_root: Path):
        with pytest.raises(t.TargetError):
            t.parse_target("containerid:my-container", str(cgroup_root))

    def test_pid_scheme(self, cgroup_root: Path, proc_root: Path, monkeypatch):
        monkeypatch.setattr(t.util, "read_text", lambda p: (
            "0::/dev.slice/dev-background.slice" if p.endswith("/cgroup") else "fakeproc"
        ))
        resolved = t.parse_target("pid:4242", str(cgroup_root))
        assert resolved[0].cgroup == "/dev.slice/dev-background.slice"
        assert resolved[0].pid == 4242

    def test_pid_scheme_rejects_a_non_numeric_pid(self, cgroup_root: Path):
        with pytest.raises(t.TargetError, match="needs a number"):
            t.parse_target("pid:not-a-pid", str(cgroup_root))

    def test_pid_scheme_raises_when_the_pid_has_no_readable_cgroup(self, cgroup_root: Path, monkeypatch):
        # The process already exited between being named and being resolved.
        monkeypatch.setattr(t, "cgroup_of_pid", lambda pid, root: None)
        with pytest.raises(t.TargetError, match="already exited"):
            t.parse_target("pid:99999", str(cgroup_root))

    def test_self_scheme_bare(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "cgroup_of_pid", lambda pid, root: "/dev.slice/dev-background.slice")
        resolved = t.parse_target("self", str(cgroup_root))
        assert resolved[0].kind == "self"
        assert resolved[0].cgroup == "/dev.slice/dev-background.slice"

    def test_self_scheme_explicit(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "cgroup_of_pid", lambda pid, root: "/wings.slice")
        resolved = t.parse_target("self:", str(cgroup_root))
        assert resolved[0].cgroup == "/wings.slice"

    def test_self_scheme_raises_at_the_namespace_root(self, cgroup_root: Path, monkeypatch):
        # A private cgroup namespace reads its own processes back as "/" —
        # correct for that namespace, useless for naming a host cgroup.
        monkeypatch.setattr(t, "cgroup_of_pid", lambda pid, root: "/")
        with pytest.raises(t.TargetError, match="namespace root"):
            t.parse_target("self", str(cgroup_root))

    def test_self_scheme_raises_when_unresolvable(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "cgroup_of_pid", lambda pid, root: None)
        with pytest.raises(t.TargetError, match="namespace root"):
            t.parse_target("self", str(cgroup_root))

    def test_containerid_valid_hex_but_no_such_cgroup(self, cgroup_root: Path):
        with pytest.raises(t.TargetError, match="has no cgroup under"):
            t.parse_target("containerid:" + "f" * 64, str(cgroup_root))

    def test_container_scheme_success(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "resolve_container", lambda ref: ("b" * 64, "wings-prod"))
        resolved = t.parse_target("container:wings-prod", str(cgroup_root))
        assert resolved[0].container_id == "b" * 64
        assert resolved[0].cgroup.endswith("docker-" + "b" * 64 + ".scope")
        assert resolved[0].label == "wings-prod"

    def test_container_scheme_raises_when_no_cgroup_matches(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "resolve_container", lambda ref: ("f" * 64, "ghost"))
        with pytest.raises(t.TargetError, match="has no cgroup under"):
            t.parse_target("container:ghost", str(cgroup_root))

    def test_label_scheme_success_skips_unmatched_and_raises_if_none_land(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(
            t, "resolve_label",
            lambda selector: [("b" * 64, "matched"), ("f" * 64, "no-cgroup-here")],
        )
        resolved = t.parse_target("label:app=gate", str(cgroup_root))
        assert len(resolved) == 1
        assert resolved[0].label == "matched"
        assert resolved[0].container_id == "b" * 64

    def test_label_scheme_raises_when_none_have_a_cgroup(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "resolve_label", lambda selector: [("f" * 64, "ghost")])
        with pytest.raises(t.TargetError, match="none has a cgroup here"):
            t.parse_target("label:app=gate", str(cgroup_root))


class TestCgroupOfPid:
    def test_no_cgroup_file_is_none(self, monkeypatch):
        monkeypatch.setattr(t.util, "read_text", lambda p: None)
        assert t.cgroup_of_pid(12345) is None

    def test_skips_non_hierarchy_zero_lines_before_matching(self, monkeypatch):
        # A cgroup v1-style line ("1:name=systemd:...") must not be mistaken
        # for the unified (hierarchy "0") line the caller actually wants.
        monkeypatch.setattr(
            t.util, "read_text",
            lambda p: "1:name=systemd:/foo\n0::/dev.slice/dev-background.slice",
        )
        assert t.cgroup_of_pid(1) == "/dev.slice/dev-background.slice"

    def test_no_matching_line_is_none(self, monkeypatch):
        monkeypatch.setattr(t.util, "read_text", lambda p: "1:name=systemd:/foo")
        assert t.cgroup_of_pid(1) is None

    def test_empty_path_after_hierarchy_zero_is_root(self, monkeypatch):
        monkeypatch.setattr(t.util, "read_text", lambda p: "0::")
        assert t.cgroup_of_pid(1) == "/"


class TestBareNameInference:
    def test_docker_present_and_container_resolves(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(t, "resolve_container", lambda ref: ("b" * 64, "wings-prod"))
        resolved = t.parse_target("wings-prod", str(cgroup_root))
        assert resolved[0].kind == "container"
        assert resolved[0].container_id == "b" * 64

    def test_docker_present_but_container_lookup_fails_falls_back_to_slice(self, cgroup_root: Path, monkeypatch):
        monkeypatch.setattr(t, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(t, "resolve_container", lambda ref: (_ for _ in ()).throw(t.TargetError("nope")))
        resolved = t.parse_target("dev-background.slice", str(cgroup_root))
        assert resolved[0].kind == "slice"

    def test_docker_absent_and_value_looks_like_a_slice(self, cgroup_root: Path):
        resolved = t.parse_target("dev-background.slice", str(cgroup_root))
        assert resolved[0].kind == "slice"

    def test_docker_absent_and_value_does_not_look_like_a_slice_or_container_falls_to_cgroup(
        self, cgroup_root: Path,
    ):
        # A value containing "/" is excluded by _looks_like_slice, and with
        # no scheme and no docker it can only be read as a raw cgroup path.
        write_cgroup(cgroup_root, "weird/nested", cgroup_files())
        resolved = t.parse_target("weird/nested", str(cgroup_root))
        assert resolved[0].kind == "cgroup"
        assert resolved[0].cgroup == "/weird/nested"

    def test_leading_slash_skips_inference_entirely(self, cgroup_root: Path, monkeypatch):
        # docker_bin must not even be consulted for an already-absolute path.
        calls = []
        monkeypatch.setattr(t, "docker_bin", lambda: calls.append(1) or None)
        resolved = t.parse_target("/dev.slice/dev-background.slice", str(cgroup_root))
        assert resolved[0].kind == "cgroup"
        assert calls == []

    def test_looks_like_slice_rejects_a_path_and_accepts_a_unit_name(self):
        assert t._looks_like_slice("dev-background.slice") is True
        assert t._looks_like_slice("a/b") is False
        assert t._looks_like_slice("has space") is False


class TestListChildrenErrors:
    def test_missing_cgroup_directory_yields_no_children(self, cgroup_root: Path):
        assert t.list_children("/does/not/exist", str(cgroup_root)) == []


class TestTargetOptions:
    def test_follow_option(self, cgroup_root: Path):
        resolved = t.parse_target("cgroup:/dev.slice@follow", str(cgroup_root))
        assert resolved[0].follow_children is True

    def test_nofollow_overrides_default(self, cgroup_root: Path):
        resolved = t.parse_target("cgroup:/dev.slice@nofollow", str(cgroup_root),
                                  default_follow=True)
        assert resolved[0].follow_children is False

    def test_metrics_subset(self, cgroup_root: Path):
        resolved = t.parse_target("cgroup:/dev.slice@metrics=mem+io", str(cgroup_root))
        assert resolved[0].metrics == {"mem", "io"}

    def test_label_override_and_role(self, cgroup_root: Path):
        resolved = t.parse_target("cgroup:/wings.slice@as=soulmask,role=observer",
                                  str(cgroup_root))
        assert resolved[0].label == "soulmask"
        assert resolved[0].role == "observer"


class TestResolveAll:
    def test_deduplicates_by_cgroup_and_keeps_stronger_request(self, cgroup_root: Path):
        resolved = t.resolve_all(
            ["cgroup:/dev.slice", "cgroup:/dev.slice@follow"], str(cgroup_root)
        )
        assert len(resolved) == 1
        assert resolved[0].follow_children is True

    def test_keys_are_unique(self, cgroup_root: Path):
        resolved = t.resolve_all(
            ["cgroup:/dev.slice@as=same", "cgroup:/wings.slice@as=same"], str(cgroup_root)
        )
        assert len({target.key for target in resolved}) == 2

    def test_partial_failure_keeps_the_rest(self, cgroup_root: Path, capsys):
        resolved = t.resolve_all(
            ["cgroup:/dev.slice", "containerid:not-hex"], str(cgroup_root)
        )
        assert len(resolved) == 1
        assert "skipped" in capsys.readouterr().out

    def test_total_failure_raises(self, cgroup_root: Path):
        with pytest.raises(t.TargetError):
            t.resolve_all(["containerid:not-hex"], str(cgroup_root))

    def test_key_collision_across_three_targets_increments_the_suffix_twice(self, cgroup_root: Path):
        # A key is derived from the label's last two path components
        # (util.short_label), so three distinct cgroups that merely share a
        # tail ("shared.slice/leaf.slice") collide on it — the third must not
        # clash with the second's "-2" suffix either, forcing the while loop
        # in resolve_all's key-uniquing step around more than once.
        write_cgroup(cgroup_root, "g1/shared.slice/leaf.slice", cgroup_files())
        write_cgroup(cgroup_root, "g2/shared.slice/leaf.slice", cgroup_files())
        write_cgroup(cgroup_root, "g3/shared.slice/leaf.slice", cgroup_files())
        resolved = t.resolve_all(
            ["cgroup:/g1/shared.slice/leaf.slice", "cgroup:/g2/shared.slice/leaf.slice",
             "cgroup:/g3/shared.slice/leaf.slice"],
            str(cgroup_root),
        )
        assert sorted(target.key for target in resolved) == [
            "shared-slice-leaf-slice", "shared-slice-leaf-slice-2", "shared-slice-leaf-slice-3",
        ]

    def test_second_spec_for_the_same_cgroup_widens_metrics_to_everything(self, cgroup_root: Path):
        # First asked for a subset, second asked for everything: the merge
        # must broaden, never silently keep the narrower request.
        resolved = t.resolve_all(
            ["cgroup:/dev.slice@metrics=mem", "cgroup:/dev.slice"], str(cgroup_root)
        )
        assert resolved[0].metrics is None

    def test_second_spec_for_the_same_cgroup_unions_two_metric_subsets(self, cgroup_root: Path):
        resolved = t.resolve_all(
            ["cgroup:/dev.slice@metrics=mem", "cgroup:/dev.slice@metrics=io"], str(cgroup_root)
        )
        assert resolved[0].metrics == {"mem", "io"}

    def test_widen_then_narrow_stays_wide(self, cgroup_root: Path):
        # Once a cgroup has been asked for in full, a later narrower request
        # for the same cgroup must not un-widen it.
        resolved = t.resolve_all(
            ["cgroup:/dev.slice", "cgroup:/dev.slice@metrics=mem"], str(cgroup_root)
        )
        assert resolved[0].metrics is None


class TestMembership:
    def test_follow_children_picks_up_a_container_that_appears_later(self, cgroup_root: Path):
        # This is the case that makes @follow load-bearing: a gate creates its
        # containers after the profiler is already sampling.
        target = t.parse_target("cgroup:/dev.slice/dev-background.slice@follow",
                                str(cgroup_root))[0]
        membership = t.Membership([target], root=str(cgroup_root))
        appeared, gone = membership.refresh()
        assert "/dev.slice/dev-background.slice" in appeared
        before = set(membership.paths())

        write_cgroup(cgroup_root, "dev.slice/dev-background.slice/docker-" + "c" * 64 + ".scope",
                     cgroup_files())
        appeared, gone = membership.refresh()

        assert len(appeared) == 1
        assert appeared[0].endswith("docker-" + "c" * 64 + ".scope")
        assert not gone
        assert set(membership.paths()) - before == set(appeared)

    def test_disappearance_is_reported(self, cgroup_root: Path):
        scope = "dev.slice/dev-background.slice/docker-" + "a" * 64 + ".scope"
        target = t.parse_target("cgroup:/dev.slice/dev-background.slice@follow",
                                str(cgroup_root))[0]
        membership = t.Membership([target], root=str(cgroup_root))
        membership.refresh()
        (cgroup_root / scope).rmdir() if not any((cgroup_root / scope).iterdir()) else None
        for child in (cgroup_root / scope).iterdir():
            child.unlink()
        (cgroup_root / scope).rmdir()
        appeared, gone = membership.refresh()
        assert any(path.endswith("docker-" + "a" * 64 + ".scope") for path in gone)

    def test_nofollow_samples_only_the_target(self, cgroup_root: Path):
        target = t.parse_target("cgroup:/dev.slice/dev-background.slice", str(cgroup_root))[0]
        membership = t.Membership([target], root=str(cgroup_root))
        membership.refresh()
        assert membership.paths() == ["/dev.slice/dev-background.slice"]

    def test_max_depth_is_respected(self, cgroup_root: Path):
        deep = "dev.slice/dev-background.slice/a/b/c/d/e"
        write_cgroup(cgroup_root, deep, cgroup_files())
        target = t.parse_target("cgroup:/dev.slice/dev-background.slice@follow",
                                str(cgroup_root))[0]
        membership = t.Membership([target], root=str(cgroup_root), max_depth=2)
        membership.refresh()
        assert not any(path.endswith("/c") or path.endswith("/d") for path in membership.paths())

    def test_label_for_descendant_names_its_owner(self, cgroup_root: Path):
        target = t.parse_target("cgroup:/dev.slice/dev-background.slice@follow,as=gate",
                                str(cgroup_root))[0]
        membership = t.Membership([target], root=str(cgroup_root))
        membership.refresh()
        scope = next(p for p in membership.paths() if p.endswith(".scope"))
        assert membership.label_for(scope).startswith("gate/")

    def test_a_target_whose_own_cgroup_is_gone_contributes_nothing(self, cgroup_root: Path):
        target = t.Target(key="ghost", cgroup="/does/not/exist", label="ghost",
                          kind="cgroup", spec="cgroup:/does/not/exist")
        membership = t.Membership([target], root=str(cgroup_root))
        appeared, disappeared = membership.refresh()
        assert appeared == []
        assert membership.paths() == []

    def test_owner_of_an_untracked_path_is_none(self, cgroup_root: Path):
        target = t.parse_target("cgroup:/dev.slice/dev-background.slice", str(cgroup_root))[0]
        membership = t.Membership([target], root=str(cgroup_root))
        membership.refresh()
        assert membership.owner("/no/such/path") is None

    def test_label_for_the_targets_own_cgroup_is_its_own_label(self, cgroup_root: Path):
        target = t.parse_target("cgroup:/dev.slice/dev-background.slice@as=gate",
                                str(cgroup_root))[0]
        membership = t.Membership([target], root=str(cgroup_root))
        membership.refresh()
        assert membership.label_for("/dev.slice/dev-background.slice") == "gate"

    def test_label_for_a_path_never_tracked_falls_back_to_short_label(self, cgroup_root: Path):
        membership = t.Membership([], root=str(cgroup_root))
        membership.refresh()
        assert membership.label_for("/wings.slice/wings-prod.slice") == "wings.slice/wings-prod.slice"


class TestWalk:
    def test_list_children(self, cgroup_root: Path):
        children = t.list_children("/dev.slice", str(cgroup_root))
        assert "/dev.slice/dev-background.slice" in children
        assert "/dev.slice/dev-interactive.slice" in children

    def test_find_container_cgroup(self, cgroup_root: Path):
        found = t.find_container_cgroup("b" * 64, str(cgroup_root))
        assert found == "/wings.slice/wings-prod.slice/docker-" + "b" * 64 + ".scope"

    def test_find_container_cgroup_missing(self, cgroup_root: Path):
        assert t.find_container_cgroup("f" * 64, str(cgroup_root)) is None
