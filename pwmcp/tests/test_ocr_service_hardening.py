"""PWMCP-11 phase 1, review follow-up: slow clients, signals, drains, TSV/length edge cases,
image-mode handling and the scaled-pixel cap. Builds on the helpers in test_ocr_service.py."""

from __future__ import annotations

import io
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_ocr_service import SERVICE_PATH, Fake, Image, Running, fake, needs_pil, png, running, start, svc  # noqa: F401

# ------------------------------------------------------------------ TSV


def test_parse_tsv_does_not_split_words_on_exotic_line_breaks() -> None:
    row = "5\t1\t1\t1\t1\t1\t0\t0\t5\t5\t80\t"
    for odd in ("a\x0bb", "a\x0cb", "a\x1cb", "a\x1db", "a\x1eb", "a\x85b", "a b", "a b"):
        (word,) = svc.parse_tsv(row + odd + "\n")
        assert word.text == odd
    two = svc.parse_tsv(row + "x\r\n" + row + "y\r\n")  # CRLF output is still understood
    assert [w.text for w in two] == ["x", "y"]


# ------------------------------------------------------------------ Content-Length


def _raw(port: int, head: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(head)
        raw = b""
        while chunk := sock.recv(4096):
            raw += chunk
    return raw


def test_non_ascii_digit_content_length_is_a_411_not_a_500(running) -> None:
    for value in (b"\xb2", b"1\xb2", b"-5", b"+5", b" 5x", b"\xd9\xa5"):
        raw = _raw(running.port, b"POST /v1/ocr HTTP/1.0\r\nContent-Length: " + value + b"\r\n\r\n")
        assert raw.startswith(b"HTTP/1.0 411"), (value, raw[:40])
        assert json.loads(raw.split(b"\r\n\r\n", 1)[1])["code"] == "length_required"


def test_absurdly_long_content_length_is_413(running) -> None:
    raw = _raw(running.port, b"POST /v1/ocr HTTP/1.0\r\nContent-Length: " + b"9" * 5000 + b"\r\n\r\n")
    assert raw.startswith(b"HTTP/1.0 413")


# ------------------------------------------------------------------ slow clients / slot


@needs_pil
def test_trickling_client_hits_the_total_body_deadline_and_frees_the_slot(fake) -> None:
    """Every recv arrives in time, the body as a whole does not: 408, and the slot is released."""

    quick = start(fake, body_deadline_s=0.6, socket_timeout_s=5.0)
    try:
        started = time.monotonic()
        raw = b""
        with socket.create_connection(("127.0.0.1", quick.port), timeout=10) as sock:
            sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: 1000\r\n\r\n")
            sock.settimeout(0.1)
            while time.monotonic() - started < 5 and not raw:
                try:
                    sock.sendall(b"x")  # one byte every ~0.2 s: never idle long enough for a recv timeout
                except OSError:
                    break
                time.sleep(0.2)
                try:
                    raw = sock.recv(4096)
                except TimeoutError:
                    continue
                if not raw:
                    break
        assert raw.startswith(b"HTTP/1.0 408") and b"request_timeout" in raw
        assert time.monotonic() - started < 4  # cut off near the 0.6 s deadline, not the 5 s recv timeout
        assert quick.request("POST", "/v1/ocr", png())[0] == 200  # slot free: processed, not 429
    finally:
        quick.close()


@needs_pil
def test_busy_slot_is_released_on_every_error_path(running) -> None:
    for query, body in (("psm=5", png()), ("", b"junk"), ("lang=deu", png()), ("engine=glyphgrid", b"x")):
        assert running.request("POST", "/v1/ocr?" + query, body)[0] in (400, 415, 501)
        assert running.request("POST", "/v1/ocr", png())[0] == 200
    assert running.request("POST", "/v1/ocr", b"", {"Content-Length": "0"})[0] == 400
    assert running.request("POST", "/v1/ocr", png())[0] == 200


@needs_pil
def test_429_reply_is_readable_by_a_client_still_sending_a_large_body(fake) -> None:
    fake.mode("block")
    both = start(fake)
    thread = threading.Thread(target=lambda: both.request("POST", "/v1/ocr", png()))
    try:
        thread.start()
        fake.wait_started()
        payload = b"x" * 400_000
        raw = b""
        with socket.create_connection(("127.0.0.1", both.port), timeout=10) as sock:
            sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % len(payload))
            sock.sendall(payload)  # ECONNRESET/EPIPE without the bounded drain
            while chunk := sock.recv(65536):
                raw += chunk
        assert raw.startswith(b"HTTP/1.0 429") and b'"busy"' in raw
    finally:
        fake.release()
        thread.join(timeout=30)
        both.close()


def test_413_reply_is_readable_by_a_client_still_sending(fake) -> None:
    small = start(fake, max_bytes=1000)
    try:
        payload = b"x" * 400_000
        raw = b""
        with socket.create_connection(("127.0.0.1", small.port), timeout=10) as sock:
            sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % len(payload))
            sock.sendall(payload)
            while chunk := sock.recv(65536):
                raw += chunk
        assert raw.startswith(b"HTTP/1.0 413") and b"payload_too_large" in raw
    finally:
        small.close()


def test_drain_tolerates_a_client_that_vanishes(fake) -> None:
    small = start(fake, max_bytes=10)
    try:
        sock = socket.create_connection(("127.0.0.1", small.port), timeout=5)
        sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: 5000\r\n\r\npartial")
        sock.close()
        assert small.request("GET", "/health")[0] == 200
    finally:
        small.close()


# ------------------------------------------------------------------ signals


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_main_shuts_down_cleanly_on_sigterm_and_sigint(monkeypatch, capsys, sig) -> None:
    """PID 1 gets no default SIGTERM action: main() must install handlers that stop the server."""

    events: dict[str, object] = {"closed": False}
    stopped = threading.Event()

    class Stub:
        server_address = ("127.0.0.1", 4321)

        def serve_forever(self) -> None:
            stopped.wait(10)

        def shutdown(self) -> None:
            events["shutdown_thread"] = threading.current_thread()
            stopped.set()

        def server_close(self) -> None:
            events["closed"] = True

    monkeypatch.setattr(svc, "make_server", lambda config: Stub())
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    threading.Timer(0.3, lambda: os.kill(os.getpid(), sig)).start()
    assert svc.main() == 0
    assert "listening on" in capsys.readouterr().out
    assert stopped.is_set() and events["closed"] is True
    assert events["shutdown_thread"] is not threading.main_thread()  # not the thread inside serve_forever
    assert {s: signal.getsignal(s) for s in before} == before  # handlers restored


def test_real_server_process_stops_on_sigterm(tmp_path: Path) -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    number = probe.getsockname()[1]
    probe.close()
    env = {**os.environ, "OCR_PORT": str(number), "OCR_BIND": "127.0.0.1", "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([sys.executable, str(SERVICE_PATH)], env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert "listening on" in process.stdout.readline()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()


# ------------------------------------------------------------------ boxes


@needs_pil
def test_unscale_rounds_outward_and_never_collapses_a_box(running, fake) -> None:
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    (fake.dir / "tsv").write_text(
        header
        + "5\t1\t1\t1\t1\t1\t5\t5\t5\t5\t90\tA\n"   # x=5,w=5 at 2x -> floor(2.5)=2 .. ceil(5)=5 -> w=3
        + "5\t1\t1\t1\t2\t1\t5\t7\t1\t1\t90\tB\n"   # x=5,w=1 at 2x -> 2 .. ceil(3)=3 -> w=1, never 0
        + "5\t1\t1\t1\t3\t1\t9\t9\t0\t0\t90\tC\n",  # a genuinely empty box stays empty
        encoding="utf-8",
    )
    body = running.request("POST", "/v1/ocr?scale=2", png(100, 50))[1]
    assert [w["box"] for w in body["words"]] == [[2, 2, 3, 3], [2, 3, 1, 1], [4, 4, 0, 0]]
    assert [line["box"] for line in body["lines"]] == [[2, 2, 3, 3], [2, 3, 1, 1], [4, 4, 0, 0]]


# ------------------------------------------------------------------ image modes


def _png_of(image, **kwargs) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", **kwargs)
    return out.getvalue()


def _engine_input(data: bytes):
    flat, _w, _h = svc.prepare_image(data, svc.Params(), svc.Config())
    return Image.open(io.BytesIO(flat))


@needs_pil
def test_rgba_transparency_is_composited_onto_white() -> None:
    clear = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    clear.putpixel((0, 0), (255, 0, 0, 255))
    result = _engine_input(_png_of(clear))
    assert result.mode == "RGB"
    assert result.getpixel((1, 1)) == (255, 255, 255)  # transparent -> white, not black
    assert result.getpixel((0, 0)) == (255, 0, 0)


@needs_pil
def test_palette_with_transparency_and_la_are_composited_onto_white() -> None:
    palette = Image.new("P", (4, 4), 0)
    palette.putpalette([0, 0, 0] * 256)
    assert _engine_input(_png_of(palette, transparency=0)).convert("RGB").getpixel((2, 2)) == (255, 255, 255)
    assert _engine_input(_png_of(Image.new("LA", (4, 4), (0, 0)))).convert("RGB").getpixel((2, 2)) == (255, 255, 255)


@needs_pil
def test_opaque_palette_and_other_modes_still_convert() -> None:
    assert _engine_input(_png_of(Image.new("P", (4, 4), 0))).mode == "RGB"
    assert _engine_input(_png_of(Image.new("L", (4, 4), 7))).mode == "L"
    assert _engine_input(_png_of(Image.new("1", (4, 4), 1))).mode == "RGB"


@needs_pil
def test_sixteen_bit_gray_is_scaled_to_eight_bits_not_clipped() -> None:
    result = _engine_input(_png_of(Image.new("I;16", (4, 4), 40000)))
    assert result.mode == "L" and 150 <= result.getpixel((1, 1)) <= 160  # 40000 / 256 = 156, not 255


# ------------------------------------------------------------------ scaled pixel cap


@needs_pil
def test_scaled_pixel_count_is_capped_before_resizing(fake) -> None:
    capped = start(fake, max_scaled_pixels=40_000)
    try:
        assert capped.request("POST", "/v1/ocr?scale=2", png(100, 50))[0] == 200  # 20,000 px
        status, body, _ = capped.request("POST", "/v1/ocr?scale=3", png(100, 50))  # 45,000 px
        assert (status, body["code"]) == (413, "scaled_image_too_large") and "40000" in body["error"]
    finally:
        capped.close()
    with pytest.raises(svc.ServiceError) as caught:
        svc.prepare_image(png(4000, 2000), svc.Params(scale=2), svc.Config())  # 32 MP > the 24 MP default
    assert caught.value.code == "scaled_image_too_large"


# ------------------------------------------------------------------ stdlib-generated errors are JSON


def _exchange(port: int, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(data)
        sock.shutdown(socket.SHUT_WR)
        raw = b""
        while chunk := sock.recv(65536):
            raw += chunk
    return raw


def _json_of(raw: bytes) -> dict:
    assert raw.startswith(b"HTTP/1.0 "), raw[:60]
    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"application/json" in head and b"<html" not in body.lower()
    return json.loads(body)


def test_a_bad_request_line_gets_json_not_a_traceback_or_html(running, capfd) -> None:
    raw = _exchange(running.port, b"GARBAGE\r\n\r\n")
    assert raw.split(b" ", 2)[1] == b"400"
    assert _json_of(raw) == {"error": "bad request", "code": "bad_request"}
    assert "Traceback" not in capfd.readouterr().err
    assert running.request("GET", "/health")[0] == 200  # the service lives on


def test_an_overlong_request_line_is_a_json_414(running) -> None:
    raw = _exchange(running.port, b"GET /" + b"a" * 80_000 + b" HTTP/1.0\r\n\r\n")
    assert raw.split(b" ", 2)[1] == b"414"
    assert _json_of(raw)["code"] in ("request_uri_too_long", "uri_too_long")  # the enum was renamed in Python 3.14


def test_too_many_headers_is_a_json_431(running) -> None:
    raw = _exchange(running.port, b"GET /health HTTP/1.0\r\n" + b"X-A: b\r\n" * 150 + b"\r\n")
    assert raw.split(b" ", 2)[1] == b"431"
    assert _json_of(raw)["code"] == "request_header_fields_too_large"


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_unsupported_methods_get_a_json_501(running, method: str) -> None:
    raw = _exchange(running.port, method.encode() + b" /v1/ocr HTTP/1.0\r\n\r\n")
    assert raw.split(b" ", 2)[1] == b"501"
    assert _json_of(raw) == {"error": "not implemented", "code": "not_implemented"}


def test_a_head_request_gets_headers_only(running) -> None:
    raw = _exchange(running.port, b"HEAD /nope HTTP/1.0\r\n\r\n")
    # HEAD is not implemented by this service (501), so the stdlib error path answers without a body
    assert raw.split(b" ", 2)[1] == b"501" and raw.endswith(b"\r\n\r\n")


# ------------------------------------------------------------------ drains for every rejection


@pytest.mark.parametrize("target", ["/nope", "/health", "/v1/ocr?psm=5", "/v1/ocr?engine=glyphgrid"])
def test_any_rejection_with_a_body_on_the_wire_is_readable_by_the_sender(running, target: str) -> None:
    payload = b"x" * 300_000
    raw = b""
    with socket.create_connection(("127.0.0.1", running.port), timeout=10) as sock:
        sock.sendall(b"POST " + target.encode() + b" HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % len(payload))
        sock.sendall(payload)
        while chunk := sock.recv(65536):
            raw += chunk
    assert raw.split(b" ", 2)[1] in (b"404", b"405", b"400", b"501"), raw[:40]
    assert _json_of(raw)["code"]


# ------------------------------------------------------------------ images


@needs_pil
def test_only_the_png_and_jpeg_plugins_parse_untrusted_bytes(running) -> None:
    for fmt in ("GIF", "BMP", "TIFF", "WEBP", "ICO"):
        out = io.BytesIO()
        try:
            Image.new("RGB", (8, 8)).save(out, format=fmt)
        except (OSError, KeyError, ValueError):  # a codec this Pillow build lacks
            continue
        status, resp, _ = running.request("POST", "/v1/ocr", out.getvalue())
        assert (status, resp["code"]) == (415, "unsupported_image"), fmt


@needs_pil
def test_a_multi_picture_jpeg_is_decoded_as_its_first_frame(running) -> None:
    out = io.BytesIO()
    first = Image.new("RGB", (64, 32), "white")
    first.save(out, format="MPO", save_all=True, append_images=[Image.new("RGB", (64, 32), "black")])
    status, resp, _ = running.request("POST", "/v1/ocr", out.getvalue())
    assert status == 200, resp
    assert (resp["width"], resp["height"]) == (64, 32)


# ------------------------------------------------------------------ deadlines and connection cap


def test_trickled_headers_hit_the_total_header_deadline(fake) -> None:
    quick = start(fake, header_deadline_s=0.5, socket_timeout_s=5.0)
    try:
        started = time.monotonic()
        raw = b""
        with socket.create_connection(("127.0.0.1", quick.port), timeout=10) as sock:
            sock.settimeout(0.1)
            sock.sendall(b"GET /health HTTP/1.0\r\n")
            while time.monotonic() - started < 6 and not raw:
                try:
                    sock.sendall(b"X-Slow: 1\r\n")  # a header line every ~0.15 s, never idle for a recv timeout
                except OSError:
                    break
                time.sleep(0.15)
                try:
                    raw = sock.recv(4096)
                except TimeoutError:
                    continue
        assert raw.startswith(b"HTTP/1.0 408") and b"header_timeout" in raw
        assert time.monotonic() - started < 4
        assert quick.request("GET", "/health")[0] == 200
    finally:
        quick.close()


def test_a_prompt_client_is_not_hit_by_the_header_deadline(fake) -> None:
    quick = start(fake, header_deadline_s=0.5)
    try:
        assert quick.request("GET", "/health")[0] == 200
        time.sleep(0.8)  # long past the header deadline: the timer must have been cancelled
        assert quick.request("GET", "/health")[0] == 200
    finally:
        quick.close()


def test_excess_connections_get_a_json_503_and_slots_come_back(fake) -> None:
    capped = start(fake, max_connections=2, socket_timeout_s=10.0, header_deadline_s=30.0)
    idle = []
    try:
        for _ in range(2):  # two connections that say nothing keep both handler slots busy
            idle.append(socket.create_connection(("127.0.0.1", capped.port), timeout=10))
        deadline = time.monotonic() + 5
        while True:  # wait until both are being handled
            status, body, headers = capped.request("GET", "/health")
            if status == 503 or time.monotonic() > deadline:
                break
            time.sleep(0.02)
        assert (status, body["code"]) == (503, "overloaded") and headers.get("Retry-After") == "1"
        for sock in idle:
            sock.close()
        deadline = time.monotonic() + 5
        while capped.request("GET", "/health")[0] != 200:
            assert time.monotonic() < deadline, "handler slots were never released"
            time.sleep(0.02)
    finally:
        for sock in idle:
            sock.close()
        capped.close()


# ------------------------------------------------------------------ engine/service edge paths


def test_body_deadline_already_expired_is_a_408(fake) -> None:
    instant = start(fake, body_deadline_s=0.0)
    try:
        status, body, _ = instant.request("POST", "/v1/ocr", b"x" * 10)
        assert (status, body["code"]) == (408, "request_timeout")
    finally:
        instant.close()


def test_an_oserror_inside_the_engine_drops_the_connection_without_a_reply(fake) -> None:
    class Resetting(svc.UnavailableEngine):
        def available(self) -> bool:
            return True

        def recognize(self, png_bytes, params, timeout_s):
            raise ConnectionResetError("peer went away")

    server = Running(svc.Config(host="127.0.0.1", port=0), {"tesseract": Resetting("tesseract")})
    try:
        if Image is not None:
            with socket.create_connection(("127.0.0.1", server.port), timeout=10) as sock:
                body = png()
                sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
                assert sock.recv(4096) == b""  # closed, nothing written
        # (without Pillow the request is rejected earlier; the branch itself is covered where Pillow exists)
    finally:
        server.close()


def test_version_and_languages_survive_an_engine_that_cannot_start(fake) -> None:
    fake.binary.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    engine = svc.TesseractEngine(str(fake.binary))
    assert engine.available()
    assert engine.version() == "unknown"
    assert engine.languages() == []


def test_languages_are_read_once_and_cached(fake) -> None:
    engine = svc.TesseractEngine(str(fake.binary))
    assert engine.languages() == ["eng", "osd"]
    fake.binary.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    assert engine.languages() == ["eng", "osd"]  # cached: not re-run


def test_module_entrypoint_runs_main_and_stops_on_sigterm(monkeypatch) -> None:
    import runpy

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    number = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("OCR_PORT", str(number))
    monkeypatch.setenv("OCR_BIND", "127.0.0.1")
    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(SERVICE_PATH), run_name="__main__")
    assert caught.value.code == 0


def test_config_header_deadline_and_connection_cap_from_env() -> None:
    config = svc.Config.from_env({"OCR_HEADER_DEADLINE_S": "3", "OCR_MAX_CONNECTIONS": "5"})
    assert (config.header_deadline_s, config.max_connections) == (3.0, 5)
    assert (svc.Config().header_deadline_s, svc.Config().max_connections) == (10.0, 16)


def test_the_drain_gives_up_after_its_byte_budget(fake) -> None:
    small = start(fake, max_bytes=1000, drain_bytes=1000)
    try:
        with socket.create_connection(("127.0.0.1", small.port), timeout=10) as sock:
            sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: 5000\r\n\r\n" + b"x" * 5000)
            raw = b""
            while chunk := sock.recv(65536):  # the client stays connected until the SERVER hangs up
                raw += chunk
        assert raw.startswith(b"HTTP/1.0 413")
    finally:
        small.close()


def test_the_drain_gives_up_after_its_time_budget(fake) -> None:
    small = start(fake, max_bytes=1000, drain_s=0.3)
    try:
        with socket.create_connection(("127.0.0.1", small.port), timeout=10) as sock:
            sock.sendall(b"POST /v1/ocr HTTP/1.0\r\nContent-Length: 5000\r\n\r\nxx")  # the rest never comes
            raw = b""
            while chunk := sock.recv(65536):
                raw += chunk
        assert raw.startswith(b"HTTP/1.0 413")
    finally:
        small.close()
