r"""
test-webspace-passenger-forwarded-headers - Passenger WSGI forwarding-header probe.

PURPOSE
  Answers "what does a Phusion Passenger WSGI application see for forwarding
  headers, and does a front-end proxy preserve, append, overwrite or strip a
  client-supplied value?" It reports exactly what this WSGI request received
  and interprets the X-Forwarded-For chain for a trusted-proxy hop count.

DEPLOY AS A TINY PASSENGER APP DIRECTORY
  1. Set TOKEN below to a long random string, for example from
     `openssl rand -hex 16`. The probe refuses to run unless it is at least
     16 characters.
  2. On the hoster, create a dedicated directory containing only this file as
     `passenger_wsgi.py`. In the domain's Passenger configuration, point the
     application root at that directory and select the Python 3.11 interpreter
     as the hoster documents for naf. Passenger's startup file can remain the
     default `passenger_wsgi.py`. Follow only the hoster's generic
     Passenger/Python-selection steps; no web-root file is needed.
  3. Reload or restart the application as the hoster documents, then request
     the probe's URL below. If needed, create `tmp/restart.txt` in the app
     directory to ask Passenger to restart on the next request.
  4. Run it twice from the same client:
       plain:
         https://DOMAIN/PATH/?token=YOUR-TOKEN
       forged (send the TEST-NET-3 marker so the probe can identify it):
         curl -H 'X-Forwarded-For: 203.0.113.9' \
              -H 'X-Forwarded-Proto: http' \
              'https://DOMAIN/PATH/?token=YOUR-TOKEN'
     Optional parameter:
       &format=json      same data from the same report structure
  5. Compare the plain and forged outputs, compare this Passenger result with
     the PHP probe's result, then DELETE THIS DIRECTORY AGAIN. It reveals
     proxy topology details and the hosting server's view of request
     addresses. Do not leave `passenger_wsgi.py` or the probe directory
     reachable after the test.

WHAT IT REPORTS
  The explicitly requested WSGI fields: REMOTE_ADDR,
  HTTP_X_FORWARDED_FOR, HTTP_X_FORWARDED_PROTO, HTTP_X_FORWARDED_HOST,
  HTTP_X_FORWARDED_PORT, HTTP_X_REAL_IP, HTTP_FORWARDED, HTTP_HOST,
  wsgi.url_scheme, SERVER_PORT, SERVER_SOFTWARE, SERVER_NAME, SCRIPT_NAME,
  PATH_INFO, and sys.version. Present PASSENGER_* keys are listed too.

  It also lists every other HTTP header whose name matches
  forward|real|client|proxy|via|cdn|cf-. Cookie and authorization headers are
  never listed. The access token is replaced with *** if it appears in any
  reflected name or value. C0 control bytes and DEL in reflected names and
  values are shown as \xNN. The whole environ is never dumped.

VERDICT LOGIC
  X-Forwarded-For is split on commas into a chain, in the order WSGI received
  it. Only the deliberate 203.0.113.0/24 (IPv4 TEST-NET-3) marker can drive a
  trusted-hop recommendation. An unmarked multi-entry chain is diagnostic
  only: plain requests can legitimately carry multi-entry chains, so it is
  neither proof of forgery nor a basis for a trusted-hop count.

  WSGI can only observe the post-proxy header, not what was sent. The
  TEST-NET-3 value is the strong check: if it survives, the proxy did not
  remove or overwrite that client-supplied prefix. A chain longer than one
  entry is append evidence. The trusted-hop recommendation is:
    - one TEST-NET-3 entry, or TEST-NET-3 at the chain end: trusted hops: 0
      (the header has no proxy view after the forged value, so forwarded
      client identity must not be trusted);
    - TEST-NET-3 followed by another entry: trusted hops: the number of
      entries after the marker, because each one is append evidence at the
      marker's live position in the chain;
    - no TEST-NET-3 marker, including an unmarked multi-entry chain:
      UNDETERMINED: run again with the forged header.
  A single forged value reaching WSGI with no later entry is indistinguishable
  from a direct request at WSGI's boundary; the safe recommendation is
  therefore 0, not "there is no proxy".

  For X-Forwarded-Proto, HTTPS-on plus a forged value containing "http" means
  the forged value survived. HTTPS-on plus a present HTTPS-only value means it
  was overwritten to https. A missing header cannot be classified from one
  request: it may have been stripped, or simply not added. Compare the plain
  and forged runs.

  PASSENGER-SPECIFIC NOTE
  Passenger or the web server in front of it may already rewrite REMOTE_ADDR
  before the WSGI application runs. The displayed REMOTE_ADDR is therefore
  the post-Passenger value, not necessarily the raw TCP peer. naf currently
  applies `ProxyFix(x_for=1, x_proto=1, x_host=1)`: x_for=1 consumes the
  right-most X-Forwarded-For entry as the nearest trusted proxy view. If
  Passenger rewrites REMOTE_ADDR but preserves or appends an untrusted
  client-supplied X-Forwarded-For value, naf's fixed one-hop setting can still
  misattribute the client. Set the tarball's trusted-proxy hop count only
  after reconciling this probe's marker position, the PHP probe, and the
  app-level evidence below.

APP-LEVEL CHECK (AUTHORITATIVE AFTER THE NAF TARBALL IS DEPLOYED)
  PHP and Passenger may be fronted differently, so this probe is not the final
  authority for naf. After deploying the naf tarball, use the app's own
  GET /api/myip endpoint, which returns "ip" as the app sees it plus the raw
  X-Forwarded-For and X-Real-IP headers. Repeat both the plain and forged
  requests against /api/myip and compare those values. Set naf's
  trusted-proxy hop count only from that app-level evidence; keep it at 0
  until then.

WHAT IT TOUCHES
  Reads WSGI request metadata only. Standard library only, no network calls,
  no files written, no request body read, and no environment dump. Written
  for Python 3.11 and later.
"""

import datetime
import hmac
import re
import sys
import urllib.parse


# REQUIRED: put a long random string here, for example from
# `openssl rand -hex 16`. The probe returns 403 while this is empty or short.
TOKEN = ""


_CONTROL_RE = re.compile(r"[\x00-\x1F\x7F]")
_TEST_NET_3_RE = re.compile(
    r"203\.0\.113\.(?:25[0-5]|2[0-4][0-9]|[0-9]{1,2})(?::[0-9]{1,5})?"
)
_IPV4_WITH_PORT_RE = re.compile(r"((?:[0-9]{1,3}\.){3}[0-9]{1,3}):[0-9]+")
_IPV6_WITH_PORT_RE = re.compile(r"\[([^\]]+)\](?::[0-9]+)?")
_FORWARDING_HEADER_RE = re.compile(
    r"forward|real|client|proxy|via|cdn|cf-",
    re.IGNORECASE,
)
_SECRET_HEADER_RE = re.compile(
    r"authorization|cookie|set-cookie",
    re.IGNORECASE,
)

_REQUIRED_FIELDS = (
    "REMOTE_ADDR",
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_PROTO",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PORT",
    "HTTP_X_REAL_IP",
    "HTTP_FORWARDED",
    "HTTP_HOST",
    "WSGI_URL_SCHEME",
    "SERVER_PORT",
    "SERVER_SOFTWARE",
    "SERVER_NAME",
    "SCRIPT_NAME",
    "PATH_INFO",
    "PYTHON_VERSION",
)

_DISPLAY_NAMES = {
    "WSGI_URL_SCHEME": "wsgi.url_scheme",
    "PYTHON_VERSION": "sys.version",
}


def _escape_controls(value):
    """Render every C0 control byte and DEL as a printable \\xNN sequence."""
    return _CONTROL_RE.sub(lambda match: "\\x%02X" % ord(match.group(0)), value)


def _clean(value):
    """Make a reflected value safe for text and JSON without exposing a token."""
    text = value if isinstance(value, str) else str(value)
    if TOKEN and len(TOKEN) >= 16:
        text = text.replace(TOKEN, "***")
    return _escape_controls(text)


def _query_last(query_string, name):
    """Return the last query parameter named name, matching PHP's convention."""
    pairs = urllib.parse.parse_qsl(query_string, keep_blank_values=True)
    result = None
    found = False
    for key, value in pairs:
        if key == name:
            result = value
            found = True
    return result if found else None


def _token_is_valid(environ):
    supplied = _query_last(environ.get("QUERY_STRING", ""), "token")
    if not isinstance(TOKEN, str) or len(TOKEN) < 16:
        return False
    if not isinstance(supplied, str):
        return False
    try:
        return hmac.compare_digest(TOKEN, supplied)
    except (TypeError, ValueError):
        return False


def _header_name(wsgi_key):
    """Convert a WSGI HTTP_* key to its display header name."""
    header_part = wsgi_key[5:].replace("_", "-")
    parts = header_part.lower().split("-")
    upper_parts = {"x", "cf", "cdn", "ip", "http", "https"}
    return "-".join(
        part.upper() if part in upper_parts else part.capitalize()
        for part in parts
    )


def _display_name(key):
    if key == "PYTHON_VERSION":
        return _DISPLAY_NAMES[key]
    if key.startswith("HTTP_"):
        return _header_name(key)
    return _DISPLAY_NAMES.get(key, key)


def _secret_header(wsgi_key):
    header_name = _header_name(wsgi_key)
    return _SECRET_HEADER_RE.search(header_name) is not None


def _address_key(value):
    value = str(value).strip()
    match = _IPV6_WITH_PORT_RE.fullmatch(value)
    if match:
        return match.group(1).lower()
    match = _IPV4_WITH_PORT_RE.fullmatch(value)
    if match:
        return match.group(1)
    return value.lower()


def _is_test_net_3(value):
    return _TEST_NET_3_RE.fullmatch(str(value).strip()) is not None


def _as_text(environ, key):
    value = environ[key]
    return value if isinstance(value, str) else str(value)


def _build_server_rows(environ):
    rows = []
    for key in _REQUIRED_FIELDS:
        if key == "PYTHON_VERSION":
            present = True
            value = sys.version
        else:
            present = key in environ
            value = _as_text(environ, key) if present else None
        rows.append(
            {
                "name": _clean(_display_name(key)),
                "key": _clean(key),
                "kind": "requested",
                "present": present,
                "value": _clean(value) if present else None,
            }
        )

    passenger_keys = sorted(
        key
        for key in environ
        if isinstance(key, str) and key.startswith("PASSENGER_")
    )
    for key in passenger_keys:
        rows.append(
            {
                "name": _clean(key),
                "key": _clean(key),
                "kind": "passenger",
                "present": True,
                "value": _clean(_as_text(environ, key)),
            }
        )

    extra_headers = []
    for key in environ:
        if (
            not isinstance(key, str)
            or not key.startswith("HTTP_")
            or key in _REQUIRED_FIELDS
            or _secret_header(key)
        ):
            continue
        header_name = _header_name(key)
        if _FORWARDING_HEADER_RE.search(header_name) is None:
            continue
        extra_headers.append(
            {
                "name": _clean(header_name),
                "key": _clean(key),
                "kind": "matched-header",
                "present": True,
                "value": _clean(_as_text(environ, key)),
            }
        )
    extra_headers.sort(key=lambda row: row["name"])
    rows.extend(extra_headers)
    return rows


def _build_report(environ):
    generated = datetime.datetime.now(datetime.timezone.utc).isoformat()
    server_rows = _build_server_rows(environ)

    remote_addr = (
        _as_text(environ, "REMOTE_ADDR") if "REMOTE_ADDR" in environ else ""
    )
    xff_present = "HTTP_X_FORWARDED_FOR" in environ
    xff_raw = _as_text(environ, "HTTP_X_FORWARDED_FOR") if xff_present else ""
    xff_entries = []
    if xff_present and xff_raw.strip() != "":
        xff_entries = [
            entry
            for entry in re.split(r"\s*,\s*", xff_raw.strip())
            if entry != ""
        ]

    chain_length = len(xff_entries)
    chain_last = xff_entries[-1] if chain_length else ""
    chain_last_key = _address_key(chain_last)
    test_net_3_present = False
    test_net_3_index = None
    value_differs_from_chain_end = False
    for index, entry in enumerate(xff_entries):
        if test_net_3_index is None and _is_test_net_3(entry):
            test_net_3_present = True
            test_net_3_index = index
        if (
            index < chain_length - 1
            and _address_key(entry) != chain_last_key
        ):
            value_differs_from_chain_end = True

    test_net_3_at_chain_end = (
        test_net_3_present and _is_test_net_3(chain_last)
    )
    entries_after_test_net_3 = (
        0 if test_net_3_index is None else chain_length - test_net_3_index - 1
    )
    proxy_append_evidence = chain_length > 1 and chain_last != ""
    last_matches_remote_addr = (
        chain_last != ""
        and remote_addr != ""
        and chain_last_key == _address_key(remote_addr)
    )
    last_differs_from_remote_addr = (
        chain_last != ""
        and remote_addr != ""
        and chain_last_key != _address_key(remote_addr)
    )

    xfp_present = "HTTP_X_FORWARDED_PROTO" in environ
    xfp_raw = _as_text(environ, "HTTP_X_FORWARDED_PROTO") if xfp_present else ""
    xfp_protocols = []
    if xfp_present and xfp_raw.strip() != "":
        xfp_protocols = [
            protocol
            for protocol in re.split(
                r"\s*,\s*", xfp_raw.strip().lower()
            )
            if protocol != ""
        ]

    scheme = (
        _as_text(environ, "WSGI_URL_SCHEME").lower()
        if "WSGI_URL_SCHEME" in environ
        else ""
    )
    server_port = (
        _as_text(environ, "SERVER_PORT") if "SERVER_PORT" in environ else ""
    )
    https_by_scheme = scheme == "https"
    https_by_port = server_port == "443"
    https_observed = https_by_scheme or https_by_port
    xfp_contains_http = "http" in xfp_protocols
    xfp_contains_https = "https" in xfp_protocols
    forged_http_preserved = https_observed and xfp_contains_http
    xfp_overwritten_to_https = (
        https_observed
        and xfp_present
        and xfp_contains_https
        and not xfp_contains_http
    )

    trusted_hops = None
    recommendation = "UNDETERMINED: run again with the forged header."
    reason = (
        "No deliberate TEST-NET-3 marker was observed; an unmarked chain is "
        "diagnostic only."
    )
    if not xff_present or chain_length == 0:
        reason = (
            "X-Forwarded-For is absent or empty and has no TEST-NET-3 marker; "
            "one request cannot distinguish a missing proxy from a proxy that "
            "strips the header."
        )
    elif not test_net_3_present:
        reason = (
            "X-Forwarded-For has no deliberate TEST-NET-3 marker; "
            + str(chain_length)
            + " chain entr"
            + ("y is" if chain_length == 1 else "ies are")
            + " diagnostic only."
        )
    elif test_net_3_at_chain_end:
        trusted_hops = 0
        recommendation = "trusted hops: 0"
        reason = (
            "TEST-NET-3 is the final or only X-Forwarded-For entry; no later "
            "proxy view was observed, so do not trust the forwarded client "
            "identity."
        )
    elif entries_after_test_net_3 > 0:
        trusted_hops = entries_after_test_net_3
        recommendation = "trusted hops: " + str(trusted_hops)
        reason = (
            "TEST-NET-3 is at position "
            + str(test_net_3_index + 1)
            + " of "
            + str(chain_length)
            + "; the "
            + str(entries_after_test_net_3)
            + " later entr"
            + ("y provides" if entries_after_test_net_3 == 1 else "ies provide")
            + " append evidence."
        )
    else:
        reason = (
            "The TEST-NET-3 marker has no later entry to establish append "
            "evidence."
        )

    if forged_http_preserved:
        reason += " X-Forwarded-Proto still says http while WSGI reports HTTPS."
    elif xfp_overwritten_to_https:
        reason += " X-Forwarded-Proto was overwritten or normalised to https."

    passenger_note = (
        "Passenger or the web server may already rewrite REMOTE_ADDR before "
        "WSGI. naf's ProxyFix(x_for=1) consumes the right-most "
        "X-Forwarded-For entry; reconcile that boundary with the marker "
        "position, the PHP probe, and GET /api/myip before setting the "
        "tarball's trusted-proxy hop count."
    )

    return {
        "generated": _clean(generated),
        "file": "passenger_wsgi.py",
        "python_version": _clean(sys.version),
        "server": server_rows,
        "xff": {
            "present": xff_present,
            "value": _clean(xff_raw) if xff_present else None,
            "entries": [_clean(entry) for entry in xff_entries],
            "chain_length": chain_length,
            "test_net_3_present": test_net_3_present,
            "test_net_3_position": (
                None if test_net_3_index is None else test_net_3_index + 1
            ),
            "entries_after_test_net_3": entries_after_test_net_3,
            "test_net_3_at_chain_end": test_net_3_at_chain_end,
            "value_differs_from_chain_end_diagnostic": (
                value_differs_from_chain_end
            ),
            "proxy_append_evidence": proxy_append_evidence,
            "last_entry_matches_remote_addr": last_matches_remote_addr,
            "last_entry_differs_from_remote_addr": (
                last_differs_from_remote_addr
            ),
        },
        "x_forwarded_proto": {
            "https_observed_by_wsgi": https_observed,
            "https_tests": {
                "wsgi.url_scheme": https_by_scheme,
                "SERVER_PORT_443": https_by_port,
            },
            "present": xfp_present,
            "value": _clean(xfp_raw) if xfp_present else None,
            "forged_http_preserved": forged_http_preserved,
            "overwritten_to_https": xfp_overwritten_to_https,
        },
        "verdict": {
            "trusted_hops": trusted_hops,
            "recommendation": recommendation,
            "reasoning": reason,
            "passenger_remote_addr_note": passenger_note,
            "authoritative_check": (
                "Repeat after deploying naf: GET /api/myip plain and forged; "
                "its ip and raw forwarding-header values decide."
            ),
        },
        "reminder": "Delete this Passenger app directory from the hoster.",
    }


def _render_text(report):
    lines = [
        "test-webspace-passenger-forwarded-headers " + report["generated"],
        "",
        "== WSGI VIEW OF FORWARDING FIELDS ==",
    ]
    for row in report["server"]:
        if row["present"]:
            value = row["value"]
            if value == "":
                value = "(empty string)"
        else:
            value = "(unset)"
        label = row["name"]
        if row["kind"] == "matched-header":
            label += " [" + row["key"] + "]"
        lines.append(label.ljust(42) + " " + value)

    xff = report["xff"]
    proto = report["x_forwarded_proto"]
    verdict = report["verdict"]
    lines.extend(
        [
            "",
            "== VERDICT ==",
            "X-Forwarded-For present: "
            + ("yes" if xff["present"] else "no")
            + "  chain length: "
            + str(xff["chain_length"]),
            "TEST-NET-3 value reaches WSGI: "
            + ("yes" if xff["test_net_3_present"] else "no"),
            "TEST-NET-3 position / entries after: "
            + (
                "(none)"
                if xff["test_net_3_position"] is None
                else str(xff["test_net_3_position"])
            )
            + " / "
            + str(xff["entries_after_test_net_3"]),
            "Value differs from chain end (diagnostic only): "
            + (
                "yes"
                if xff["value_differs_from_chain_end_diagnostic"]
                else "no"
            ),
            "Proxy append evidence: "
            + ("yes" if xff["proxy_append_evidence"] else "no")
            + "  last matches REMOTE_ADDR: "
            + ("yes" if xff["last_entry_matches_remote_addr"] else "no")
            + "  last differs: "
            + ("yes" if xff["last_entry_differs_from_remote_addr"] else "no"),
            "WSGI reports HTTPS: "
            + ("yes" if proto["https_observed_by_wsgi"] else "no"),
            "  forged X-Forwarded-Proto=http preserved: "
            + ("yes" if proto["forged_http_preserved"] else "no"),
            "  overwritten to https: "
            + ("yes" if proto["overwritten_to_https"] else "no"),
            "recommendation: " + verdict["recommendation"],
            "reasoning: " + verdict["reasoning"],
            "",
            "Passenger REMOTE_ADDR note: "
            + verdict["passenger_remote_addr_note"],
            "",
            "Authoritative check after naf is deployed: "
            + verdict["authoritative_check"],
            "",
            "**DELETE THIS PASSENGER APP DIRECTORY FROM THE HOSTER NOW. "
            "It reveals proxy topology details.**",
            "",
        ]
    )
    return "\n".join(lines)


def application(environ, start_response):
    if not _token_is_valid(environ):
        start_response(
            "403 Forbidden",
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Cache-Control", "no-store"),
                ("X-Robots-Tag", "noindex"),
            ],
        )
        return [
            (
                "Forbidden. Set TOKEN near the top of passenger_wsgi.py "
                "(at least 16 characters) and call ?token=...\n"
            ).encode("utf-8")
        ]

    report = _build_report(environ)
    format_value = _query_last(environ.get("QUERY_STRING", ""), "format")
    if format_value == "json":
        import json

        body = (
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
            )
            + "\n"
        )
        content_type = "application/json; charset=utf-8"
    else:
        body = _render_text(report)
        content_type = "text/plain; charset=utf-8"

    start_response(
        "200 OK",
        [
            ("Content-Type", content_type),
            ("Cache-Control", "no-store"),
            ("X-Robots-Tag", "noindex"),
        ],
    )
    return [body.encode("utf-8")]
