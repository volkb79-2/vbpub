"""W0-TESTER: tester-unified image inputs (KI-52(b), BG-05).

The image is not built here (no docker builds). What is testable offline is the
requirements generator, run against synthetic trees and against the real
estate trees, and the Dockerfile / .dockerignore text that carries the policy.
"""
from __future__ import annotations

import importlib.util
import re
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "tester-unified" / "gen-requirements.py"
DOCKERFILE = REPO_ROOT / "tester-unified" / "Dockerfile"


def _load():
    spec = importlib.util.spec_from_file_location("gen_requirements", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The assay canary runs the suite from an isolated copy of ``cmru/`` alone, with
# no repository-root files; these tests judge those files, so they only run
# where the repository tree is present (the coverage lane and the cockpit).
pytestmark = pytest.mark.skipif(
    not GENERATOR.exists(), reason="repository-root tester-unified files absent (isolated canary tree)",
)
gen = _load() if GENERATOR.exists() else None


def _tree(root: Path, **projects: str) -> Path:
    """Write ``<root>/<relative>/pyproject.toml`` for every project."""
    for relative, body in projects.items():
        path = root / relative.replace("__", "/")
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def _all_projects(root: Path, **overrides: str) -> Path:
    base = {
        "ciu": '[project]\nname = "ciu"\ndependencies = ["Jinja2>=3"]\n'
               '[project.optional-dependencies]\nssh = ["paramiko>=5"]\ntest = ["pytest>=8"]\n',
        "cmru": '[project]\nname = "cmru"\ndependencies = []\n'
                '[project.optional-dependencies]\ntest = ["pytest-cov>=5"]\n'
                '[build-system]\nrequires = ["setuptools==1", "setuptools_scm==2"]\n',
        "assay": '[project]\nname = "assay"\ndependencies = []\n'
                 '[project.optional-dependencies]\ntest = ["hypothesis>=6"]\n'
                 '[build-system]\nrequires = ["setuptools==9"]\n',
        "topos": '[project]\nname = "topos"\ndependencies = ["textual>=8"]\n'
                 '[project.optional-dependencies]\nzstandard = ["zstandard>=0.22"]\n'
                 'dev = ["pytest>=8", "topos[zstandard]"]\n',
        "nyxloom": '[project]\nname = "nyxloom"\ndependencies = ["PyYAML>=6"]\n'
                   '[project.optional-dependencies]\ntest = ["coverage>=7"]\n',
        "cgroup-profiler": '[project]\nname = "cgroup-profiler"\ndependencies = []\n'
                           '[project.optional-dependencies]\ntest = ["numpy>=2"]\n',
        "libraries__cli-extended": '[project]\nname = "cli-extended"\ndependencies = []\n'
                                   '[build-system]\nrequires = ["setuptools==1", "wheel==3"]\n',
    }
    base.update({key.replace("/", "__"): value for key, value in overrides.items()})
    return _tree(root, **base)


def test_generator_emits_the_third_party_closure_and_expands_self_extras(tmp_path):
    _all_projects(tmp_path)
    produced = list(gen.requirements(tmp_path))
    assert "Jinja2>=3" in produced and "paramiko>=5" in produced
    assert "zstandard>=0.22" in produced  # topos[dev] -> topos[zstandard] expanded
    assert not any(item.startswith("topos[") for item in produced)  # never handed to pip
    assert "setuptools==9" in produced  # assay's build backend (no-build-isolation lanes)
    assert "setuptools==1" not in produced  # cmru's pins go to the throwaway build venv
    assert produced[-1] == "build"


@pytest.mark.parametrize(
    "bad",
    ["Worktree", "cmru==1", "assay", "ciu>=1",
     "nyxloom", "topos[dev]>=1", "CGroup.Profiler", "run_gate"],
)
def test_generator_refuses_estate_internal_names_in_dependencies(tmp_path, bad):
    """BG-05: a PyPI-default pip must never be asked for an estate-internal
    name (unclaimed `cli-extended`, unrelated look-alike `worktree`)."""
    _all_projects(tmp_path, cmru=(
        '[project]\nname = "cmru"\n'
        f'dependencies = ["{bad}"]\n'
    ))
    with pytest.raises(SystemExit, match="estate-internal"):
        list(gen.requirements(tmp_path))


def test_generator_refuses_internal_names_in_extras_and_build_requires(tmp_path):
    _all_projects(tmp_path, ciu=(
        '[project]\nname = "ciu"\ndependencies = []\n'
        '[project.optional-dependencies]\nssh = []\ntest = ["worktree>=1"]\n'
    ))
    with pytest.raises(SystemExit, match="'worktree'"):
        list(gen.requirements(tmp_path))
    _all_projects(tmp_path, assay=(
        '[project]\nname = "assay"\ndependencies = []\n'
        '[project.optional-dependencies]\ntest = []\n'
        '[build-system]\nrequires = ["ciu"]\n'
    ))
    with pytest.raises(SystemExit, match="'ciu'"):
        list(gen.requirements(tmp_path))
    other = _tree(tmp_path / "x", cmru='[build-system]\nrequires = ["cmru"]\n')
    with pytest.raises(SystemExit, match="'cmru'"):
        list(gen.build_requirements(other, ["cmru/pyproject.toml"]))


@pytest.mark.parametrize("line", [
    "cli-extended>=0.2.0", "cli_extended", "CLI.Extended==0.2.0", "cli-extended[x]>=0.2",
])
def test_generator_skips_every_cli_extended_line_so_none_reaches_pip(tmp_path, line, capsys):
    """D8: cmru declares cli-extended as a real dependency. The line must be
    SKIPPED (not refused, not printed): the image installs the released wheel
    by sha256, and a PyPI-default pip is never asked for the name."""
    _all_projects(
        tmp_path,
        cmru=(
            f'[project]\nname = "cmru"\ndependencies = ["{line}", "Jinja2>=3"]\n'
            f'[project.optional-dependencies]\ntest = ["pytest-cov>=5", "{line}"]\n'
            f'[build-system]\nrequires = ["setuptools==1", "{line}"]\n'
        ),
        assay=(
            '[project]\nname = "assay"\ndependencies = []\n'
            '[project.optional-dependencies]\ntest = ["hypothesis>=6"]\n'
            f'[build-system]\nrequires = ["setuptools==9", "{line}"]\n'
        ),
    )
    produced = list(gen.requirements(tmp_path))
    assert "Jinja2>=3" in produced and "pytest-cov>=5" in produced  # neighbours survive
    assert not [item for item in produced if "extended" in item.lower()]
    assert gen.main(["--root", str(tmp_path)]) == 0
    assert "extended" not in capsys.readouterr().out.lower()
    built = list(gen.build_requirements(tmp_path, ["cmru/pyproject.toml"]))
    assert built == ["setuptools==1"]


def test_generator_still_refuses_other_internal_names_next_to_a_cli_extended_line(tmp_path):
    _all_projects(tmp_path, cmru=(
        '[project]\nname = "cmru"\ndependencies = ["cli-extended>=0.2.0", "worktree>=1"]\n'
    ))
    with pytest.raises(SystemExit, match="'worktree'"):
        list(gen.requirements(tmp_path))


def test_generator_refuses_direct_url_requirements(tmp_path):
    _all_projects(tmp_path, nyxloom=(
        '[project]\nname = "nyxloom"\n'
        'dependencies = ["somepkg @ https://example.invalid/somepkg.whl"]\n'
        '[project.optional-dependencies]\ntest = []\n'
    ))
    with pytest.raises(SystemExit, match="direct-URL"):
        list(gen.requirements(tmp_path))


def test_generator_build_requires_mode_prints_only_the_build_backends(tmp_path, capsys):
    _all_projects(tmp_path)
    assert gen.main(["--root", str(tmp_path), "--build-requires",
                     "cmru/pyproject.toml", "libraries/cli-extended/pyproject.toml"]) == 0
    assert capsys.readouterr().out.splitlines() == ["setuptools==1", "setuptools_scm==2", "wheel==3"]


def test_generator_deduplicates_and_prints_one_requirement_per_line(tmp_path, capsys):
    _all_projects(tmp_path)
    assert gen.main(["--root", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(set(lines)) and lines[-1] == "build"


def test_real_estate_trees_have_no_internal_requirement_in_the_pypi_bound_closure(tmp_path):
    """The generator against the REAL pyprojects, laid out as the Dockerfile
    COPYs them: cmru now declares ``cli-extended`` (KI-51), the closure must
    still be accepted, and no cli-extended line may be in it."""
    for name in ("ciu", "cmru", "assay", "topos", "nyxloom"):
        (tmp_path / name).symlink_to(REPO_ROOT / name)
    (tmp_path / "cgroup-profiler").symlink_to(REPO_ROOT / "scripts" / "cgroup-profiler")
    (tmp_path / "libraries").mkdir()
    (tmp_path / "libraries" / "cli-extended").symlink_to(REPO_ROOT / "libraries" / "cli-extended")
    closure = list(gen.requirements(tmp_path))
    names = {gen.requirement_name(item) for item in closure}
    assert not names & gen.ESTATE_INTERNAL
    assert {"pytest", "jinja2", "textual"} <= names
    build = list(gen.build_requirements(tmp_path, ["cmru/pyproject.toml"]))
    assert any(item.startswith("setuptools") for item in build)
    # The real cmru pyproject DOES declare it; that is what the skip is for.
    declared = tomllib.loads((REPO_ROOT / "cmru" / "pyproject.toml").read_text("utf-8"))
    assert any(gen.requirement_name(item) == "cli-extended"
               for item in declared["project"]["dependencies"])


# --- Dockerfile / .dockerignore policy text (the image itself is not built here)

def _dockerfile_run_lines() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_dockerfile_disables_detached_git_maintenance_system_wide_and_asserts_it():
    code = _dockerfile_run_lines()
    assert "git config --system maintenance.autoDetach false" in code
    assert "git config --system gc.autoDetach false" in code
    assert 'git config --system --get maintenance.autoDetach)" = false' in code
    assert 'git config --system --get gc.autoDetach)" = false' in code
    # The setting must be in place (as root) before the image drops to uid 1003.
    assert code.index("gc.autoDetach false") < code.index("USER 1003")


def test_dockerfile_installs_estate_internal_packages_only_offline_from_copied_sources():
    code = _dockerfile_run_lines()
    # The old install of cmru from a PyPI-default pip is gone.
    assert "pip install --no-cache-dir --no-deps /src/cmru" not in code
    wheel = re.search(r"pip wheel (?P<flags>[^\n]*\\\n[^\n]*)", code)
    assert wheel is not None
    for flag in ("--no-index", "--no-deps", "--no-build-isolation"):
        assert flag in wheel["flags"], flag
    # cmru is the only project built from COPYed source; cli-extended is not.
    assert "/src/cmru" in wheel["flags"] and "/src/libraries/cli-extended" not in code
    assert "--no-index --no-deps /tmp/internal-wheels/*.whl" in code
    # The PyPI-bound requirements come from the refusing generator.
    assert "gen-requirements.py --root /src" in code
    assert "pip install --no-cache-dir -r /tmp/tester-requirements.txt" in code
    # Relative layout cmru's pyproject packages (`../libraries/worktree/src`);
    # cli-extended source is NOT copied (it is a wheel dependency).
    assert "COPY libraries/worktree/ /src/libraries/worktree/" in code
    assert not re.search(r"^COPY libraries/\s", code, re.MULTILINE)


def test_dockerfile_asserts_where_imports_come_from_and_that_cli_extended_is_the_release():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "import cli_extended" in text and "import worktree" in text
    assert "\n    assert location.startswith(site)," in text and 'site = "/opt/tester-venv/"' in text
    assert '\nassert version.endswith("+tester.unified"), (' in text
    assert 'assert "+" not in released' in text


def _pip_commands(code: str) -> list[str]:
    """Every ``pip ...`` command of the Dockerfile's RUN lines, continuation
    lines joined, split at ``&&``."""
    flat = re.sub(r"\\\n\s*", " ", code)
    commands = []
    for line in flat.splitlines():
        for part in line.split("&&"):
            if re.search(r"\bpip\b", part):
                commands.append(" ".join(part.split()))
    return commands


def test_dockerfile_no_cli_extended_reaches_pip_except_the_sha256_verified_release_wheel():
    """D8: the only pip command that may name cli-extended installs the wheel the
    fetcher verified, offline and without dependency resolution; every other pip
    command either is offline or takes only the generator's (skipped) closure."""
    code = _dockerfile_run_lines()
    commands = _pip_commands(code)
    assert commands
    release = [c for c in commands if "cli-extended" in c.lower() or "cli_extended" in c.lower()]
    assert len(release) == 1, release
    assert re.search(
        r"pip install --no-cache-dir --no-index --no-deps /tmp/cli-extended-release/\*\.whl$",
        release[0],
    ), release[0]
    for command in commands:
        if "--no-index" in command:
            continue
        # An index-capable pip may only read the generator output or upgrade itself.
        assert re.search(r"-r /tmp/(tester|internal-build)-requirements\.txt$|--upgrade pip$", command), command
    assert "/src/libraries/cli-extended" not in code
    assert "libraries/cli-extended" not in code


def test_dockerfile_fetches_the_release_by_sha256_before_cmru_is_installed():
    code = _dockerfile_run_lines()
    flat = re.sub(r"\\\n\s*", " ", code)
    fetch = flat.index("fetch-cli-extended.py")
    install_release = flat.index("/tmp/cli-extended-release/*.whl")
    build_cmru = flat.index("pip wheel")
    install_cmru = flat.index("/tmp/internal-wheels/*.whl")
    assert fetch < install_release < build_cmru < install_cmru
    # Pointer or pin, both through the digest-verifying fetcher; no bare index install.
    assert "CLI_EXTENDED_POINTER_URL" in code and "--sha256" in flat and "--min-version" in flat
    assert re.search(r"^ARG CLI_EXTENDED_POINTER_URL=https://github\.com/volkb79-2/vbpub/releases/download/"
                     r"cli-extended-latest/latest\.json$", DOCKERFILE.read_text("utf-8"), re.MULTILINE)
    # The requirements file is generated before pip reads it, and the fetcher is COPYed.
    assert "COPY tester-unified/fetch-cli-extended.py" in code


def test_dockerfile_pins_the_released_asset_and_digest_by_default_and_latest_is_opt_in():
    """D-3: the repo carries the sha256 pin (the pointer cannot vouch for itself);
    `latest.json` mode needs an explicit CLI_EXTENDED_RESOLVE=latest build arg."""
    text = DOCKERFILE.read_text("utf-8")
    args = dict(re.findall(r"^ARG (CLI_EXTENDED_\w+)=(\S*)$", text, re.MULTILINE))
    assert args["CLI_EXTENDED_RESOLVE"] == "pinned"
    url, digest = args["CLI_EXTENDED_WHEEL_URL"], args["CLI_EXTENDED_WHEEL_SHA256"]
    fetcher = _load_fetcher()
    assert fetcher._check_url(url) == url  # the allowlisted release prefix
    named = fetcher._WHEEL.fullmatch(url.rsplit("/", 1)[-1])
    assert named is not None
    declared = tomllib.loads((REPO_ROOT / "cmru" / "pyproject.toml").read_text("utf-8"))
    (spec,) = [d for d in declared["project"]["dependencies"] if gen.requirement_name(d) == "cli-extended"]
    assert fetcher._at_least(named["version"], spec.replace(" ", "").split(">=")[1])
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    # PKG-4 N8: the literal default digest, verified against the downloaded
    # cli_extended-0.2.0 wheel; a changed pin must be a deliberate edit here too.
    assert digest == "84ec3db81e8a4eaa931e57786f9b07395382af2601562f067b56fadbf3f3b58b"
    flat =" ".join(re.sub(r"\\\n\s*", " ", _dockerfile_run_lines()).split())
    # pinned branch passes the pin, latest branch passes only the pointer, anything else fails the build.
    assert re.search(r'pinned\) set -- --wheel-url "\$\{CLI_EXTENDED_WHEEL_URL\}" '
                     r'--sha256 "\$\{CLI_EXTENDED_WHEEL_SHA256\}" ;;', flat)
    assert re.search(r'latest\) set -- --pointer-url "\$\{CLI_EXTENDED_POINTER_URL\}" ;;', flat)
    assert re.search(r'\*\) echo "[^"]*" >&2; exit 1 ;;', flat)
    assert 'fetch-cli-extended.py --dest /tmp/cli-extended-release --min-version 0.2.0 "$@"' in flat


def test_dockerfile_fetch_floor_equals_cmru_declared_floor():
    code = re.sub(r"\\\n\s*", " ", _dockerfile_run_lines())
    floor = re.search(r"fetch-cli-extended\.py .*?--min-version (\S+)", code).group(1)
    declared = tomllib.loads((REPO_ROOT / "cmru" / "pyproject.toml").read_text("utf-8"))
    (spec,) = [d for d in declared["project"]["dependencies"] if gen.requirement_name(d) == "cli-extended"]
    assert spec.replace(" ", "") == f"cli-extended>={floor}"


# --- fetch-cli-extended.py: the digest is mandatory and checked before any file exists

FETCHER = REPO_ROOT / "tester-unified" / "fetch-cli-extended.py"


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_cli_extended", FETCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BASE = "https://github.com/volkb79-2/vbpub/releases/download/cli-extended-v0.2.0/"
_WHEEL_NAME = "cli_extended-0.2.0-py3-none-any.whl"


def _fake_release(payload: bytes = b"wheel-bytes", **pointer):
    import hashlib
    import json
    body = {"version": "0.2.0", "asset": _WHEEL_NAME, "url": _BASE + _WHEEL_NAME,
            "sha256": hashlib.sha256(payload).hexdigest()}
    body.update(pointer)
    served = {
        "https://github.com/volkb79-2/vbpub/releases/download/cli-extended-latest/latest.json":
            json.dumps(body).encode(),
        _BASE + _WHEEL_NAME: payload,
    }

    def fetch(url):
        return served[url]

    return fetch, hashlib.sha256(payload).hexdigest()


def test_fetcher_installs_nothing_unless_the_pointer_digest_matches(tmp_path):
    fetcher = _load_fetcher()
    fetch, digest = _fake_release()
    wheel = fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "ok", "0.2.0", fetch)
    assert wheel.read_bytes() == b"wheel-bytes" and wheel.name == _WHEEL_NAME

    tampered, _ = _fake_release(sha256="0" * 64)
    with pytest.raises(SystemExit, match="sha256 mismatch"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "bad", "0.2.0", tampered)
    assert not (tmp_path / "bad").exists()  # nothing written before the digest matched


@pytest.mark.parametrize("digest", [None, "", "abc", "A" * 64, 7])
def test_fetcher_refuses_a_pointer_without_a_valid_sha256(tmp_path, digest):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release(sha256=digest)
    with pytest.raises(SystemExit, match="no valid sha256"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "x", "0.2.0", fetch)
    assert not (tmp_path / "x").exists()


def test_fetcher_refuses_foreign_hosts_old_versions_and_odd_names(tmp_path):
    fetcher = _load_fetcher()
    with pytest.raises(SystemExit, match="not under"):
        fetcher.fetch_wheel("https://evil.invalid/latest.json", None, None, tmp_path, "0.2.0",
                            lambda url: b"")
    foreign, _ = _fake_release(url="https://pypi.org/packages/" + _WHEEL_NAME)
    with pytest.raises(SystemExit, match="not under"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path, "0.2.0", foreign)
    fetch, _ = _fake_release()
    with pytest.raises(SystemExit, match="older than the declared floor"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path, "0.3.0", fetch)
    odd, _ = _fake_release(url=_BASE + "cmru-0.2.0-py3-none-any.whl", asset="cmru-0.2.0-py3-none-any.whl")
    with pytest.raises(SystemExit, match="not a cli_extended"):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path, "0.2.0", odd)


@pytest.mark.parametrize("field, value, message", [
    ("asset", "cli_extended-0.9.9-py3-none-any.whl", "asset .* is not the url's file name"),
    ("asset", None, "asset None is not the url's file name"),
    ("version", "0.3.0", "version '0.3.0' is not the wheel's '0.2.0'"),
    ("version", None, "version None is not the wheel's '0.2.0'"),
])
def test_fetcher_cross_checks_the_pointer_asset_and_version_against_the_url(tmp_path, field, value, message):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release(**{field: value})
    with pytest.raises(SystemExit, match=message):
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "x", "0.2.0", fetch)
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize("version, floor, expected", [
    ("0.2", "0.2.0", True), ("0.2.0", "0.2", True), ("0.2.0.0", "0.2", True),
    ("0.1.9", "0.2", False), ("0.2", "0.2.1", False), ("1", "0.2.0", True),
])
def test_fetcher_pads_version_tuples_before_comparing_to_the_floor(version, floor, expected):
    assert _load_fetcher()._at_least(version, floor) is expected


def test_fetcher_turns_a_failed_wheel_download_into_a_clean_exit(tmp_path):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release()

    def pointer_only(url):
        if url.endswith(_WHEEL_NAME):
            raise OSError("connection reset")
        return fetch(url)

    with pytest.raises(SystemExit) as caught:
        fetcher.fetch_wheel(fetcher.POINTER_URL, None, None, tmp_path / "x", "0.2.0", pointer_only)
    assert caught.value.code == "fetch-cli-extended: cannot download " + _BASE + _WHEEL_NAME + ": connection reset"
    assert not (tmp_path / "x").exists()


# --- B2: HTTPS only across redirects, to allowlisted hosts only ---------------

@pytest.mark.parametrize("target, message", [
    ("http://github.com/volkb79-2/x.whl", "not https"),
    ("http://objects.githubusercontent.com/x", "not https"),
    ("ftp://github.com/x", "not https"),
    ("https://evil.invalid/x.whl", "host not allowed"),
    ("https://github.com.evil.invalid/x.whl", "host not allowed"),
    ("https://pypi.org/x.whl", "host not allowed"),
])
def test_redirect_handler_refuses_non_https_and_foreign_hosts(target, message):
    import urllib.request
    fetcher = _load_fetcher()
    handler = fetcher._HttpsOnlyRedirects()
    request = urllib.request.Request("https://github.com/volkb79-2/vbpub/releases/download/x")
    with pytest.raises(SystemExit, match=message):
        handler.redirect_request(request, None, 302, "Found", {}, target)


@pytest.mark.parametrize("host", [
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
    "raw.githubusercontent.com",
])
def test_redirect_handler_follows_https_to_each_allowlisted_host(host):
    import urllib.request
    fetcher = _load_fetcher()
    request = urllib.request.Request("https://github.com/volkb79-2/vbpub/releases/download/x")
    followed = fetcher._HttpsOnlyRedirects().redirect_request(
        request, None, 302, "Found", {}, f"https://{host}/a/b?sig=1")
    assert followed.full_url == f"https://{host}/a/b?sig=1"


def test_real_opener_refuses_an_http_redirect_served_by_a_fake_origin():
    """End to end through the real opener (no fake fetch): a local origin that
    302s to http:// and to a foreign https host; the origin's own host is allowed
    as the pointer host, the redirect targets are not."""
    import http.server
    import threading

    fetcher = _load_fetcher()
    targets = {"/to-http": "http://127.0.0.1:1/x", "/to-foreign": "https://evil.invalid/x"}

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", targets[self.path])
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(SystemExit, match="not https"):
            fetcher._urlopen_bytes(base + "/to-http")
        with pytest.raises(SystemExit, match="host not allowed"):
            fetcher._urlopen_bytes(base + "/to-foreign")
    finally:
        server.shutdown()
        server.server_close()


def test_fetcher_pinned_asset_needs_both_url_and_digest_and_skips_the_pointer(tmp_path):
    fetcher = _load_fetcher()
    fetch, digest = _fake_release()
    pointer = fetcher.POINTER_URL

    def no_pointer(url):
        assert url != pointer, "a pinned fetch must not read the pointer"
        return fetch(url)

    wheel = fetcher.fetch_wheel(pointer, _BASE + _WHEEL_NAME, digest, tmp_path / "p", "0.2.0", no_pointer)
    assert wheel.name == _WHEEL_NAME
    with pytest.raises(SystemExit, match="together"):
        fetcher.fetch_wheel(pointer, _BASE + _WHEEL_NAME, None, tmp_path / "q", "0.2.0", fetch)
    with pytest.raises(SystemExit, match="together"):
        fetcher.fetch_wheel(pointer, None, digest, tmp_path / "q", "0.2.0", fetch)
    with pytest.raises(SystemExit, match="no valid sha256"):
        fetcher.fetch_wheel(pointer, _BASE + _WHEEL_NAME, "xyz", tmp_path / "q", "0.2.0", fetch)


def test_fetcher_main_prints_the_wheel_path_and_treats_empty_pin_args_as_unset(tmp_path, monkeypatch, capsys):
    fetcher = _load_fetcher()
    fetch, _ = _fake_release()
    monkeypatch.setattr(fetcher, "_urlopen_bytes", fetch)
    # The Dockerfile passes unset ARGs as empty strings.
    assert fetcher.main(["--dest", str(tmp_path / "m"), "--min-version", "0.2.0",
                         "--pointer-url", fetcher.POINTER_URL,
                         "--wheel-url", "", "--sha256", ""]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "m" / _WHEEL_NAME)
    assert (tmp_path / "m" / _WHEEL_NAME).read_bytes() == b"wheel-bytes"


def test_fetcher_refuses_unreadable_or_malformed_pointers(tmp_path):
    fetcher = _load_fetcher()
    pointer = fetcher.POINTER_URL

    def broken(url):
        raise OSError("down")

    with pytest.raises(SystemExit, match="cannot read the release pointer"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", broken)
    with pytest.raises(SystemExit, match="cannot read the release pointer"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", lambda url: b"{not json")
    with pytest.raises(SystemExit, match="not a JSON object"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", lambda url: b"[]")
    with pytest.raises(SystemExit, match="no url"):
        fetcher.fetch_wheel(pointer, None, None, tmp_path, "0.2.0", lambda url: b"{}")
    with pytest.raises(SystemExit, match="plain release version"):
        fetcher.fetch_wheel(pointer, _BASE + "cli_extended-0.2.0rc1-py3-none-any.whl", "a" * 64,
                            tmp_path, "0.2.0", lambda url: b"")


def _dockerignore_included(relative: str) -> bool:
    """Docker's .dockerignore semantics: the LAST matching pattern decides;
    ``!`` re-includes. Patterns here are plain paths/globs without ``**``."""
    included = True
    parts = relative.split("/")
    candidates = ["/".join(parts[:depth]) for depth in range(1, len(parts) + 1)]
    for raw in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        pattern = pattern.lstrip("!")
        if pattern.startswith("**/"):
            continue  # global noise rules (.git, __pycache__ ...) are not relevant here
        # `*` never crosses a path separator; a pattern also applies to a path
        # whose parent directory it matches.
        regex = re.compile("".join("[^/]*" if char == "*" else re.escape(char) for char in pattern))
        if any(regex.fullmatch(candidate) for candidate in candidates):
            included = negate
    return included


@pytest.mark.parametrize("path", [
    "tester-unified/gen-requirements.py",
    "tester-unified/fetch-cli-extended.py",
    "libraries/worktree/src/worktree/__init__.py",
    "cmru/pyproject.toml",
])
def test_dockerignore_admits_every_file_the_offline_build_needs(path):
    assert _dockerignore_included(path), path


@pytest.mark.parametrize("path", [
    "tester-unified/run",
    "tester-unified/Dockerfile",
    # cli-extended is a wheel dependency: the tester image copies none of its
    # source or build inputs (the src/cli_extended whitelist is nyxloomd's).
    "libraries/cli-extended/pyproject.toml",
    "libraries/cli-extended/README.md",
    "libraries/cli-extended/tests/test_x.py",
    "libraries/worktree/tests/test_core.py",
    ".git/config",
])
def test_dockerignore_still_excludes_everything_else(path):
    assert not _dockerignore_included(path), path


def test_refusal_set_includes_every_pyproject_name_under_the_root(tmp_path):
    _all_projects(tmp_path)
    assert "foo-lib" not in gen.derive_internal(tmp_path)
    _tree(tmp_path, libraries__foo='[project]\nname = "Foo_Lib"\n', brandnew='[project]\nname = "brand.new"\n')
    derived = gen.derive_internal(tmp_path)
    assert {"foo-lib", "brand-new"} <= derived
    assert gen.ESTATE_INTERNAL <= derived  # the static list stays as a floor


def test_new_library_name_is_refused_as_a_dependency(tmp_path):
    _all_projects(tmp_path, nyxloom=(
        '[project]\nname = "nyxloom"\ndependencies = ["foo_lib>=1"]\n'
        '[project.optional-dependencies]\ntest = []\n'
    ))
    assert "foo_lib>=1" in list(gen.requirements(tmp_path))  # unknown name: third-party
    _tree(tmp_path, libraries__foo='[project]\nname = "foo-lib"\n')
    with pytest.raises(SystemExit, match="'foo-lib'"):
        list(gen.requirements(tmp_path))
    with pytest.raises(SystemExit, match="'foo-lib'"):
        list(gen.build_requirements(
            _tree(tmp_path / "y", libraries__foo='[project]\nname = "foo-lib"\n',
                  cmru='[build-system]\nrequires = ["foo-lib"]\n'),
            ["cmru/pyproject.toml"],
        ))


def test_dockerfile_builds_cmru_from_the_build_requires_mode():
    """The throwaway build venv must get the pinned backends of the one internal
    project it builds (a dropped one would only fail at image build time)."""
    code = _dockerfile_run_lines()
    match = re.search(r"--build-requires (?P<paths>[^\n\\]*)", code)
    assert match is not None
    assert match["paths"].split() == ["cmru/pyproject.toml"]
    wheel = re.search(r"pip wheel (?P<flags>[^\n]*\\\n[^\n]*)", code)
    assert "/src/cmru" in wheel["flags"]


def test_every_dockerfile_copy_source_is_admitted_by_the_dockerignore():
    """`.dockerignore` includes exactly what the Dockerfile COPYs: each COPY
    source is admitted and the tester image copies no cli-extended file."""
    sources = []
    for line in _dockerfile_run_lines().splitlines():
        parts = line.split()
        if parts and parts[0] == "COPY":
            sources.extend(part.rstrip("/") for part in parts[1:-1])
    assert "tester-unified/fetch-cli-extended.py" in sources
    for source in sources:
        assert _dockerignore_included(source), source
        assert "cli-extended" not in source.replace("fetch-cli-extended.py", "")
