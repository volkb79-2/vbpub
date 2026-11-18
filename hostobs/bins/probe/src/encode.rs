//! The server's encoders (design R1, R14, R25; WIRE.md sections 1.4, 2.1, 4.1, 5.4, 8.2).
//!
//! Every function here is pure and returns bytes. Strings that come from the server are built
//! with [`push_string`], which never emits a raw control character; text that came from a
//! client is never passed to it (a [`Detail`] holds only schema names, design R14).
//!
//! - [`control_frame`] writes the 12-byte frame header of WIRE.md section 2.1 (flags 0, reserved
//!   0) in front of a JSON payload; the section codec of `proto` is for BLOCK frames only.
//! - [`rep_ok`] and [`rep_ok_line`] replace a reply longer than `max_reply_bytes` by an
//!   `internal` error (R25), which bounds a misbehaving backend.
//! - the error envelope is `{"ok":false,"error":{"code","message","detail"}}` with `detail`
//!   always present; the JSON line variant adds `"corr"` and ends in `\n`.

use hostobs_proto::frame::{FrameType, FRAME_HEADER_LEN, MAGIC};

use crate::codes::{Code, Detail};

/// The length of the binary prefix `HOB\x01` + `u16 major` + `u16 minor`.
pub const PREFIX_LEN: usize = 8;

/// The server's 8-byte prefix with its own `major` and the negotiated `minor` (WIRE.md 1.4).
#[must_use]
pub fn server_prefix(major: u16, minor: u16) -> [u8; PREFIX_LEN] {
    let [m0, m1, m2, m3] = MAGIC;
    let [a0, a1] = major.to_le_bytes();
    let [b0, b1] = minor.to_le_bytes();
    [m0, m1, m2, m3, a0, a1, b0, b1]
}

/// One frame of `frame_type` with `corr` and the JSON `payload`: the 12-byte header (`len`,
/// type, flags 0, reserved 0, `corr`, little endian) followed by the payload. The payload of
/// every caller is bounded by `max_reply_bytes`, far below the 4 GiB a `u32` length holds.
#[must_use]
pub fn control_frame(frame_type: FrameType, corr: u32, payload: &[u8]) -> Vec<u8> {
    let total = FRAME_HEADER_LEN.saturating_add(payload.len());
    let len = u32::try_from(total).unwrap_or(u32::MAX);
    let mut out = Vec::with_capacity(total);
    out.extend_from_slice(&len.to_le_bytes());
    out.push(frame_type.code());
    out.push(0);
    out.extend_from_slice(&[0, 0]);
    out.extend_from_slice(&corr.to_le_bytes());
    out.extend_from_slice(payload);
    out
}

/// Append `text` as a JSON string: quotes, backslash and every control character below 0x20 or
/// equal to 0x7F are escaped (the control form is a four-digit hex escape), everything else,
/// including non-ASCII text, stays as UTF-8.
pub fn push_string(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        let code = u32::from(c);
        if c == '"' || c == '\\' {
            out.push('\\');
            out.push(c);
        } else if code < 0x20 || code == 0x7F {
            out.push('\\');
            out.push('u');
            out.push_str(&format!("{code:04x}"));
        } else {
            out.push(c);
        }
    }
    out.push('"');
}

/// `text` as a JSON string.
#[must_use]
pub fn json_string(text: &str) -> String {
    let mut out = String::with_capacity(text.len().saturating_add(2));
    push_string(&mut out, text);
    out
}

fn push_detail(out: &mut String, detail: &Detail) {
    match detail {
        Detail::Empty => out.push_str("{}"),
        Detail::Key(path) => {
            out.push_str("{\"key\":");
            push_string(out, path);
            out.push('}');
        }
        Detail::Pool(pool) => {
            out.push_str("{\"pool\":");
            push_string(out, pool.name());
            out.push('}');
        }
    }
}

/// The `"error":{...}` member of the error envelope.
fn error_member(code: Code, detail: &Detail) -> String {
    let mut out = String::from("\"error\":{\"code\":");
    push_string(&mut out, code.name());
    out.push_str(",\"message\":");
    push_string(&mut out, code.message());
    out.push_str(",\"detail\":");
    push_detail(&mut out, detail);
    out.push('}');
    out
}

/// The binary-mode error envelope payload (WIRE.md 4.1).
#[must_use]
pub fn error_payload(code: Code, detail: &Detail) -> String {
    format!("{{\"ok\":false,{}}}", error_member(code, detail))
}

/// A binary REP frame carrying the error envelope.
#[must_use]
pub fn rep_error(corr: u32, code: Code, detail: &Detail) -> Vec<u8> {
    let payload = error_payload(code, detail);
    control_frame(FrameType::Rep, corr, payload.as_bytes())
}

/// The JSON-mode error line: the envelope plus `"corr"`, then `\n` (WIRE.md 8.2).
#[must_use]
pub fn error_line(corr: u32, code: Code, detail: &Detail) -> Vec<u8> {
    let member = error_member(code, detail);
    let mut line = format!("{{\"ok\":false,\"corr\":{corr},{member}}}");
    line.push('\n');
    line.into_bytes()
}

/// The success payload `{"ok":true,"result":<result_json>}`.
#[must_use]
pub fn ok_payload(result_json: &str) -> String {
    format!("{{\"ok\":true,\"result\":{result_json}}}")
}

/// Whether `len` is above `max_reply_bytes`.
fn over(len: usize, max_reply_bytes: u32) -> bool {
    len > usize::try_from(max_reply_bytes).unwrap_or(usize::MAX)
}

/// A binary REP frame for `result_json` (one JSON object). A payload above `max_reply_bytes` is
/// replaced by an `internal` error on the same `corr` (R25). The cap is measured on the payload
/// alone (`{"ok":true,"result":...}`): the 12-byte header and the `corr` are not counted.
#[must_use]
pub fn rep_ok(corr: u32, result_json: &str, max_reply_bytes: u32) -> Vec<u8> {
    let payload = ok_payload(result_json);
    if over(payload.len(), max_reply_bytes) {
        return rep_error(corr, Code::Internal, &Detail::Empty);
    }
    control_frame(FrameType::Rep, corr, payload.as_bytes())
}

/// The JSON-mode success line for `result_json`, `"corr"` included, ending in `\n`. A reply
/// above `max_reply_bytes` is replaced by an `internal` error line. The cap is measured on the
/// whole line without its `\n`, so the `,"corr":N` member counts (unlike [`rep_ok`], whose cap
/// measures the payload alone).
#[must_use]
pub fn rep_ok_line(corr: u32, result_json: &str, max_reply_bytes: u32) -> Vec<u8> {
    let mut line = format!("{{\"ok\":true,\"corr\":{corr},\"result\":{result_json}}}");
    if over(line.len(), max_reply_bytes) {
        return error_line(corr, Code::Internal, &Detail::Empty);
    }
    line.push('\n');
    line.into_bytes()
}

/// The `closing` EVT frame (`corr` 0) naming the fatal `code` (WIRE.md 2.2, 5.4).
#[must_use]
pub fn closing_evt(code: Code) -> Vec<u8> {
    let mut payload = String::from("{\"event\":\"closing\",\"reason\":");
    push_string(&mut payload, code.name());
    payload.push('}');
    control_frame(FrameType::Evt, 0, payload.as_bytes())
}

/// Why a successful stream ended (WIRE.md sections 4.6, 4.7 and 5.4).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EndReason {
    /// The requested block sequence was completed.
    Complete,
    /// The client unsubscribed.
    Unsubscribed,
    /// The client half-closed after all outstanding work.
    ClientEof,
}

impl EndReason {
    /// The wire spelling of the reason.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            EndReason::Complete => "complete",
            EndReason::Unsubscribed => "unsubscribed",
            EndReason::ClientEof => "client-eof",
        }
    }
}

/// A successful END frame (design S29): `next` is `null` for these P5b-2 reasons.
#[must_use]
pub fn end_ok(corr: u32, blocks: u32, dropped: u32, reason: EndReason) -> Vec<u8> {
    let mut payload = String::from("{\"ok\":true,\"blocks\":");
    payload.push_str(&blocks.to_string());
    payload.push_str(",\"dropped\":");
    payload.push_str(&dropped.to_string());
    payload.push_str(",\"next\":null,\"reason\":");
    push_string(&mut payload, reason.name());
    payload.push('}');
    control_frame(FrameType::End, corr, payload.as_bytes())
}

/// A failed stream END frame with the existing error member (design S29).
#[must_use]
pub fn end_error(
    corr: u32,
    blocks: u32,
    dropped: u32,
    code: Code,
    detail: &Detail,
) -> Vec<u8> {
    let member = error_member(code, detail);
    let payload = format!(
        "{{\"ok\":false,\"blocks\":{blocks},\"dropped\":{dropped},{member}}}"
    );
    control_frame(FrameType::End, corr, payload.as_bytes())
}

/// The `current` success result `{"stream":{"tick_ms":T}}`.
#[must_use]
pub fn current_result(tick_ms: u32) -> String {
    format!("{{\"stream\":{{\"tick_ms\":{tick_ms}}}}}")
}

/// The `subscribe` success result `{"sub":C,"interval_ms":V}`.
#[must_use]
pub fn subscribe_result(sub: u32, interval_ms: u32) -> String {
    format!("{{\"sub\":{sub},\"interval_ms\":{interval_ms}}}")
}

/// The `unsubscribe` success result.
pub const UNSUBSCRIBE_RESULT: &str = "{}";

/// The line written for a first byte that selects no mode (WIRE.md 1.3): the error line with
/// `corr` 0 and `"detail":{}`, which the client requires.
#[must_use]
pub fn unknown_mode_line() -> Vec<u8> {
    error_line(0, Code::UnknownMode, &Detail::Empty)
}

#[cfg(test)]
mod tests {
    use super::{
        closing_evt, control_frame, current_result, end_error, end_ok, error_line, error_payload,
        json_string, ok_payload, rep_error, rep_ok, rep_ok_line, server_prefix, subscribe_result,
        unknown_mode_line, EndReason, PREFIX_LEN, UNSUBSCRIBE_RESULT,
    };
    use crate::args::unescape;
    use crate::codes::{Code, Detail};
    use hostobs_proto::envelope::{check_end, check_evt, check_rep, members, members_up_to, Kind};
    use hostobs_proto::frame::{detect_mode, FrameHeader, FrameType, Mode};

    fn text_of(bytes: &[u8], range: &core::ops::Range<usize>) -> String {
        let slice = bytes.get(range.clone()).unwrap_or_default();
        String::from_utf8_lossy(slice).into_owned()
    }

    /// The header and payload of one frame, the header checked against the codec's parser.
    fn split(frame: &[u8]) -> (FrameHeader, &[u8]) {
        let header = FrameHeader::parse(frame).expect("header parses");
        assert_eq!(usize::try_from(header.len).expect("len"), frame.len());
        assert_eq!(header.flags, 0);
        (header, frame.get(12..).expect("payload"))
    }

    /// The `(code, message, detail)` texts of a checked error REP payload.
    fn error_parts(payload: &[u8], json: bool) -> (String, String, String) {
        let rep = check_rep(payload, json).expect("a valid REP");
        assert!(!rep.ok);
        let body = payload.get(rep.body.clone()).expect("error object");
        let found = members(body).expect("error members");
        assert_eq!(found.len(), 3);
        let pick = |name: &str| {
            let member = found
                .iter()
                .find(|m| body.get(m.key.clone()) == Some(name.as_bytes()))
                .expect("member present");
            text_of(body, &member.value)
        };
        (pick("code"), pick("message"), pick("detail"))
    }

    #[test]
    fn the_prefix_is_magic_then_major_and_minor_little_endian() {
        let prefix = server_prefix(1, 1);
        assert_eq!(prefix.len(), PREFIX_LEN);
        assert_eq!(prefix, [0x48, 0x4F, 0x42, 0x01, 1, 0, 1, 0]);
        let wide = server_prefix(0x0201, 0x0403);
        assert_eq!(wide.get(4..), Some([1u8, 2, 3, 4].as_slice()));
        let detected = detect_mode(&prefix);
        assert_eq!(detected, Ok(Mode::Binary { major: 1, minor: 1 }));
        assert_eq!(hostobs_proto::frame::hello(), prefix.to_vec());
    }

    #[test]
    fn a_control_frame_is_a_twelve_byte_header_and_the_payload() {
        let frame = control_frame(FrameType::Rep, 0x0102_0304, b"{}");
        assert_eq!(frame.get(..4), Some([14u8, 0, 0, 0].as_slice()));
        assert_eq!(frame.get(4..8), Some([2u8, 0, 0, 0].as_slice()));
        assert_eq!(frame.get(8..12), Some([4u8, 3, 2, 1].as_slice()));
        assert_eq!(frame.get(12..), Some(b"{}".as_slice()));
        let (header, payload) = split(&frame);
        assert_eq!(header.frame_type, FrameType::Rep);
        assert_eq!(header.corr, 0x0102_0304);
        assert_eq!(payload, b"{}");
        let empty = control_frame(FrameType::Evt, 0, b"");
        assert_eq!(empty.len(), 12);
        assert_eq!(split(&empty).0.frame_type, FrameType::Evt);
    }

    #[test]
    fn strings_escape_quote_backslash_and_controls_and_keep_utf8() {
        assert_eq!(json_string("a\"b"), "\"a\\\"b\"");
        assert_eq!(json_string("a\\b"), "\"a\\\\b\"");
        assert_eq!(json_string("\u{e9}\u{1F600}"), "\"\u{e9}\u{1F600}\"");
        let bs = char::from(0x5c);
        let nul = format!("\"{bs}u0000\"");
        assert_eq!(json_string("\u{0}"), nul);
        let del = format!("\"{bs}u007f\"");
        assert_eq!(json_string("\u{7f}"), del);
        assert_eq!(json_string(""), "\"\"");
    }

    #[test]
    fn every_scalar_below_0x80_round_trips_and_no_raw_control_is_emitted() {
        let mut all = String::new();
        for code in 0u8..=0x7F {
            all.push(char::from(code));
        }
        all.push_str("\u{e9}\u{20AC}\u{1F600}");
        let encoded = json_string(&all);
        assert!(encoded.bytes().all(|b| b >= 0x20 && b != 0x7F));
        assert_eq!(unescape(encoded.as_bytes(), 1000), Some(all));
        for text in ["", "\"", "\\", "\\\"\\", "a\nb", "\t\r"] {
            let encoded = json_string(text);
            assert_eq!(unescape(encoded.as_bytes(), 100).as_deref(), Some(text));
        }
    }

    #[test]
    fn the_error_envelope_always_carries_detail_and_round_trips() {
        for code in Code::ALL {
            let payload = error_payload(code, &Detail::Empty);
            let (name, message, detail) = error_parts(payload.as_bytes(), false);
            assert_eq!(name, json_string(code.name()));
            assert_eq!(message, json_string(code.message()));
            assert_eq!(detail, "{}");
            let frame = rep_error(7, code, &Detail::Empty);
            let (header, body) = split(&frame);
            assert_eq!((header.frame_type, header.corr), (FrameType::Rep, 7));
            assert_eq!(body, payload.as_bytes());
        }
    }

    #[test]
    fn a_key_detail_is_an_object_with_the_escaped_path() {
        let payload = error_payload(Code::BadRequest, &Detail::key("proto.minor"));
        let (_, _, detail) = error_parts(payload.as_bytes(), false);
        assert_eq!(detail, "{\"key\":\"proto.minor\"}");
        let empty = error_payload(Code::BadRequest, &Detail::key(""));
        assert_eq!(error_parts(empty.as_bytes(), false).2, "{\"key\":\"\"}");
        let odd = Detail::Key("a\"b\\c\u{1}".to_string());
        let payload = error_payload(Code::BadRequest, &odd);
        assert!(check_rep(payload.as_bytes(), false).is_ok());
    }

    #[test]
    fn the_json_line_adds_corr_and_ends_in_one_newline() {
        for corr in [0u32, 1, 4_294_967_295] {
            let line = error_line(corr, Code::BadRequest, &Detail::key("verb"));
            assert_eq!(line.last(), Some(&b'\n'));
            let body = line.get(..line.len() - 1).expect("body");
            assert!(!body.contains(&b'\n'));
            let rep = check_rep(body, true).expect("a valid JSON REP");
            assert_eq!((rep.ok, rep.corr), (false, Some(corr)));
            assert_eq!(error_parts(body, true).0, "\"bad-request\"");
        }
        let ok = rep_ok_line(9, "{\"a\":1}", 1000);
        let body = ok.get(..ok.len() - 1).expect("body");
        let rep = check_rep(body, true).expect("valid");
        assert_eq!((rep.ok, rep.corr), (true, Some(9)));
        assert_eq!(text_of(body, &rep.body), "{\"a\":1}");
    }

    #[test]
    fn the_unknown_mode_line_has_corr_zero_and_an_empty_detail() {
        let line = unknown_mode_line();
        let text = String::from_utf8(line.clone()).expect("utf-8");
        let head = "{\"ok\":false,\"corr\":0,\"error\":{\"code\":\"unknown-mode\"";
        assert!(text.starts_with(head));
        assert!(text.ends_with("\"detail\":{}}}\n"));
        let body = line.get(..line.len() - 1).expect("body");
        let rep = check_rep(body, true).expect("the client's JSON check passes");
        assert_eq!(rep.corr, Some(0));
        assert_eq!(error_parts(body, true).2, "{}");
    }

    #[test]
    fn a_reply_over_max_reply_bytes_becomes_an_internal_error() {
        let result = format!("{{\"pad\":\"{}\"}}", "x".repeat(100));
        let payload_len = ok_payload(&result).len();
        let max = u32::try_from(payload_len).expect("small");
        let fits = rep_ok(3, &result, max);
        let (header, body) = split(&fits);
        assert_eq!((header.frame_type, header.corr), (FrameType::Rep, 3));
        assert!(check_rep(body, false).expect("ok").ok);
        let over = rep_ok(3, &result, max - 1);
        let (header, body) = split(&over);
        assert_eq!(header.corr, 3);
        assert_eq!(error_parts(body, false).0, "\"internal\"");
        assert!(over.len() < fits.len());
        let zero = rep_ok(3, "{}", 0);
        assert_eq!(error_parts(split(&zero).1, false).0, "\"internal\"");
        let line_fits = rep_ok_line(3, &result, max + 20);
        let line_over = rep_ok_line(3, &result, 10);
        let ok_body = line_fits.get(..line_fits.len() - 1).expect("body");
        assert!(check_rep(ok_body, true).expect("ok").ok);
        let err_body = line_over.get(..line_over.len() - 1).expect("body");
        assert_eq!(error_parts(err_body, true).0, "\"internal\"");
        assert_eq!(check_rep(err_body, true).expect("rep").corr, Some(3));
    }

    #[test]
    fn the_reply_cap_is_exact_for_the_line_and_for_the_payload() {
        let result = format!("{{\"pad\":\"{}\"}}", "x".repeat(40));
        let full = rep_ok_line(3, &result, u32::MAX);
        assert_eq!(full.last(), Some(&b'\n'));
        let cap = u32::try_from(full.len() - 1).expect("small");
        // The line cap is the line without its newline, `,"corr":N` included.
        assert_eq!(rep_ok_line(3, &result, cap), full);
        let over = rep_ok_line(3, &result, cap - 1);
        let body = over.get(..over.len() - 1).expect("body");
        assert_eq!(error_parts(body, true).0, "\"internal\"");
        assert_eq!(check_rep(body, true).expect("rep").corr, Some(3));
        // Nine more corr digits make the same result nine bytes longer.
        let wide = rep_ok_line(4_294_967_295, &result, cap);
        let body = wide.get(..wide.len() - 1).expect("body");
        assert_eq!(error_parts(body, true).0, "\"internal\"");
        let wide = rep_ok_line(4_294_967_295, &result, cap + 9);
        let body = wide.get(..wide.len() - 1).expect("body");
        assert!(check_rep(body, true).expect("rep").ok);
        // The binary cap is the payload alone: the corr is in the header, not counted.
        let payload_cap = u32::try_from(ok_payload(&result).len()).expect("small");
        let frame = rep_ok(4_294_967_295, &result, payload_cap);
        assert!(check_rep(split(&frame).1, false).expect("rep").ok);
        let frame = rep_ok(4_294_967_295, &result, payload_cap - 1);
        assert_eq!(error_parts(split(&frame).1, false).0, "\"internal\"");
    }

    #[test]
    fn rep_error_golden_bytes_for_a_key_detail() {
        let frame = rep_error(7, Code::BadRequest, &Detail::key("proto.minor"));
        let mut expected = vec![123u8, 0, 0, 0, 2, 0, 0, 0, 7, 0, 0, 0];
        let payload = concat!(
            r#"{"ok":false,"error":{"code":"bad-request","#,
            r#""message":"the request is malformed","#,
            r#""detail":{"key":"proto.minor"}}}"#
        );
        expected.extend_from_slice(payload.as_bytes());
        assert_eq!(expected.len(), 123);
        assert_eq!(frame, expected);
    }

    #[test]
    fn error_line_golden_bytes_with_a_spaced_message() {
        let line = error_line(3, Code::Idle, &Detail::Empty);
        let expected = concat!(
            r#"{"ok":false,"corr":3,"error":{"code":"idle","#,
            r#""message":"the connection was idle for too long","#,
            r#""detail":{}}}"#,
            "\n"
        );
        assert_eq!(line, expected.as_bytes());
        let line = error_line(7, Code::BadRequest, &Detail::key("proto.minor"));
        let expected = concat!(
            r#"{"ok":false,"corr":7,"error":{"code":"bad-request","#,
            r#""message":"the request is malformed","#,
            r#""detail":{"key":"proto.minor"}}}"#,
            "\n"
        );
        assert_eq!(line, expected.as_bytes());
    }

    #[test]
    fn closing_evt_golden_bytes() {
        let frame = closing_evt(Code::Deadline);
        let mut expected = vec![51u8, 0, 0, 0, 3, 0, 0, 0, 0, 0, 0, 0];
        expected.extend_from_slice(br#"{"event":"closing","reason":"deadline"}"#);
        assert_eq!(expected.len(), 51);
        assert_eq!(frame, expected);
    }

    #[test]
    fn the_closing_evt_names_every_fatal_code_and_passes_the_client_check() {
        for code in Code::ALL.iter().filter(|code| code.fatal()) {
            let frame = closing_evt(*code);
            let (header, payload) = split(&frame);
            assert_eq!((header.frame_type, header.corr), (FrameType::Evt, 0));
            let evt = check_evt(payload).expect("closing EVT");
            assert_eq!(payload.get(evt.event), Some(b"closing".as_slice()));
            let found = members(payload).expect("members");
            assert_eq!(found.len(), 2);
            assert!(found.iter().all(|m| m.kind == Kind::Str));
            let text = String::from_utf8(payload.to_vec()).expect("utf-8");
            assert!(text.ends_with(&format!("\"reason\":\"{}\"}}", code.name())));
        }
    }

    #[test]
    fn encode_end_ok_and_error_parse_with_check_end() {
        let reasons = [
            EndReason::Complete,
            EndReason::Unsubscribed,
            EndReason::ClientEof,
        ];
        for reason in reasons {
            for (blocks, dropped) in [(0u32, 0u32), (1, 2), (u32::MAX, u32::MAX)] {
                let frame = end_ok(9, blocks, dropped, reason);
                let (header, payload) = split(&frame);
                assert_eq!((header.frame_type, header.corr), (FrameType::End, 9));
                let end = check_end(payload).expect("END payload");
                assert!(end.ok);
                assert_eq!((end.blocks, end.dropped), (blocks, dropped));
                let found = end.reason.and_then(|range| payload.get(range));
                assert_eq!(found, Some(reason.name().as_bytes()));
                assert_eq!(end.next, None);
            }
        }
        for (code, detail) in [
            (Code::Budget, Detail::Pool(crate::codes::Pool::Rows)),
            (Code::Internal, Detail::Empty),
            (Code::NotFound, Detail::Empty),
        ] {
            let frame = end_error(9, u32::MAX, u32::MAX, code, &detail);
            let (header, payload) = split(&frame);
            assert_eq!((header.frame_type, header.corr), (FrameType::End, 9));
            let end = check_end(payload).expect("END error payload");
            assert!(!end.ok);
            assert_eq!((end.blocks, end.dropped), (u32::MAX, u32::MAX));
            assert_eq!(end.reason, None);
            assert!(text_of(payload, &(0..payload.len())).contains(code.name()));
        }
    }

    #[test]
    fn encode_results_shapes() {
        assert_eq!(current_result(1_000), "{\"stream\":{\"tick_ms\":1000}}");
        assert_eq!(
            subscribe_result(7, 2_000),
            "{\"sub\":7,\"interval_ms\":2000}"
        );
        assert_eq!(UNSUBSCRIBE_RESULT, "{}");
        let current =
            members_up_to(current_result(u32::MAX).as_bytes(), 8).expect("current result object");
        assert_eq!(current.len(), 1);
        let subscribe =
            members_up_to(subscribe_result(0, 0).as_bytes(), 8).expect("subscribe result object");
        assert_eq!(subscribe.len(), 2);
    }
}
