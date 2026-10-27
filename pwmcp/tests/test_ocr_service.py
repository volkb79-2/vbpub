"""PWMCP-11 phase 1: the OCR sidecar service (containers/ocr/ocr_service.py), all offline.

The service runs in-process on a loopback port. Recognition is exercised against a FAKE
``tesseract`` executable (a tiny script emitting canned TSV and logging its argv), so these
tests verify OUR contract: parameter validation, limits, TSV parsing, box un-scaling and error
JSON. What they cannot verify is real Tesseract's behaviour; the one real-engine test at the
bottom skips with an explicit reason unless the binary, Pillow and DejaVu Sans Mono exist.
"""

from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
import shutil
import socket
import stat
import sys
import threading
import time
from pathlib import Path

import pytest
from _testdeps import REQUIRED

PWMCP_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = PWMCP_ROOT / "containers" / "ocr" / "ocr_service.py"

_spec = importlib.util.spec_from_file_location("pwmcp_ocr_service", SERVICE_PATH)
assert _spec and _spec.loader
svc = importlib.util.module_from_spec(_spec)
sys.modules["pwmcp_ocr_service"] = svc
_spec.loader.exec_module(svc)

PIL_REASON = "Pillow is not installed (the sidecar image installs python3-pil); image-decoding tests skipped"
try:
    from PIL import Image  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - environment dependent
    Image = None
# The registered gate sets PWMCP_REQUIRE_TEST_DEPS=1 (assay.toml lane env): there a missing Pillow FAILS
# these tests (conftest.py fixture) instead of letting ~40 of them skip and the gate go green.
needs_pil = (
    pytest.mark.usefixtures("require_pillow") if REQUIRED else pytest.mark.skipif(Image is None, reason=PIL_REASON)
)

# Real Tesseract TSV (levels 1-4 are layout rows with conf -1 and no text; level 5 are words).
TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t200\t100\t-1\t",
    "2\t1\t1\t0\t0\t0\t10\t10\t180\t40\t-1\t",
    "3\t1\t1\t1\t0\t0\t10\t10\t180\t40\t-1\t",
    "4\t1\t1\t1\t1\t0\t10\t10\t180\t16\t-1\t",
    "5\t1\t1\t1\t1\t1\t10\t10\t80\t16\t96.5\tDebian",
    "5\t1\t1\t1\t1\t2\t100\t10\t90\t16\t88.25\tinstaller",
    "4\t1\t1\t1\t2\t0\t10\t34\t60\t16\t-1\t",
    "5\t1\t1\t1\t2\t1\t10\t34\t60\t16\t42\tmaybe",
    "5\t1\t1\t1\t2\t2\t80\t34\t10\t16\t-1\t",  # a word row without a usable confidence
    "5\t1\t1\t1\t2\t3\t96\t34\t10\t16\t90\t ",  # blank text
    "",
])

FAKE = """#!{python}
import json, os, struct, sys, time
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode")).read().strip() if os.path.exists(os.path.join(here, "mode")) else "ok"
args = sys.argv[1:]
if args == ["--version"]:
    print("tesseract 5.3.4" if mode != "badversion" else "who knows")
    sys.exit(0)
if args == ["--list-langs"]:
    print('List of available languages in "/usr/share/tessdata/" (2):')
    if mode != "osdonly":
        print("eng")
    print("osd")
    sys.exit(0)
data = sys.stdin.buffer.read()
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(json.dumps({{"argv": args, "stdin_len": len(data),
                          "png": list(struct.unpack(">II", data[16:24])) if data[:8] == b"\\x89PNG\\r\\n\\x1a\\n" else None}}) + "\\n")
open(os.path.join(here, "started"), "w").write("1")
if mode == "sleep":
    time.sleep(float(open(os.path.join(here, "sleep")).read()))
if mode == "block":  # held until the test creates the "release" file: no wall-clock windows
    for _ in range(3000):
        if os.path.exists(os.path.join(here, "release")):
            break
        time.sleep(0.02)
if mode == "fail":
    sys.stderr.write("Error: could not initialise\\nfatal: boom\\n")
    sys.exit(3)
if mode == "empty":
    sys.stdout.write(open(os.path.join(here, "tsv")).read().splitlines()[0] + "\\n")
else:
    sys.stdout.write(open(os.path.join(here, "tsv")).read())
"""


class Fake:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.binary = directory / "tesseract"
        self.binary.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        (directory / "tsv").write_text(TSV, encoding="utf-8")

    def mode(self, name: str, sleep: float = 0.0) -> None:
        (self.dir / "mode").write_text(name, encoding="utf-8")
        (self.dir / "sleep").write_text(str(sleep), encoding="utf-8")

    def release(self) -> None:
        (self.dir / "release").write_text("1", encoding="utf-8")

    def wait_started(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while not (self.dir / "started").exists():
            assert time.monotonic() < deadline, "the engine never started"
            time.sleep(0.01)

    def calls(self) -> list[dict[str, object]]:
        log = self.dir / "calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def fake(tmp_path: Path) -> Fake:
    return Fake(tmp_path)


class Running:
    def __init__(self, config: object, registry: dict | None = None) -> None:
        self.server = svc.make_server(config, registry)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(
        self, method: str, target: str, body: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict, dict[str, str]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, target, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw), dict(response.getheaders())
        finally:
            conn.close()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start(fake: Fake, **overrides: object) -> Running:
    base = dict(host="127.0.0.1", port=0, tesseract=str(fake.binary), timeout_s=5.0, socket_timeout_s=2.0)
    base.update(overrides)
    return Running(svc.Config(**base))


@pytest.fixture
def running(fake: Fake):
    instance = start(fake)
    yield instance
    instance.close()


def png(width: int = 100, height: int = 50, fmt: str = "PNG", mode: str = "RGB") -> bytes:
    assert Image is not None
    out = io.BytesIO()
    Image.new(mode, (width, height), "white" if mode != "P" else 0).save(out, format=fmt)
    return out.getvalue()


# ------------------------------------------------------------------ pure logic (no Pillow needed)


def test_parse_tsv_keeps_only_real_words() -> None:
    words = svc.parse_tsv(TSV)
    assert [(w.text, w.conf, w.box, w.line) for w in words] == [
        ("Debian", 96.5, (10, 10, 80, 16), (1, 1, 1)),
        ("installer", 88.25, (100, 10, 90, 16), (1, 1, 1)),
        ("maybe", 42.0, (10, 34, 60, 16), (1, 1, 2)),
    ]


@pytest.mark.parametrize("text", ["", "\n", TSV.splitlines()[0], "garbage without tabs\n", "5\tnot\tenough\n"])
def test_parse_tsv_empty_or_malformed_output_is_no_words(text: str) -> None:
    assert svc.parse_tsv(text) == []


def test_parse_tsv_skips_rows_with_non_numeric_fields() -> None:
    row = "5\t1\tX\t1\t1\t1\t0\t0\t1\t1\t90\tword"
    assert svc.parse_tsv(row) == []


def test_parse_tsv_text_may_contain_a_tab() -> None:
    (word,) = svc.parse_tsv("5\t1\t1\t1\t1\t1\t0\t0\t5\t5\t80\ta\tb")
    assert word.text == "a\tb"


REGISTRY = {"tesseract": svc.UnavailableEngine("tesseract", "x"), "glyphgrid": svc.UnavailableEngine("glyphgrid")}


def test_params_defaults() -> None:
    assert svc.parse_params("", REGISTRY) == svc.Params("tesseract", "eng", 6, 1, None, 0.0)


def test_params_full_set() -> None:
    got = svc.parse_params("engine=glyphgrid&lang=eng%2Bdeu&psm=13&scale=3.0&whitelist=%3Brm+-rf&min_conf=60.5", REGISTRY)
    assert got == svc.Params("glyphgrid", "eng+deu", 13, 3, ";rm -rf", 60.5)


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("engine=nope", "unknown_engine"),
        ("engine=", "unknown_engine"),
        ("lang=eng;rm", "invalid_lang"),
        ("lang=..%2Fx", "invalid_lang"),
        ("lang=-l", "invalid_lang"),
        ("lang=eng%0A", "invalid_lang"),
        ("lang=", "invalid_lang"),
        ("psm=5", "invalid_psm"),
        ("psm=%2B6", "invalid_psm"),
        ("psm=abc", "invalid_psm"),
        ("psm=", "invalid_psm"),
        ("psm=6%3Brm", "invalid_psm"),
        ("scale=0", "invalid_scale"),
        ("scale=9", "invalid_scale"),
        ("scale=1.5", "invalid_scale"),
        ("scale=nan", "invalid_scale"),
        ("scale=inf", "invalid_scale"),
        ("scale=x", "invalid_scale"),
        ("whitelist=", "invalid_whitelist"),
        ("whitelist=a%0Ab", "invalid_whitelist"),
        ("whitelist=a%00b", "invalid_whitelist"),
        ("whitelist=caf%C3%A9", "invalid_whitelist"),
        ("whitelist=" + "a" * 129, "invalid_whitelist"),
        ("min_conf=-1", "invalid_min_conf"),
        ("min_conf=101", "invalid_min_conf"),
        ("min_conf=nan", "invalid_min_conf"),
        ("min_conf=x", "invalid_min_conf"),
        ("rm=-rf", "unknown_param"),
        ("psm=6&psm=7", "duplicate_param"),
    ],
)
def test_params_rejected_with_a_specific_code(query: str, code: str) -> None:
    with pytest.raises(svc.ServiceError) as caught:
        svc.parse_params(query, REGISTRY)
    assert caught.value.status == 400 and caught.value.code == code


def test_params_accept_injection_looking_whitelists_as_plain_data() -> None:
    for text in ['"', "'", "$(id)", "`id`", "a b", "\\", ";|&><*?"]:
        assert svc.parse_params("whitelist=" + __import__("urllib.parse").parse.quote(text), REGISTRY).whitelist == text
    assert svc.parse_params("whitelist=" + "a" * 128, REGISTRY).whitelist == "a" * 128


def test_config_from_env() -> None:
    assert svc.Config.from_env({}) == svc.Config()
    got = svc.Config.from_env({"OCR_PORT": "9", "OCR_MAX_BYTES": "5", "OCR_MAX_DIM": "7", "OCR_TIMEOUT_S": "1.5",
                               "OCR_BIND": "127.0.0.1", "OCR_TESSERACT": "/x/t"})
    assert (got.port, got.max_bytes, got.max_dim, got.timeout_s, got.host, got.tesseract) == (9, 5, 7, 1.5, "127.0.0.1", "/x/t")
    assert svc.Config().max_bytes == 8 * 1024 * 1024 and svc.Config().max_dim == 4096 and svc.Config().timeout_s == 20.0
    assert svc.Config().port == 8941
    more = svc.Config.from_env({"OCR_BODY_DEADLINE_S": "2.5", "OCR_MAX_SCALED_PIXELS": "99"})
    assert (more.body_deadline_s, more.max_scaled_pixels) == (2.5, 99)
    assert (svc.Config().body_deadline_s, svc.Config().max_scaled_pixels) == (10.0, 24_000_000)
    assert isinstance(svc.Config.from_env(), svc.Config)  # reads os.environ without raising


def test_unavailable_engine_is_a_clear_501() -> None:
    engine = svc.UnavailableEngine("glyphgrid")
    assert not engine.available() and engine.version() is None and engine.languages() == []
    with pytest.raises(svc.ServiceError) as caught:
        engine.recognize(b"", svc.Params(), 1.0)
    assert (caught.value.status, caught.value.code, caught.value.message) == (501, "engine_unavailable", "engine not implemented")


def test_build_registry_ships_tesseract_and_an_unavailable_glyphgrid() -> None:
    registry = svc.build_registry(svc.Config())
    assert sorted(registry) == ["glyphgrid", "tesseract"]
    assert isinstance(registry["tesseract"], svc.TesseractEngine)
    assert registry["glyphgrid"].available() is False


# ------------------------------------------------------------------ HTTP surface


def test_health_ok_and_unavailable(fake: Fake) -> None:
    ok = start(fake)
    gone = start(fake, tesseract=str(fake.dir / "missing"))
    try:
        assert ok.request("GET", "/health")[:2] == (200, {"status": "ok"})
        status, body, _ = gone.request("GET", "/health")
        assert status == 503 and body["status"] == "unavailable" and "not found" in body["reason"]
        fake.mode("osdonly")  # the binary runs but the default language's traineddata is missing
        no_data = start(fake)
        try:
            status, body, _ = no_data.request("GET", "/health")
            assert status == 503 and "traineddata" in body["reason"] and "eng" in body["reason"]
        finally:
            no_data.close()
    finally:
        ok.close()
        gone.close()


def test_engines_lists_names_versions_languages_and_unavailable_reason(running: Running) -> None:
    status, body, _ = running.request("GET", "/v1/engines")
    assert status == 200
    by_name = {e["name"]: e for e in body["engines"]}
    assert by_name["tesseract"] == {"name": "tesseract", "available": True, "version": "5.3.4", "languages": ["eng", "osd"]}
    assert by_name["glyphgrid"] == {"name": "glyphgrid", "available": False, "version": None, "languages": [],
                                    "reason": "engine not implemented"}


def test_engines_when_tesseract_missing(fake: Fake) -> None:
    gone = start(fake, tesseract=str(fake.dir / "missing"))
    try:
        entry = {e["name"]: e for e in gone.request("GET", "/v1/engines")[1]["engines"]}["tesseract"]
        assert entry["available"] is False and entry["languages"] == [] and entry["reason"] == "tesseract binary not found"
    finally:
        gone.close()


def test_version_falls_back_to_unknown(fake: Fake) -> None:
    fake.mode("badversion")
    assert svc.TesseractEngine(str(fake.binary)).version() == "unknown"


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/nope", 404, "not_found"),
        ("POST", "/nope", 404, "not_found"),
        ("GET", "/v1/ocr", 405, "method_not_allowed"),
        ("POST", "/health", 405, "method_not_allowed"),
        ("POST", "/v1/engines", 405, "method_not_allowed"),
    ],
)
def test_routing_errors_are_json(running: Running, method: str, path: str, status: int, code: str) -> None:
    got_status, body, _ = running.request(method, path, body=b"x" if method == "POST" else None)
    assert (got_status, body["code"]) == (status, code) and set(body) == {"error", "code"}


@needs_pil
def test_ocr_success_shape_and_exact_engine_argv(running: Running, fake: Fake) -> None:
    status, body, _ = running.request("POST", "/v1/ocr", png(100, 50))
    assert status == 200
    assert body["engine"] == "tesseract" and body["engine_version"] == "5.3.4"
    assert (body["lang"], body["psm"], body["width"], body["height"]) == ("eng", 6, 100, 50)
    assert isinstance(body["elapsed_ms"], int)
    assert body["text"] == "Debian installer\nmaybe"
    assert body["lines"] == [
        {"text": "Debian installer", "conf": 92.38, "conf_mean": 92.38, "conf_min": 88.25, "low_conf_words": 0,
         "box": [10, 10, 180, 16]},
        {"text": "maybe", "conf": 42.0, "conf_mean": 42.0, "conf_min": 42.0, "low_conf_words": 0, "box": [10, 34, 60, 16]},
    ]
    assert body["words"] == [
        {"text": "Debian", "conf": 96.5, "box": [10, 10, 80, 16], "line": 0, "low_conf": False},
        {"text": "installer", "conf": 88.25, "box": [100, 10, 90, 16], "line": 0, "low_conf": False},
        {"text": "maybe", "conf": 42.0, "box": [10, 34, 60, 16], "line": 1, "low_conf": False},
    ]
    assert (body["min_conf_applied"], body["low_conf_words"], body["drop_low_conf"], body["dropped_words"]) == (0.0, 0, False, 0)
    (call,) = fake.calls()
    assert call["argv"] == ["stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"]
    assert call["png"] == [100, 50]


@needs_pil
def test_ocr_forwards_validated_parameters_as_separate_argv_elements(running: Running, fake: Fake) -> None:
    hostile = ";rm -rf / $(id) `id` \"'"
    from urllib.parse import quote

    query = f"lang=eng%2Bosd&psm=7&whitelist={quote(hostile)}"
    status, _body, _ = running.request("POST", f"/v1/ocr?{query}", png())
    assert status == 200
    (call,) = fake.calls()
    assert call["argv"] == ["stdin", "stdout", "-l", "eng+osd", "--psm", "7", "-c",
                            f"tessedit_char_whitelist={hostile}", "tsv"]


@needs_pil
def test_scale_upscales_the_engine_input_and_boxes_return_in_original_pixels(running: Running, fake: Fake) -> None:
    status, body, _ = running.request("POST", "/v1/ocr?scale=2", png(100, 50))
    assert status == 200
    assert fake.calls()[0]["png"] == [200, 100]  # the engine saw the upscaled frame
    assert (body["width"], body["height"]) == (100, 50)  # reported in the ORIGINAL image
    assert body["words"][0]["box"] == [5, 5, 40, 8]  # 10,10,80,16 / 2
    assert body["words"][1]["box"] == [50, 5, 45, 8]
    assert body["lines"][0]["box"] == [5, 5, 90, 8]


@needs_pil
def test_scale_odd_factor_rounds_boxes(running: Running) -> None:
    body = running.request("POST", "/v1/ocr?scale=3", png(100, 50))[1]
    assert body["words"][0]["box"] == [3, 3, 27, 6]  # outward: floor(10/3), ceil(90/3)-3, ceil(26/3)-3


@needs_pil
def test_min_conf_flags_words_but_never_removes_them(running: Running) -> None:
    body = running.request("POST", "/v1/ocr?min_conf=90", png())[1]
    # the result is COMPLETE: every recognised word and the whole text, whatever its confidence
    assert [w["text"] for w in body["words"]] == ["Debian", "installer", "maybe"]
    assert body["text"] == "Debian installer\nmaybe"
    assert [w["low_conf"] for w in body["words"]] == [False, True, True]  # 96.5 ok; 88.25 and 42 flagged
    assert [(line["low_conf_words"], line["conf_min"]) for line in body["lines"]] == [(1, 88.25), (1, 42.0)]
    assert (body["min_conf_applied"], body["low_conf_words"], body["dropped_words"]) == (90.0, 2, 0)
    nothing_flagged = running.request("POST", "/v1/ocr", png())[1]
    assert nothing_flagged["low_conf_words"] == 0 and not any(w["low_conf"] for w in nothing_flagged["words"])
    everything = running.request("POST", "/v1/ocr?min_conf=100", png())[1]
    assert everything["text"] == "Debian installer\nmaybe" and everything["low_conf_words"] == 3


@needs_pil
def test_a_prompt_with_one_doubtful_word_comes_back_complete(running: Running, fake: Fake) -> None:
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    (fake.dir / "tsv").write_text(
        header
        + "5\t1\t1\t1\t1\t1\t0\t0\t60\t16\t96\tContinue?\n"
        + "5\t1\t1\t1\t1\t2\t70\t0\t60\t16\t10\t[y/N]\n",
        encoding="utf-8",
    )
    body = running.request("POST", "/v1/ocr?min_conf=50", png())[1]
    assert body["text"] == "Continue? [y/N]"  # the doubtful token is NOT silently deleted
    assert body["low_conf_words"] == 1
    assert body["lines"][0]["low_conf_words"] == 1 and body["lines"][0]["conf_min"] == 10.0
    assert body["lines"][0]["conf_mean"] == 53.0 == body["lines"][0]["conf"]
    assert [w["low_conf"] for w in body["words"]] == [False, True]


@needs_pil
def test_drop_low_conf_is_an_explicit_opt_in_that_reports_what_it_dropped(running: Running) -> None:
    body = running.request("POST", "/v1/ocr?min_conf=90&drop_low_conf=1", png())[1]
    assert [w["text"] for w in body["words"]] == ["Debian"] and body["text"] == "Debian"
    assert body["lines"] == [{"text": "Debian", "conf": 96.5, "conf_mean": 96.5, "conf_min": 96.5,
                              "low_conf_words": 0, "box": [10, 10, 80, 16]}]
    assert (body["drop_low_conf"], body["dropped_words"], body["low_conf_words"]) == (True, 2, 2)
    none_left = running.request("POST", "/v1/ocr?min_conf=100&drop_low_conf=1", png())[1]
    assert none_left["text"] == "" and none_left["lines"] == [] and none_left["words"] == []
    assert none_left["dropped_words"] == 3
    assert running.request("POST", "/v1/ocr?drop_low_conf=2", png())[1]["code"] == "invalid_drop_low_conf"
    assert running.request("POST", "/v1/ocr?drop_low_conf=0", png())[0] == 200


@needs_pil
def test_empty_recognition_is_a_valid_empty_result(running: Running, fake: Fake) -> None:
    fake.mode("empty")
    status, body, _ = running.request("POST", "/v1/ocr", png())
    assert status == 200 and (body["text"], body["lines"], body["words"]) == ("", [], [])


@needs_pil
@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_png_and_jpeg_are_accepted_including_palette_and_gray(running: Running, fmt: str) -> None:
    assert running.request("POST", "/v1/ocr", png(fmt=fmt))[0] == 200
    mode = "L"
    assert running.request("POST", "/v1/ocr", png(mode=mode, fmt=fmt))[0] == 200


@needs_pil
def test_palette_and_rgba_images_are_flattened_for_the_engine(running: Running) -> None:
    assert running.request("POST", "/v1/ocr", png(mode="P"))[0] == 200
    assert running.request("POST", "/v1/ocr", png(mode="RGBA"))[0] == 200


@needs_pil
def test_unsupported_language_is_a_400(running: Running) -> None:
    status, body, _ = running.request("POST", "/v1/ocr?lang=deu", png())
    assert status == 400 and body["code"] == "unsupported_lang" and "eng" in body["error"]


def test_engine_glyphgrid_is_501_with_the_documented_body(running: Running) -> None:
    status, body, _ = running.request("POST", "/v1/ocr?engine=glyphgrid", b"anything")
    assert status == 501 and body == {"error": "engine not implemented", "code": "engine_unavailable"}


def test_parameter_errors_come_before_the_image_is_looked_at(running: Running) -> None:
    status, body, _ = running.request("POST", "/v1/ocr?psm=5", b"not an image")
    assert (status, body["code"]) == (400, "invalid_psm")


def test_missing_tesseract_is_501(fake: Fake) -> None:
    gone = start(fake, tesseract=str(fake.dir / "missing"))
    try:
        status, body, _ = gone.request("POST", "/v1/ocr", b"x")
        assert (status, body["code"]) == (501, "engine_unavailable")
    finally:
        gone.close()


# -- limits


def test_oversized_body_is_413_without_reading_it(fake: Fake) -> None:
    small = start(fake, max_bytes=1000)
    try:
        status, body, _ = small.request("POST", "/v1/ocr", b"x" * 2000)
        assert (status, body["code"]) == (413, "payload_too_large")
    finally:
        small.close()


def test_content_length_is_required_and_chunked_is_refused(running: Running) -> None:
    for head in (b"POST /v1/ocr HTTP/1.0\r\n\r\n",
                 b"POST /v1/ocr HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
                 b"POST /v1/ocr HTTP/1.0\r\nContent-Length: abc\r\n\r\n"):
        with socket.create_connection(("127.0.0.1", running.port), timeout=5) as sock:
            sock.sendall(head)
            raw = b""
            while chunk := sock.recv(4096):
                raw += chunk
        assert raw.startswith(b"HTTP/1.0 411") and json.loads(raw.split(b"\r\n\r\n", 1)[1])["code"] == "length_required"


def test_empty_body_is_400(running: Running) -> None:
    status, body, _ = running.request("POST", "/v1/ocr", b"", {"Content-Length": "0"})
    assert (status, body["code"]) == (400, "empty_body")


def test_body_shorter_than_declared_is_400(running: Running) -> None:
    with socket.create_connection(("127.0.0.1", running.port), timeout=5) as sock:
        sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: 100\r\n\r\nshort")
        sock.shutdown(socket.SHUT_WR)
        raw = b""
        while chunk := sock.recv(4096):
            raw += chunk
    assert json.loads(raw.split(b"\r\n\r\n", 1)[1])["code"] == "short_body"


@needs_pil
def test_image_dimension_limit(fake: Fake) -> None:
    small = start(fake, max_dim=64)
    try:
        assert small.request("POST", "/v1/ocr", png(64, 64))[0] == 200
        for size in ((65, 10), (10, 65)):
            status, body, _ = small.request("POST", "/v1/ocr", png(*size))
            assert (status, body["code"]) == (413, "image_too_large")
    finally:
        small.close()


@needs_pil
def test_scaled_dimension_limit(running: Running) -> None:
    status, body, _ = running.request("POST", "/v1/ocr?scale=8", png(1100, 10))  # 8800 px wide once scaled
    assert (status, body["code"]) == (400, "invalid_scale") and "8192" in body["error"]
    assert running.request("POST", "/v1/ocr?scale=8", png(1000, 10))[0] == 200


@needs_pil
def test_default_limits_are_8mib_and_4096() -> None:
    config = svc.Config()
    big = png(4097, 10)
    assert len(big) < config.max_bytes
    with pytest.raises(svc.ServiceError) as caught:
        svc.prepare_image(big, svc.Params(), config)
    assert caught.value.code == "image_too_large"
    ok, width, height = svc.prepare_image(png(4096, 8), svc.Params(), config)
    assert (width, height) == (4096, 8) and ok.startswith(b"\x89PNG")


@needs_pil
def test_decompression_bomb_is_refused_before_decoding() -> None:
    # 20000x20000 declared in the header of a tiny file: refused on the header alone.
    config = svc.Config(max_dim=4096)
    bomb = png(20000, 20000, mode="1")
    with pytest.raises(svc.ServiceError) as caught:
        svc.prepare_image(bomb, svc.Params(), config)
    assert (caught.value.status, caught.value.code) == (413, "image_too_large")  # Pillow's bomb error, not a 415


@needs_pil
@pytest.mark.parametrize("body", [b"not an image at all", b"GIF89a\x01\x00\x01\x00", b"\x89PNG\r\n\x1a\ntruncated"])
def test_non_images_are_415(running: Running, body: bytes) -> None:
    status, resp, _ = running.request("POST", "/v1/ocr", body)
    assert status == 415 and resp["code"] == "unsupported_image"


@needs_pil
def test_gif_and_bmp_are_not_accepted_formats(running: Running) -> None:
    for fmt in ("GIF", "BMP"):
        out = io.BytesIO()
        Image.new("RGB", (8, 8)).save(out, format=fmt)
        status, resp, _ = running.request("POST", "/v1/ocr", out.getvalue())
        assert (status, resp["code"]) == (415, "unsupported_image") and "PNG or JPEG" in resp["error"]


@needs_pil
def test_truncated_pixel_data_is_415(running: Running) -> None:
    data = png(200, 200)
    status, resp, _ = running.request("POST", "/v1/ocr", data[: len(data) // 2])
    assert (status, resp["code"]) == (415, "unsupported_image")


@needs_pil
def test_recognition_timeout_is_504_and_the_slot_is_released(fake: Fake) -> None:
    fake.mode("block")  # never released: only the service's own timeout can end it
    quick = start(fake, timeout_s=0.4)
    try:
        status, body, _ = quick.request("POST", "/v1/ocr", png())
        assert (status, body["code"]) == (504, "timeout")
        fake.mode("ok")
        assert quick.request("POST", "/v1/ocr", png())[0] == 200
    finally:
        quick.close()


@needs_pil
def test_one_recognition_at_a_time_second_caller_gets_429(fake: Fake) -> None:
    fake.mode("block")
    both = start(fake)
    first: list[tuple[int, dict, dict]] = []
    thread = threading.Thread(target=lambda: first.append(both.request("POST", "/v1/ocr", png())))
    try:
        thread.start()
        fake.wait_started()  # the first request is provably inside the engine now
        status, body, headers = both.request("POST", "/v1/ocr", png())
        assert (status, body["code"]) == (429, "busy") and headers.get("Retry-After") == "1"
        fake.release()
        thread.join(timeout=30)
        assert first[0][0] == 200  # the in-flight request was not disturbed
        fake.mode("ok")
        assert both.request("POST", "/v1/ocr", png())[0] == 200  # slot free again
    finally:
        fake.release()
        thread.join(timeout=30)
        both.close()


@needs_pil
def test_engine_failure_is_500_with_a_fixed_message(
    running: Running, fake: Fake, capfd: pytest.CaptureFixture[str]
) -> None:
    fake.mode("fail")
    status, body, _ = running.request("POST", "/v1/ocr", png())
    # the engine's own stderr never reaches the client ...
    assert status == 500 and body == {"error": "the recognition engine failed", "code": "engine_failed"}
    # ... it is logged server-side only
    err = capfd.readouterr().err
    assert "exited 3" in err and "boom" in err


@needs_pil
def test_unstartable_engine_is_503(running: Running, fake: Fake) -> None:
    fake.binary.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    status, body, _ = running.request("POST", "/v1/ocr", png())
    assert (status, body["code"]) == (503, "engine_unavailable")


@needs_pil
def test_unexpected_exception_is_a_generic_500_that_leaks_nothing(fake: Fake) -> None:
    class Exploding(svc.UnavailableEngine):
        def available(self) -> bool:
            return True

        def recognize(self, png_bytes: bytes, params: object, timeout_s: float) -> list:
            raise RuntimeError("secret internal detail " + repr(png_bytes[:8]))

    server = Running(svc.Config(host="127.0.0.1", port=0), {"tesseract": Exploding("tesseract")})
    try:
        status, body, _ = server.request("POST", "/v1/ocr", png())
        assert (status, body) == (500, {"error": "internal error", "code": "internal_error"})
    finally:
        server.close()


def test_client_that_vanishes_mid_upload_does_not_crash_the_service(running: Running) -> None:
    with socket.create_connection(("127.0.0.1", running.port), timeout=5) as sock:
        sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: 100000\r\n\r\npartial")
    # The slot must be free again: a POST (which needs the slot) is answered by validation, not 429.
    # The handler notices the vanished client within moments; poll for that instead of sleeping.
    deadline = time.monotonic() + 10
    while True:
        status, body, _ = running.request("POST", "/v1/ocr?psm=5", b"x")
        if status != 429 or time.monotonic() > deadline:
            break
        time.sleep(0.02)
    assert (status, body["code"]) == (400, "invalid_psm")


def test_stalled_upload_times_out_and_frees_the_slot(fake: Fake) -> None:
    quick = start(fake, socket_timeout_s=0.3)
    try:
        with socket.create_connection(("127.0.0.1", quick.port), timeout=5) as sock:
            sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: 1000\r\n\r\nx")
            time.sleep(0.8)  # no more bytes: the handler gives up
        assert quick.request("POST", "/v1/ocr?psm=5", b"x")[1]["code"] == "invalid_psm"  # not 429
    finally:
        quick.close()


def test_access_log_omits_query_and_body(running: Running, capfd: pytest.CaptureFixture[str]) -> None:
    running.request("POST", "/v1/ocr?whitelist=SECRETCHARS&psm=5", b"IMAGEBYTES")
    time.sleep(0.1)
    err = capfd.readouterr().err
    assert "SECRETCHARS" not in err and "IMAGEBYTES" not in err


# ------------------------------------------------------------------ packaging facts


def test_dockerfile_runs_non_root_and_keeps_the_declared_defaults() -> None:
    text = (PWMCP_ROOT / "containers" / "ocr" / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 1000:1000" in text and "tesseract-ocr-eng" in text and "python3-pil" in text
    assert "--no-install-recommends" in text and "OCR_MAX_BYTES=8388608" in text and "OCR_MAX_DIM=4096" in text
    assert "OCR_TIMEOUT_S=20" in text and "nice" in text and "COPY containers/ocr/ocr_service.py" in text
    assert "OCR_MAX_SCALED_PIXELS=24000000" in text and "OCR_BODY_DEADLINE_S=10" in text
    assert "HEALTHCHECK" in text and "/health" in text
    assert "@${DEBIAN_IMAGE_DIGEST}" in text and "KNOWN VIOLATION" not in text  # base pinned by digest


# ------------------------------------------------------------------ the one REAL-engine test

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
REAL_SKIP = (
    "real-Tesseract test skipped: needs the `tesseract` binary on PATH "
    f"(found: {shutil.which('tesseract')}), Pillow (installed: {Image is not None}) and "
    f"DejaVu Sans Mono at {FONT} (present: {FONT.is_file()})"
)


@pytest.mark.skipif(shutil.which("tesseract") is None or Image is None or not FONT.is_file(), reason=REAL_SKIP)
def test_real_tesseract_recognises_rendered_console_lines() -> None:  # pragma: no cover - needs tesseract
    from PIL import ImageDraw, ImageFont

    lines = ["Debian GNU/Linux installer", "Install", "Graphical install", "Advanced options"]
    font = ImageFont.truetype(str(FONT), 20)
    image = Image.new("RGB", (640, 40 + 30 * len(lines)), "black")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((16, 16 + 30 * index), line, font=font, fill="white")
    out = io.BytesIO()
    image.save(out, format="PNG")

    server = Running(svc.Config(host="127.0.0.1", port=0, timeout_s=30.0))
    try:
        status, body, _ = server.request("POST", "/v1/ocr?scale=2", out.getvalue())
    finally:
        server.close()
    assert status == 200, body
    recognised = " ".join(body["text"].lower().split())
    for expected in ("debian", "installer", "graphical", "advanced options"):
        assert expected in recognised, (expected, body["text"])
    assert all(0 <= word["conf"] <= 100 for word in body["words"])
    assert sum(w["conf"] for w in body["words"]) / len(body["words"]) > 50


def test_real_engine_skip_reason_is_explicit_when_tesseract_is_absent() -> None:
    if shutil.which("tesseract") is None:
        assert "needs the `tesseract` binary" in REAL_SKIP and "found: None" in REAL_SKIP
