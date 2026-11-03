"""Offline guards for the dark theme (templates/theme-dark.css.j2) and the client
features (templates/custom-config.js.j2, [jitsi.client]): both render from the
project defaults, honour overrides and the enable flags, leave no unrendered
placeholders, and the JS is syntactically valid. No network, no docker."""
from __future__ import annotations

import copy
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

jinja2 = pytest.importorskip("jinja2")

PROJECT = Path(__file__).resolve().parents[1]
DEFAULTS = PROJECT / "ciu.defaults.toml.j2"
COMPOSE = PROJECT / "ciu.compose.yml.j2"
CSS_T = PROJECT / "templates" / "theme-dark.css.j2"
JS_T = PROJECT / "templates" / "custom-config.js.j2"
ENV = {"PUBLIC_FQDN": "example.test", "PUBLIC_IP": "203.0.113.7"}


def _render(path: Path, ctx: dict) -> str:
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return env.from_string(path.read_text(encoding="utf-8")).render(**ctx)


def _cfg() -> dict:
    return tomllib.loads(_render(DEFAULTS, {"env": ENV}))["jitsi"]


def _css(cfg: dict) -> str:
    return _render(CSS_T, {"jitsi": cfg})


def _js(cfg: dict) -> str:
    return _render(JS_T, {"jitsi": cfg})


def _with(**sections: dict) -> dict:
    cfg = copy.deepcopy(_cfg())
    for name, values in sections.items():
        cfg[name].update(values)
    return cfg


def _assert_clean(text: str) -> None:
    for token in ("{{", "}}", "{%", "%}", "{#", "#}"):
        assert token not in text, f"unrendered Jinja token {token!r}"


# ---- configfile declaration ----------------------------------------------------

def test_configfile_mounts_are_declared_with_owned_directories() -> None:
    cfg = _cfg()
    cf = cfg["jitsi-web"]["configfile"]
    assert cf["custom_config"]["target"] == "/config/custom-config.js"
    assert cf["theme_dark"]["target"] == "/usr/share/jitsi-meet/static/theme/theme-dark.css"
    for spec in cf.values():
        assert (PROJECT / spec["template"]).is_file()
        # the image runs as uid 1000: the rendered file must be world-readable
        assert int(spec["mode"], 8) & 0o004
    # ciu mounts the PARENT directory (SPEC S5.3a): never a directory the image
    # needs for itself (those would be hidden by the mount)
    for spec in cf.values():
        parent = str(Path(spec["target"]).parent)
        assert parent in ("/config", "/usr/share/jitsi-meet/static/theme"), parent
    # the compose service the section names must exist
    assert re.search(r"^  jitsi-web:", COMPOSE.read_text(encoding="utf-8"), re.MULTILINE)


def test_web_config_is_not_also_a_hostdir_mount() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    web = compose.split("  jitsi-web:")[1]
    assert ":/config" not in web, "a second /config mount would collide with the configfile mount"


# ---- theme -----------------------------------------------------------------------

def test_theme_renders_with_defaults() -> None:
    cfg = _cfg()
    css = _css(cfg)
    _assert_clean(css)
    t = cfg["theme"]
    assert t["enabled"] is True
    for key in ("background", "surface", "text", "accent"):
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", t[key]), key
    assert f"--vt-accent: {t['accent']};" in css
    assert f"--vt-bg: {t['background']};" in css
    assert "color-scheme: dark;" in css
    # covers the main areas
    for needle in (".welcome", ".meetings-list", ".premeeting-screen", ".toolbox-content-items",
                   "#sideToolbarContainer", "contextMenu", "-modal", "speaker", "video, canvas, img"):
        assert needle in css, needle
    # balanced braces (cheap CSS syntax sanity)
    assert css.count("{") == css.count("}")


def test_theme_custom_colours_are_used() -> None:
    cfg = _with(theme={"accent": "#aa5500", "background": "#0b0b0b", "surface": "#141414", "text": "#cccccc"})
    css = _css(cfg)
    _assert_clean(css)
    assert "--vt-accent: #aa5500;" in css
    assert "--vt-bg: #0b0b0b;" in css
    assert "--vt-surface: #141414;" in css
    assert "--vt-text: #cccccc;" in css
    assert "#3b6ea8" not in css
    js = _js(cfg)
    assert '"#0b0b0b"' in js


def test_theme_disabled_renders_nothing() -> None:
    cfg = _with(theme={"enabled": False})
    css = _css(cfg)
    _assert_clean(css)
    assert "{" not in css and "color-scheme" not in css, "disabled theme must carry no rules"
    js = _js(cfg)
    _assert_clean(js)
    assert "theme-dark.css" not in js and "createElement" not in js


def test_theme_enabled_js_links_the_served_stylesheet() -> None:
    js = _js(_cfg())
    target = _cfg()["jitsi-web"]["configfile"]["theme_dark"]["target"]
    served = "/" + target.split("/usr/share/jitsi-meet/")[1]
    assert f"link.href = '{served}';" in js


# ---- client features ---------------------------------------------------------------

def _check_js(js: str, tmp_path: Path) -> None:
    node = shutil.which("node")
    if node:
        f = tmp_path / "custom-config.js"
        f.write_text(js, encoding="utf-8")
        proc = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        # run it against a stub `config`/`document`: every assignment must work
        runner = tmp_path / "run.js"
        runner.write_text(
            "global.config = {};\n"
            "const appended = [];\n"
            "global.document = {head: {appendChild: (e) => appended.push(e)},\n"
            "  documentElement: {style: {}}, createElement: (t) => ({tag: t})};\n"
            f"require({str(f)!r});\n"
            "console.log(JSON.stringify({config: global.config, appended}));\n",
            encoding="utf-8")
        out = subprocess.run([node, str(runner)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return
    # fallback syntax sanity: balanced brackets outside strings and comments
    code = re.sub(r"//[^\n]*", "", js)
    code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', code)
    for a, b in ("()", "{}", "[]"):
        assert code.count(a) == code.count(b), f"unbalanced {a}{b}"


def test_client_js_defaults_valid_and_flags_present(tmp_path: Path) -> None:
    cfg = _cfg()
    js = _js(cfg)
    _assert_clean(js)
    _check_js(js, tmp_path)
    # defaults leave the audio processing as upstream ships it
    assert "config.disableAGC = false;" in js
    assert "config.disableAEC = false;" in js
    assert "config.disableNS = false;" in js
    assert "config.audioQuality.stereo = false;" in js
    # visibility raised: connection indicator always shown, speaker stats search on
    assert "autoHide: false" in js and "disabled: false" in js
    assert "disableSearch: false" in js
    assert '["role", "name", "hasLeft"]' in js
    assert "config.disableShowMoreStats = false;" in js
    assert "config.desktopSharingFrameRate" in js and "max: 15" in js
    # optional keys are absent unless set
    assert "opusMaxAverageBitrate" not in js and "statisticsDisplayName" not in js


def test_client_js_overrides(tmp_path: Path) -> None:
    cfg = _with(client={
        "disable_agc": True, "disable_aec": True, "disable_ns": True, "stereo": True,
        "opus_max_average_bitrate": 128000, "connection_indicators_auto_hide": True,
        "speaker_stats_search": False, "statistics_display_name": 'Team "A"',
        "desktop_sharing_framerate_max": 0,
    })
    js = _js(cfg)
    _assert_clean(js)
    _check_js(js, tmp_path)
    assert "config.disableAGC = true;" in js
    assert "config.disableAEC = true;" in js
    assert "config.disableNS = true;" in js
    assert "config.audioQuality.stereo = true;" in js
    assert "config.audioQuality.opusMaxAverageBitrate = 128000;" in js
    assert "autoHide: true" in js
    assert "disableSearch: true" in js
    assert 'config.statisticsDisplayName = "Team \\"A\\"";' in js  # quoting survives
    assert "desktopSharingFrameRate" not in js


def test_client_disabled_sets_nothing(tmp_path: Path) -> None:
    js = _js(_with(client={"enabled": False}))
    _assert_clean(js)
    _check_js(js, tmp_path)
    assert "config.disable" not in js and "connectionIndicators" not in js
    assert "theme-dark.css" in js, "the theme flag is independent of the client flag"


def test_both_disabled_is_valid_and_inert(tmp_path: Path) -> None:
    js = _js(_with(client={"enabled": False}, theme={"enabled": False}))
    _assert_clean(js)
    _check_js(js, tmp_path)
    assert "config." not in re.sub(r"//[^\n]*", "", js)


# ---- JVB stats stay internal ---------------------------------------------------------

def _compose(cfg: dict) -> str:
    cfg = copy.deepcopy(cfg)
    for svc in ("prosody", "jicofo", "jvb"):
        cfg[svc]["hostdir"] = {"config": f"/tmp/vol-{svc}-config"}
    cfg["prosody"]["hostdir"]["data"] = "/tmp/vol-prosody-data"
    cfg["container_prefix"] = "jitsi-test"
    return _render(COMPOSE, {"jitsi": cfg})


def test_colibri_rest_is_enabled_but_never_published() -> None:
    cfg = _cfg()
    assert cfg["jvb"]["rest_stats"] is True
    text = _compose(cfg)
    assert 'COLIBRI_REST_ENABLED: "true"' in text
    assert "8080" not in "".join(
        line for line in text.splitlines() if "ports:" in line or re.match(r'\s+- "\d+:\d+', line)
    ), "the JVB private http server (8080) must not be published"
    published = re.findall(r'^\s+- "(\d+):\d+(?:/\w+)?"\s*$', text, re.MULTILINE)
    assert published == [str(cfg["jvb"]["port"])]
    off = _compose(_with(jvb={"rest_stats": False}))
    assert 'COLIBRI_REST_ENABLED: "false"' in off
