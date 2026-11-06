"""cgpd configuration: schema, file, reload, overrides (CGPCTL-SPEC 3.2-3.5)."""

from __future__ import annotations

import os
import stat

import pytest

from cgprofile import daemon_config as dc


def _write(path, text, mode=None):
    path.write_text(text)
    if mode is not None:
        os.chmod(path, mode)
    return str(path)


def _settings(tmp_path, text=None, *, explicit=False, log=None, **kw):
    cfg = tmp_path / "cgpd.toml"
    if text is not None:
        cfg.write_text(text)
    s = dc.Settings(
        str(cfg), explicit=explicit, overrides_path=str(tmp_path / "state" / "overrides.toml"),
        owner_uid=os.getuid(), log=log or (lambda m: None), **kw,
    )
    return s


class TestValidate:
    @pytest.mark.parametrize("key,value,expected", [
        ("sessions_dir", "/x", "/x"),
        ("busctl", "busctl", "busctl"),
        ("max_exports", 4, 4),
        ("interval", 2, 2.0),
        ("damon_default", "off", "off"),
        ("gates_slice", " dev-gates.slice ", "dev-gates.slice"),
        ("observe_slices", ["a.slice"], ["a.slice"]),
        ("allow_uids", [1, 1, 2], [1, 2]),
        ("sockets.control", {"path": "/run/cgprofile/control/cgprofile.sock",
                              "group": "cgprofile-control", "mode": "660"},
         {"path": "/run/cgprofile/control/cgprofile.sock", "group": "cgprofile-control", "mode": "0660"}),
    ])
    def test_good(self, key, value, expected):
        assert dc.validate(key, value) == expected

    @pytest.mark.parametrize("key,value,needle", [
        ("sessions_dir", "", "non-empty"),
        ("sessions_dir", "rel", "absolute"),
        ("busctl", 3, "non-empty"),
        ("max_exports", True, "integer"),
        ("max_exports", 0, "at least 1"),
        ("max_exports", 17, "at most 16"),
        ("keep_days", 0, "at least"),
        ("interval", "1", "number"),
        ("interval", True, "number"),
        ("interval", float("inf"), "finite"),
        ("interval", 0, "finite"),
        ("damon_default", "maybe", "on"),
        ("gates_slice", "a--b.slice", "slice"),
        ("gates_slice", "  ", "slice name"),
        ("gates_slice", 5, "slice name"),
        ("observe_slices", "a.slice", "array"),
        ("allow_uids", 5, "array"),
        ("allow_uids", [-1], "non-negative"),
        ("allow_uids", [True], "non-negative"),
        ("sockets.control", [], "table"),
        ("sockets.control", {"path": "relative", "group": "g", "mode": "0660"}, "absolute path"),
        ("sockets.control", {"path": "/x", "group": "bad group", "mode": "0660"}, "group name"),
        ("sockets.control", {"path": "/x", "group": "g", "mode": 660}, "octal mode"),
        ("sockets.control", {"path": "/x", "group": "g"}, "missing mode"),
    ])
    def test_bad(self, key, value, needle):
        with pytest.raises(dc.ConfigError, match=needle):
            dc.validate(key, value)

    def test_unknown_key_names_the_nearest(self):
        with pytest.raises(dc.ConfigError, match="did you mean 'keep_days'"):
            dc.validate("keep_day", 3)

    def test_unknown_key_without_neighbour(self):
        with pytest.raises(dc.ConfigError) as err:
            dc.validate("zzzzzzzz", 3)
        assert "did you mean" not in str(err.value)


class TestFile:
    def test_missing_default_file_is_all_defaults(self, tmp_path):
        values, digest = dc.read_config_file(str(tmp_path / "none.toml"), explicit=False)
        assert values == {} and digest == dc.digest_of(b"")

    def test_missing_explicit_file_is_an_error(self, tmp_path):
        with pytest.raises(dc.ConfigError, match="does not exist"):
            dc.read_config_file(str(tmp_path / "none.toml"), explicit=True)

    def test_unreadable_file(self, tmp_path):
        with pytest.raises(dc.ConfigError, match="cannot read"):
            dc.read_config_file(str(tmp_path), explicit=True)

    def test_non_utf8(self, tmp_path):
        p = tmp_path / "c.toml"
        p.write_bytes(b"\xff\xfe")
        with pytest.raises(dc.ConfigError, match="UTF-8"):
            dc.read_config_file(str(p), explicit=True)

    def test_bad_toml_and_bad_value_carry_the_path(self, tmp_path):
        p = _write(tmp_path / "c.toml", "interval = [")
        with pytest.raises(dc.ConfigError, match="not valid TOML"):
            dc.read_config_file(p, explicit=True)
        p = _write(tmp_path / "d.toml", "keep_day = 3\n")
        with pytest.raises(dc.ConfigError, match=r"d\.toml: unknown key"):
            dc.read_config_file(p, explicit=True)

    def test_good_file_and_digest(self, tmp_path):
        p = _write(tmp_path / "c.toml", 'interval = 2.5\nallow_uids = [1003]\n')
        values, digest = dc.read_config_file(p, explicit=True)
        assert values == {"interval": 2.5, "allow_uids": [1003]}
        assert digest.startswith("sha256:") and len(digest) == 71

    def test_nested_table_is_refused(self, tmp_path):
        p = _write(tmp_path / "c.toml", "[interval]\nx = 1\n")
        with pytest.raises(dc.ConfigError, match="number"):
            dc.read_config_file(p, explicit=True)

    def test_old_socket_keys_name_the_new_tables(self, tmp_path):
        for key, replacement in (("ctl_socket", "sockets.control"),
                                 ("observer_socket", "sockets.observer")):
            p = _write(tmp_path / "old.toml", f'{key} = "/run/cgprofile/old.sock"\n')
            with pytest.raises(dc.ConfigError, match=rf"{key} was removed; use \[{replacement}\] path"):
                dc.read_config_file(p, explicit=True)

    def test_socket_tables_round_trip_with_top_level_values_first(self):
        values = {
            "interval": 2.5,
            "sockets.control": {"path": "/run/cgprofile/control/cgprofile.sock",
                                "group": "cgprofile-control", "mode": "0660"},
            "sockets.observer": {"path": "/run/cgprofile/observer/cgprofile.sock",
                                 "group": "cgprofile-observer", "mode": "0660"},
        }
        rendered = dc.render_config(values)
        assert rendered.startswith("interval = 2.5\n\n[sockets.control]\n")
        assert dc.parse_table(rendered) == values

    @pytest.mark.parametrize("control,observer,needle", [
        ({"path": "/control", "group": "same", "mode": "0660"},
         {"path": "/observer", "group": "same", "mode": "0660"}, "sockets.control.group and sockets.observer.group must differ"),
        ({"path": "/same", "group": "control", "mode": "0660"},
         {"path": "/same", "group": "observer", "mode": "0660"}, "sockets.control.path and sockets.observer.path must differ"),
        ({"path": "/control", "group": "control", "mode": "0660"},
         {"path": "/observer", "group": "docker", "mode": "0660"}, "must not be docker"),
    ])
    def test_socket_table_cross_validation(self, control, observer, needle):
        with pytest.raises(dc.ConfigError, match=needle):
            dc.render_config({"sockets.control": control, "sockets.observer": observer})

    def test_parse_table_refuses_the_docker_group_for_the_observer(self):
        # the observer socket is for unprivileged readers: docker is root-equivalent
        text = ('[sockets.control]\npath = "/run/cgprofile/control/cgprofile.sock"\n'
                'group = "cgprofile-control"\nmode = "0660"\n\n'
                '[sockets.observer]\npath = "/run/cgprofile/observer/cgprofile.sock"\n'
                'group = "docker"\nmode = "0660"\n')
        with pytest.raises(dc.ConfigError, match="sockets.observer.group must not be docker"):
            dc.parse_table(text)

    def test_socket_settings_refuses_the_docker_group_for_the_observer(self):
        values = {"sockets.observer": {"path": "/run/cgprofile/observer/cgprofile.sock",
                                       "group": "docker", "mode": "0660"}}
        with pytest.raises(dc.ConfigError, match="sockets.observer.group must not be docker"):
            dc.socket_settings(values)


class TestStaleEnv:
    def test_names_the_replacement_key(self):
        problems = dc.stale_env_problems(
            {"CGPROFILE_ALLOW_UIDS": "1", "CGPROFILE_HELPER_IMAGE": "x", "PATH": "/bin"},
            "/etc/cgprofile/cgpd.toml")
        assert problems == [
            "CGPROFILE_ALLOW_UIDS is no longer read; set allow_uids in /etc/cgprofile/cgpd.toml",
            "CGPROFILE_HELPER_IMAGE is no longer read; it was removed (helper mode is gone)",
        ]

    def test_clean_environment(self):
        assert dc.stale_env_problems({"PATH": "/bin"}, "/x") == []


class TestRender:
    def test_round_trips_through_the_parser(self):
        values = {"allow_uids": [1, 2], "interval": 2.5, "gates_slice": 'a.slice',
                  "damon_default": "off", "keep_days": 3}
        assert dc.parse_table(dc.render_table(values)) == values


class TestSettingsLoadAndReload:
    def test_precedence_and_sources(self, tmp_path):
        s = _settings(tmp_path, "interval = 2.0\n")
        s.load()
        assert s.get("interval") == 2.0 and s.source("interval") == "file"
        assert s.get("keep_days") == 14 and s.source("keep_days") == "default"
        s.set_override("interval", 4)
        assert s.get("interval") == 4.0 and s.source("interval") == "override"
        entry = s.entry("interval")
        assert entry["shadows"] == {"value": 2.0, "source": "file"}
        assert "shadows" not in s.entry("keep_days")
        assert s.override_count == 1

    def test_shadow_of_a_default(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        s.set_override("keep_days", 3)
        assert s.entry("keep_days")["shadows"] == {"value": 14, "source": "default"}

    def test_invalid_first_load_raises(self, tmp_path):
        s = _settings(tmp_path, "nope = 1\n")
        with pytest.raises(dc.ConfigError):
            s.load()

    def test_describe(self, tmp_path):
        s = _settings(tmp_path, "interval = 2.0\n", clock=lambda: 0.0)
        assert s.describe()["loaded_at"] is None
        s.load()
        doc = s.describe()
        assert doc["file"] == s.config_path and doc["loaded_at"] == "1970-01-01T00:00:00Z"
        assert doc["reloads"] == 0 and doc["last_reload_error"] is None
        assert set(doc["keys"]) == set(dc.KEYS)
        assert doc["overrides_ignored"] == 0

    def test_reload_swaps_reloadable_keys(self, tmp_path):
        lines = []
        s = _settings(tmp_path, "interval = 2.0\n", log=lines.append)
        s.load()
        (tmp_path / "cgpd.toml").write_text("interval = 3.0\nkeep_days = 5\n")
        assert s.reload() is True
        assert s.get("interval") == 3.0 and s.get("keep_days") == 5
        assert s.reloads == 1 and s.last_reload_error is None
        assert any("reload applied digest=sha256:" in line for line in lines)

    def test_reload_keeps_overrides(self, tmp_path):
        s = _settings(tmp_path, "interval = 2.0\n")
        s.load()
        s.set_override("interval", 9)
        (tmp_path / "cgpd.toml").write_text("interval = 3.0\n")
        s.reload()
        assert s.get("interval") == 9.0 and s.source("interval") == "override"
        assert s.entry("interval")["shadows"]["value"] == 3.0

    def test_invalid_reload_keeps_every_old_setting(self, tmp_path):
        lines = []
        s = _settings(tmp_path, "interval = 2.0\n", log=lines.append)
        s.load()
        (tmp_path / "cgpd.toml").write_text("interval = -1\n")
        assert s.reload() is False
        assert s.get("interval") == 2.0
        assert "greater than 0" in s.last_reload_error
        assert s.reloads == 0

    def test_fixed_key_change_is_reported_and_ignored(self, tmp_path):
        s = _settings(tmp_path, 'sessions_dir = "/a"\ninterval = 2.0\n')
        s.load()
        (tmp_path / "cgpd.toml").write_text('sessions_dir = "/b"\ninterval = 3.0\n')
        assert s.reload() is False
        assert s.get("sessions_dir") == "/a"
        assert s.get("interval") == 3.0
        assert s.last_reload_error == "restart required for sessions_dir"
        # an unchanged fixed key does not trip it, and the error clears
        (tmp_path / "cgpd.toml").write_text('sessions_dir = "/a"\n')
        assert s.reload() is True and s.last_reload_error is None
        assert s.get("sessions_dir") == "/a"

    def test_fixed_key_removed_from_file_is_a_change(self, tmp_path):
        s = _settings(tmp_path, 'sessions_dir = "/a"\n')
        s.load()
        (tmp_path / "cgpd.toml").write_text("")
        assert s.reload() is False
        assert s.get("sessions_dir") == "/a"


class TestOverrides:
    def test_tunable_allowlist_listed_on_refusal(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        for key in ("ctl_socket", "observe_slices", "gates_slice", "nope", 5):
            with pytest.raises(dc.ConfigError, match="tunable keys: export_max_bytes"):
                s.set_override(key, 1)

    def test_invalid_value_leaves_no_trace(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        with pytest.raises(dc.ConfigError):
            s.set_override("keep_days", 0)
        assert s.override_count == 0

    def test_memory_only_override(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        res = s.set_override("allow_uids", [1003, 1004])
        assert res == {
            "key": "allow_uids", "old": {"value": [], "source": "default"},
            "new": {"value": [1003, 1004], "source": "override"},
            "persisted": False, "applied": True,
        }
        assert not os.path.exists(s.overrides_path)
        assert s.entry("allow_uids")["persisted"] is False

    def test_dry_run_changes_nothing(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        res = s.set_override("keep_days", 3, persist=True, dry_run=True)
        assert res["applied"] is False and res["persisted"] is True
        assert s.override_count == 0 and not os.path.exists(s.overrides_path)

    def test_persist_writes_0600_atomically_and_survives_restart(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        s.set_override("keep_days", 3, persist=True)
        mode = stat.S_IMODE(os.stat(s.overrides_path).st_mode)
        assert mode == 0o600
        assert os.listdir(os.path.dirname(s.overrides_path)) == ["overrides.toml"]
        assert s.entry("keep_days")["persisted"] is True
        s2 = _settings(tmp_path)
        s2.load()
        assert s2.get("keep_days") == 3 and s2.source("keep_days") == "override"
        assert s2.overrides_ignored == 0

    def test_memory_override_of_a_persisted_key_rewrites_the_file(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        s.set_override("keep_days", 3, persist=True)
        s.set_override("keep_sessions", 7, persist=True)
        s.set_override("keep_days", 4)  # memory only now
        assert s.entry("keep_days")["persisted"] is False
        s2 = _settings(tmp_path)
        s2.load()
        assert s2.get("keep_days") == 14 and s2.get("keep_sessions") == 7

    def test_persist_failure_applies_nothing(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        (tmp_path / "state").write_text("a file where the directory should be")
        with pytest.raises(dc.PersistError, match="cannot write"):
            s.set_override("keep_days", 3, persist=True)
        assert s.override_count == 0
        assert s.get("keep_days") == 14
        assert os.listdir(tmp_path) == ["state"] or "cgpd.toml" in os.listdir(tmp_path)

    def test_unlink_failure_in_cleanup_is_tolerated(self, tmp_path, monkeypatch):
        s = _settings(tmp_path)
        s.load()
        real_replace = os.replace

        def boom(src, dst):
            os.unlink(src)
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(dc.PersistError):
            s.set_override("keep_days", 3, persist=True)
        monkeypatch.setattr(os, "replace", real_replace)

    def test_reset_one_all_and_none(self, tmp_path):
        s = _settings(tmp_path, "keep_days = 5\n")
        s.load()
        s.set_override("keep_days", 3, persist=True)
        s.set_override("keep_sessions", 9)
        res = s.reset("keep_days")
        assert res == {"reset": ["keep_days"],
                       "values": {"keep_days": {"value": 5, "source": "file"}}}
        assert s.reset("keep_days") == {"reset": [], "values": {}}
        res = s.reset(None)
        assert res["reset"] == ["keep_sessions"]
        assert s.override_count == 0
        s2 = _settings(tmp_path, "keep_days = 5\n")
        s2.load()
        assert s2.get("keep_days") == 5

    def test_reset_of_memory_only_key_leaves_the_file_alone(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        s.set_override("keep_days", 3)
        assert s.reset("keep_days")["reset"] == ["keep_days"]
        assert not os.path.exists(s.overrides_path)

    def test_reset_refuses_a_non_tunable_key(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        with pytest.raises(dc.ConfigError):
            s.reset("ctl_socket")

    def test_reset_persist_failure_keeps_the_override(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        s.set_override("keep_days", 3, persist=True)
        os.chmod(os.path.dirname(s.overrides_path), 0o500)
        try:
            if os.geteuid() == 0:
                pytest.skip("root ignores directory modes")
            with pytest.raises(dc.PersistError):
                s.reset("keep_days")
            assert s.get("keep_days") == 3
        finally:
            os.chmod(os.path.dirname(s.overrides_path), 0o700)

    def test_audit_ids_count_up(self, tmp_path):
        s = _settings(tmp_path)
        assert [s.next_audit_id(), s.next_audit_id()] == ["1", "2"]


class TestOverridesFileIsDefensive:
    def _prepare(self, tmp_path, text, mode=0o600):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        _write(state / "overrides.toml", text, mode)
        lines = []
        s = _settings(tmp_path, log=lines.append)
        return s, lines

    def test_wrong_mode_ignored_whole(self, tmp_path):
        s, lines = self._prepare(tmp_path, "keep_days = 3\n", 0o644)
        s.load()
        assert s.get("keep_days") == 14 and s.overrides_ignored == 1
        assert any("mode 0600" in line for line in lines)

    def test_wrong_owner_ignored_whole(self, tmp_path):
        s, lines = self._prepare(tmp_path, "keep_days = 3\n")
        s.owner_uid = os.getuid() + 1
        s.load()
        assert s.overrides_ignored == 1

    def test_not_a_regular_file(self, tmp_path):
        state = tmp_path / "state"
        (state / "overrides.toml").mkdir(parents=True)
        s = _settings(tmp_path)
        s.load()
        assert s.overrides_ignored == 1

    def test_unparseable_ignored_whole(self, tmp_path):
        s, lines = self._prepare(tmp_path, "keep_days = [\n")
        s.load()
        assert s.overrides_ignored == 1 and s.override_count == 0

    def test_bad_entries_are_counted_and_the_rest_applies(self, tmp_path):
        s, lines = self._prepare(
            tmp_path,
            "keep_days = 3\nnope = 1\nctl_socket = \"/x\"\nkeep_sessions = 0\n")
        s.load()
        assert s.get("keep_days") == 3
        assert s.get("sockets.control")["path"] == dc.DEFAULT_CONTROL_SOCKET
        assert s.overrides_ignored == 3
        assert len([line for line in lines if "ignored" in line]) == 3


class TestSeed:
    def test_seed_takes_non_default_values_as_the_file_layer(self, tmp_path):
        s = _settings(tmp_path)
        s.load()
        s.seed({"keep_days": 3, "interval": 1.0, "allow_uids": [7]})
        assert s.get("keep_days") == 3 and s.source("keep_days") == "file"
        assert s.source("interval") == "default"   # equal to the default: skipped
        assert s.get("allow_uids") == [7]
