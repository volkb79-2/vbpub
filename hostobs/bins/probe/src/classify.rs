//! The request precedence function (design R11, WIRE.md section 4.2): one pure function that
//! turns a request frame or line into a [`Dispatch`] or a [`Refusal`].
//!
//! The order is fixed, the first failing step wins:
//!
//! 1. the frame type is not REQ: `bad-frame`, `corr` 0, fatal (binary mode only);
//! 2. the frame `corr` is 0 or already in use: `bad-frame`, `corr` 0, fatal (binary mode
//!    only);
//! 3. `check_req` fails: `bad-request` on the frame `corr` (binary) or on the `corr` recovered
//!    under R12 (JSON), `detail.key` from the envelope error under R14;
//! 4. binary mode before `hello`: a verb other than `hello` is `hello-required` on its own
//!    `corr`; `hello` with a `corr` other than 1 is `bad-frame`, `corr` 0 (R9);
//! 5. the verb is not in the table or is reserved: `unknown-verb`;
//! 6. the level does not know the verb: `forbidden`;
//! 7. `hello` when the connection is `Ready` (either mode): `bad-request`, key `verb` (R8);
//! 8. JSON mode and a block verb: `binary-required`;
//! 9. the verb is known but not served by this package: `unknown-verb`;
//! 10. an outstanding `current` or `subscribe`: `too-many-requests` (other verb shapes are
//!     never refused by it);
//! 11. the argument schema (`hello`, `status`, `current`, `subscribe`, `unsubscribe`):
//!     `bad-request` with the schema path, or, for a JSON-mode `hello` with a `proto.major`
//!     other than 1, `unsupported-version` (fatal, R8); a binary `hello.proto` must equal the
//!     client's own prefix (R7).
//!
//! In JSON mode there are no frames, so steps 1 and 2 do not apply and the `frame_type` and
//! `corr` of the [`Query`] are ignored; JSON mode has no `hello` gate here (the first line is
//! the connection state machine's rule, R8). The function reads no clock and keeps no state.

use hostobs_proto::envelope::{check_req, members, EnvelopeError, Kind};
use hostobs_proto::frame::FrameType;

use crate::args::{self, bounded_key, HelloArgs, SubscribeArgs};
use crate::codes::{Code, Detail};
use crate::limits::{IntervalRule, Level};
use crate::selection::Selection;
use crate::verbs::{find, Shape, Verb};

/// The only protocol major version the probe serves.
pub const SERVER_MAJOR: u16 = 1;

/// The `corr` the first request of a binary connection must carry.
pub const HELLO_CORR: u32 = 1;

/// The verb every connection starts with.
const HELLO: &str = "hello";

/// How the connection speaks.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Mode {
    /// Frames with a 12-byte header.
    Binary,
    /// One JSON object per line.
    Json,
}

/// Whether `hello` has been answered.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Phase {
    /// No successful `hello` yet.
    BeforeHello,
    /// `hello` was answered.
    Ready,
}

/// The client's own 8-byte prefix versions (binary mode).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Prefix {
    /// The client's major version.
    pub major: u16,
    /// The client's highest minor version C.
    pub minor: u16,
}

/// The connection facts that join the pure precedence after the verb table.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ConnFacts {
    /// The binary frame `corr` is a live subscription or pending request.
    pub corr_in_use: bool,
    /// `pending.len() >= max_outstanding`.
    pub outstanding_full: bool,
    /// The intervals accepted by the connection's level.
    pub interval: IntervalRule,
}

/// One request to classify.
#[derive(Clone, Copy, Debug)]
pub struct Query<'a> {
    /// The frame type (binary mode; ignored in JSON mode).
    pub frame_type: FrameType,
    /// The frame `corr` (binary mode; ignored in JSON mode, where the payload carries it).
    pub corr: u32,
    /// The connection mode.
    pub mode: Mode,
    /// Whether `hello` was answered.
    pub phase: Phase,
    /// The serving level.
    pub level: Level,
    /// The client's prefix (binary mode; `hello.proto` must equal it).
    pub prefix: Prefix,
    /// The frame payload or the line without its `\n`.
    pub payload: &'a [u8],
}

/// The checked call.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Call {
    /// `hello` with its checked arguments.
    Hello(HelloArgs),
    /// `status` (no arguments).
    Status,
    /// `current` with its checked selection.
    Current(Selection),
    /// `subscribe` with its checked selection and interval.
    Subscribe(SubscribeArgs),
    /// `unsubscribe` with its checked subscription corr.
    Unsubscribe(u32),
}

/// A request that passed every check.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Dispatch {
    /// The `corr` the reply carries.
    pub corr: u32,
    /// The call to execute.
    pub call: Call,
}

/// A request the server refuses.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Refusal {
    /// The wire code.
    pub code: Code,
    /// The `corr` of the error reply (0 when none can be trusted).
    pub corr: u32,
    /// The bounded `detail` of the error envelope.
    pub detail: Detail,
}

impl Refusal {
    /// Whether the refusal closes the connection.
    #[must_use]
    pub const fn fatal(&self) -> bool {
        self.code.fatal()
    }
}

fn refuse(code: Code, corr: u32) -> Refusal {
    Refusal {
        code,
        corr,
        detail: Detail::Empty,
    }
}

fn refuse_key(code: Code, corr: u32, path: &str) -> Refusal {
    Refusal {
        code,
        corr,
        detail: Detail::key(path),
    }
}

/// The `bad-request` for an argument defect: the schema path of the member.
fn bad_args(corr: u32, error: &args::ArgError) -> Refusal {
    refuse_key(Code::BadRequest, corr, &error.key)
}

/// The `detail.key` of an envelope defect (R14).
fn envelope_key(payload: &[u8], error: &EnvelopeError) -> String {
    let echoed = |key: &core::ops::Range<usize>| {
        let name = payload.get(key.clone()).unwrap_or_default();
        bounded_key(name).unwrap_or_default().to_string()
    };
    match error {
        EnvelopeError::Json => String::new(),
        EnvelopeError::MissingKey(name) | EnvelopeError::WrongType(name) => String::from(*name),
        EnvelopeError::DuplicateKey { key } | EnvelopeError::UnknownKey { key } => echoed(key),
    }
}

/// The `corr` of a JSON line that failed `check_req` (R12): echoed only when the members read
/// and `corr` is one exact u32 other than 0, else 0.
fn recovered_corr(payload: &[u8]) -> u32 {
    let Ok(found) = members(payload) else {
        return 0;
    };
    let corr_key = b"corr".as_slice();
    let member = found
        .iter()
        .find(|member| payload.get(member.key.clone()) == Some(corr_key));
    let Some(member) = member else {
        return 0;
    };
    if member.kind != Kind::Number {
        return 0;
    }
    payload
        .get(member.value.clone())
        .and_then(|text| core::str::from_utf8(text).ok())
        .and_then(|text| text.parse::<u32>().ok())
        .unwrap_or_default()
}

/// Steps 1 and 2: the frame itself (binary mode).
fn frame_gate(query: &Query<'_>, facts: &ConnFacts) -> Result<(), Refusal> {
    if query.frame_type != FrameType::Req || query.corr == 0 || facts.corr_in_use {
        return Err(refuse(Code::BadFrame, 0));
    }
    Ok(())
}

/// Steps 4 to 9: the verb row, or the refusal.
fn verb_gate(query: &Query<'_>, corr: u32, name: &[u8]) -> Result<&'static Verb, Refusal> {
    let binary = query.mode == Mode::Binary;
    let hello = name == HELLO.as_bytes();
    if binary && query.phase == Phase::BeforeHello {
        if !hello {
            return Err(refuse(Code::HelloRequired, corr));
        }
        if corr != HELLO_CORR {
            return Err(refuse(Code::BadFrame, 0));
        }
    }
    let verb = match find(name) {
        Some(row) if !row.reserved => row,
        _ => return Err(refuse(Code::UnknownVerb, corr)),
    };
    if !verb.at(query.level) {
        return Err(refuse(Code::Forbidden, corr));
    }
    if hello && query.phase == Phase::Ready {
        return Err(refuse_key(Code::BadRequest, corr, "verb"));
    }
    if !binary && verb.shape == Shape::Block {
        return Err(refuse(Code::BinaryRequired, corr));
    }
    if !verb.served {
        return Err(refuse(Code::UnknownVerb, corr));
    }
    Ok(verb)
}

/// Step 10 for `hello`: the schema, then the version rules.
fn hello_call(query: &Query<'_>, corr: u32, text: &[u8]) -> Result<Call, Refusal> {
    let hello = match args::hello(text) {
        Ok(hello) => hello,
        Err(error) => return Err(bad_args(corr, &error)),
    };
    match query.mode {
        Mode::Json => {
            if hello.major != SERVER_MAJOR {
                return Err(refuse(Code::UnsupportedVersion, corr));
            }
        }
        Mode::Binary => {
            if hello.major != query.prefix.major {
                return Err(refuse_key(Code::BadRequest, corr, "proto.major"));
            }
            if hello.minor != query.prefix.minor {
                return Err(refuse_key(Code::BadRequest, corr, "proto.minor"));
            }
        }
    }
    Ok(Call::Hello(hello))
}

/// Step 10: the argument schema of the served verb.
fn call_of(
    query: &Query<'_>,
    corr: u32,
    verb: &Verb,
    text: &[u8],
    facts: &ConnFacts,
) -> Result<Call, Refusal> {
    match verb.name {
        HELLO => hello_call(query, corr, text),
        "status" => match args::status(text) {
            Ok(()) => Ok(Call::Status),
            Err(error) => Err(bad_args(corr, &error)),
        },
        "current" => match args::current(text) {
            Ok(selection) => Ok(Call::Current(selection)),
            Err(error) => Err(bad_args(corr, &error)),
        },
        "subscribe" => match args::subscribe(text, facts.interval) {
            Ok(subscribe) => Ok(Call::Subscribe(subscribe)),
            Err(error) => Err(bad_args(corr, &error)),
        },
        "unsubscribe" => match args::unsubscribe(text) {
            Ok(sub) => Ok(Call::Unsubscribe(sub)),
            Err(error) => Err(bad_args(corr, &error)),
        },
        // Unreachable while `verb_gate` passes only served verbs with a schema here: a served
        // verb without one is a server defect, never the client's `unknown-verb`.
        _ => Err(refuse(Code::Internal, corr)),
    }
}

/// Steps 10 and 11 after the verb table has accepted a served verb. Block requests are
/// refused by the outstanding cap; JSON-shape requests remain answerable (design S5, Q6).
fn call_after_served(
    query: &Query<'_>,
    corr: u32,
    verb: &Verb,
    text: &[u8],
    facts: &ConnFacts,
) -> Result<Call, Refusal> {
    if query.mode == Mode::Binary && facts.outstanding_full && verb.shape == Shape::Block {
        return Err(refuse(Code::TooManyRequests, corr));
    }
    call_of(query, corr, verb, text, facts)
}

/// Classify one request (module doc: the precedence list).
///
/// # Errors
///
/// A [`Refusal`]; [`Refusal::fatal`] says whether the connection closes.
pub fn classify(query: &Query<'_>) -> Result<Dispatch, Refusal> {
    classify_with(query, &ConnFacts::default())
}

/// Classify one request with the connection's live `corr`, outstanding and interval facts.
///
/// # Errors
///
/// A [`Refusal`]; [`Refusal::fatal`] says whether the connection closes.
pub fn classify_with(query: &Query<'_>, facts: &ConnFacts) -> Result<Dispatch, Refusal> {
    let binary = query.mode == Mode::Binary;
    if binary {
        frame_gate(query, facts)?;
    }
    let req = match check_req(query.payload, !binary) {
        Ok(req) => req,
        Err(error) => {
            let corr = if binary {
                query.corr
            } else {
                recovered_corr(query.payload)
            };
            let path = envelope_key(query.payload, &error);
            return Err(refuse_key(Code::BadRequest, corr, &path));
        }
    };
    let corr = if binary {
        query.corr
    } else {
        req.corr.unwrap_or_default()
    };
    let name = query.payload.get(req.verb.clone()).unwrap_or_default();
    let verb = verb_gate(query, corr, name)?;
    let text = query.payload.get(req.args.clone()).unwrap_or_default();
    let call = call_after_served(query, corr, verb, text, facts)?;
    Ok(Dispatch { corr, call })
}

#[cfg(test)]
mod tests {
    use super::{
        call_after_served, call_of, classify, classify_with, Call, ConnFacts, Dispatch, Mode,
        Phase, Prefix, Query, Refusal, HELLO_CORR, SERVER_MAJOR,
    };
    use crate::codes::{Code, Detail};
    use crate::limits::{IntervalRule, Level};
    use crate::verbs::{find, Shape};
    use hostobs_proto::frame::FrameType;

    const HELLO_ARGS: &str = "{\"client\":\"t\",\"proto\":{\"major\":1,\"minor\":1}}";

    fn prefix(major: u16, minor: u16) -> Prefix {
        Prefix { major, minor }
    }

    fn binary(payload: &[u8]) -> Query<'_> {
        Query {
            frame_type: FrameType::Req,
            corr: 5,
            mode: Mode::Binary,
            phase: Phase::Ready,
            level: Level::Observe,
            prefix: prefix(1, 1),
            payload,
        }
    }

    fn json(payload: &[u8]) -> Query<'_> {
        Query {
            mode: Mode::Json,
            corr: 0,
            ..binary(payload)
        }
    }

    fn req(verb: &str, args: &str) -> String {
        format!("{{\"verb\":\"{verb}\",\"args\":{args}}}")
    }

    fn line(verb: &str, args: &str, corr: u32) -> String {
        format!("{{\"verb\":\"{verb}\",\"args\":{args},\"corr\":{corr}}}")
    }

    fn hello_with(major: u32, minor: u32) -> String {
        let proto = format!("{{\"major\":{major},\"minor\":{minor}}}");
        format!("{{\"client\":\"t\",\"proto\":{proto}}}")
    }

    fn refused(query: &Query<'_>) -> Refusal {
        classify(query).expect_err("refused")
    }

    fn dispatched(query: &Query<'_>) -> Dispatch {
        classify(query).expect("dispatched")
    }

    fn status_dispatch(corr: u32) -> Dispatch {
        Dispatch {
            corr,
            call: Call::Status,
        }
    }

    fn key(path: &str) -> Detail {
        Detail::Key(path.to_string())
    }

    fn assert_refusal(query: &Query<'_>, code: Code, corr: u32, detail: &Detail) {
        let refusal = refused(query);
        assert_eq!(refusal.code, code, "{refusal:?}");
        assert_eq!(refusal.corr, corr, "{refusal:?}");
        assert_eq!(&refusal.detail, detail, "{refusal:?}");
        assert_eq!(refusal.fatal(), code.fatal());
    }

    fn assert_plain(query: &Query<'_>, code: Code, corr: u32) {
        assert_refusal(query, code, corr, &Detail::Empty);
    }

    fn with_key(name: &str) -> String {
        format!("{{\"verb\":\"status\",\"args\":{{}},\"{name}\":1}}")
    }

    fn before_hello(payload: &[u8]) -> Query<'_> {
        Query {
            phase: Phase::BeforeHello,
            corr: HELLO_CORR,
            ..binary(payload)
        }
    }

    // ---- the accepted shapes

    #[test]
    fn a_status_request_dispatches_in_both_modes() {
        let text = req("status", "{}");
        let done = dispatched(&binary(text.as_bytes()));
        assert_eq!(done, status_dispatch(5));
        let text = line("status", "{}", 9);
        let done = dispatched(&json(text.as_bytes()));
        assert_eq!(done, status_dispatch(9));
    }

    #[test]
    fn a_hello_dispatches_with_its_arguments() {
        let text = req("hello", HELLO_ARGS);
        let query = Query {
            corr: HELLO_CORR,
            ..before_hello(text.as_bytes())
        };
        let done = dispatched(&query);
        assert_eq!(done.corr, 1);
        match done.call {
            Call::Hello(hello) => {
                assert_eq!(hello.client, "t");
                assert_eq!((hello.major, hello.minor), (1, 1));
            }
            _ => panic!("status"),
        }
    }

    // ---- step 1: the frame type

    #[test]
    fn step_1_a_frame_that_is_not_a_request_is_bad_frame() {
        let text = req("status", "{}");
        for frame_type in [FrameType::Rep, FrameType::Evt, FrameType::End] {
            let query = Query {
                frame_type,
                ..binary(text.as_bytes())
            };
            assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
            assert!(refused(&query).fatal());
        }
    }

    #[test]
    fn step_1_beats_step_3_and_step_4() {
        let query = Query {
            frame_type: FrameType::Rep,
            ..binary(b"garbage")
        };
        assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
        let text = req("status", "{}");
        let query = Query {
            frame_type: FrameType::Evt,
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
    }

    #[test]
    fn step_1_does_not_apply_in_json_mode() {
        let text = line("status", "{}", 3);
        let query = Query {
            frame_type: FrameType::Rep,
            ..json(text.as_bytes())
        };
        assert_eq!(dispatched(&query).corr, 3);
    }

    // ---- step 2: corr 0

    #[test]
    fn step_2_a_binary_corr_of_zero_is_bad_frame() {
        let text = req("status", "{}");
        let query = Query {
            corr: 0,
            ..binary(text.as_bytes())
        };
        assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
    }

    #[test]
    fn step_2_beats_step_3_and_step_4() {
        let query = Query {
            corr: 0,
            ..binary(b"{")
        };
        assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
        let text = req("status", "{}");
        let query = Query {
            corr: 0,
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
    }

    // ---- step 3: check_req

    #[test]
    fn step_3_a_defective_payload_is_bad_request_on_the_frame_corr() {
        let query = binary(b"not json");
        assert_refusal(&query, Code::BadRequest, 5, &key(""));
        assert!(!refused(&query).fatal());
        let query = binary(b"{\"verb\":\"status\"}");
        assert_refusal(&query, Code::BadRequest, 5, &key("args"));
        let query = binary(b"{\"verb\":7,\"args\":{}}");
        assert_refusal(&query, Code::BadRequest, 5, &key("verb"));
        let query = binary(b"{\"args\":{}}");
        assert_refusal(&query, Code::BadRequest, 5, &key("verb"));
        let query = binary(b"{\"verb\":\"status\",\"args\":[]}");
        assert_refusal(&query, Code::BadRequest, 5, &key("args"));
    }

    #[test]
    fn step_3_a_corr_key_in_a_binary_payload_is_an_unknown_key() {
        let text = line("status", "{}", 4);
        let query = binary(text.as_bytes());
        assert_refusal(&query, Code::BadRequest, 5, &key("corr"));
    }

    #[test]
    fn step_3_beats_step_4_and_step_5() {
        let query = Query {
            payload: b"{\"verb\":\"status\"}",
            ..before_hello(b"")
        };
        assert_refusal(&query, Code::BadRequest, 1, &key("args"));
        let query = binary(b"{\"verb\":\"nope\"}");
        assert_refusal(&query, Code::BadRequest, 5, &key("args"));
    }

    #[test]
    fn step_3_names_a_duplicate_and_an_unknown_key_when_bounded() {
        let query = binary(b"{\"verb\":\"status\",\"args\":{},\"verb\":\"x\"}");
        assert_refusal(&query, Code::BadRequest, 5, &key("verb"));
        let query = binary(b"{\"verb\":\"status\",\"args\":{},\"uid\":0}");
        assert_refusal(&query, Code::BadRequest, 5, &key("uid"));
        let long = "k".repeat(64);
        let text = format!("{{\"verb\":\"status\",\"args\":{{}},\"{long}\":0}}");
        let query = binary(text.as_bytes());
        assert_refusal(&query, Code::BadRequest, 5, &key(&long));
    }

    #[test]
    fn step_3_stops_at_the_parent_for_an_unbounded_key() {
        let long = "k".repeat(65);
        let text = format!("{{\"verb\":\"status\",\"args\":{{}},\"{long}\":0}}");
        assert_refusal(&binary(text.as_bytes()), Code::BadRequest, 5, &key(""));
        let huge = "k".repeat(30_000);
        let text = format!("{{\"verb\":\"status\",\"args\":{{}},\"{huge}\":0}}");
        let refusal = refused(&binary(text.as_bytes()));
        assert_eq!(refusal.detail, key(""));
        let spaced = "{\"verb\":\"status\",\"args\":{},\"a b\":0}";
        assert_refusal(&binary(spaced.as_bytes()), Code::BadRequest, 5, &key(""));
        let slash = char::from(0x5c);
        let text = with_key(&format!("v{slash}u0065rb"));
        assert_refusal(&binary(text.as_bytes()), Code::BadRequest, 5, &key(""));
    }

    #[test]
    fn step_3_refuses_a_verb_that_spells_itself_with_an_escape() {
        let slash = char::from(0x5c);
        let text = format!("{{\"verb\":\"sta{slash}u0074us\",\"args\":{{}}}}");
        assert_refusal(&binary(text.as_bytes()), Code::BadRequest, 5, &key("verb"));
    }

    // ---- R12: the JSON corr

    #[test]
    fn r12_a_defective_line_echoes_one_exact_nonzero_corr() {
        let text = "{\"verb\":7,\"args\":{},\"corr\":9}";
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 9, &key("verb"));
        let text = "{\"verb\":\"status\",\"args\":{},\"corr\":4,\"x\":1}";
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 4, &key("x"));
        let text = "{\"verb\":\"status\",\"args\":{},\"corr\":4294967295,\"x\":1}";
        let max = u32::MAX;
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, max, &key("x"));
    }

    #[test]
    fn r12_otherwise_the_corr_is_zero() {
        let cases = [
            "{\"verb\":\"status\",\"args\":{},\"corr\":0}",
            "{\"verb\":\"status\",\"args\":{},\"corr\":\"4\"}",
            "{\"verb\":\"status\",\"args\":{},\"corr\":4294967296}",
            "{\"verb\":\"status\",\"args\":{},\"corr\":1.5}",
            "{\"verb\":\"status\",\"args\":{},\"corr\":1e2}",
            "{\"verb\":\"status\",\"args\":{},\"corr\":-1}",
            "{\"verb\":\"status\",\"args\":{},\"corr\":4,\"corr\":4}",
            "{\"verb\":\"status\",\"args\":{}}",
            "{\"verb\":\"status\",\"corr\":5",
            "garbage",
        ];
        for text in cases {
            let refusal = refused(&json(text.as_bytes()));
            assert_eq!(refusal.code, Code::BadRequest, "{text}");
            assert_eq!(refusal.corr, 0, "{text}");
            assert!(!refusal.fatal(), "{text}");
        }
    }

    #[test]
    fn r12_a_json_corr_of_zero_is_bad_request_key_corr_and_not_fatal() {
        let text = line("status", "{}", 0);
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 0, &key("corr"));
    }

    #[test]
    fn r12_a_missing_corr_is_bad_request_key_corr() {
        let text = req("status", "{}");
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 0, &key("corr"));
    }

    #[test]
    fn r12_a_json_defect_wins_the_key_in_member_order() {
        let text = "{\"verb\":\"status\",\"args\":{},\"corr\":\"x\"}";
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 0, &key("corr"));
        let text = "{\"verb\":1,\"args\":{},\"corr\":0}";
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 0, &key("verb"));
    }

    // ---- step 4: the hello gate (binary)

    #[test]
    fn step_4_a_verb_before_hello_is_hello_required_on_its_corr() {
        let text = req("status", "{}");
        let query = Query {
            corr: 7,
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::HelloRequired, 7, &Detail::Empty);
        assert!(refused(&query).fatal());
    }

    #[test]
    fn step_4_hello_on_another_corr_is_bad_frame_corr_zero() {
        let text = req("hello", HELLO_ARGS);
        for corr in [2, 5, u32::MAX] {
            let query = Query {
                corr,
                ..before_hello(text.as_bytes())
            };
            assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
        }
    }

    #[test]
    fn step_4_beats_step_5_and_step_6() {
        let text = req("nope", "{}");
        let query = Query {
            corr: 3,
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::HelloRequired, 3, &Detail::Empty);
        let text = req("gc", "{}");
        let query = Query {
            corr: 3,
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::HelloRequired, 3, &Detail::Empty);
        let text = req("annotate", "{}");
        let query = Query {
            corr: 3,
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::HelloRequired, 3, &Detail::Empty);
    }

    #[test]
    fn step_4_beats_step_10() {
        let text = req("hello", "{\"x\":1}");
        let query = Query {
            corr: 2,
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::BadFrame, 0, &Detail::Empty);
    }

    #[test]
    fn step_4_json_mode_has_no_hello_gate() {
        let text = line("status", "{}", 2);
        let query = Query {
            phase: Phase::BeforeHello,
            ..json(text.as_bytes())
        };
        assert_eq!(dispatched(&query).call, Call::Status);
        let text = line("hello", HELLO_ARGS, 2);
        let query = Query {
            phase: Phase::BeforeHello,
            ..json(text.as_bytes())
        };
        assert_eq!(dispatched(&query).corr, 2);
    }

    // ---- step 5: the table and the reserved verbs

    #[test]
    fn step_5_an_unknown_or_reserved_verb_is_unknown_verb() {
        let spelled = ["nope", "", "Status", "status ", "session.watch"];
        let reserved = ["gc", "reload", "export"];
        for verb in spelled.iter().chain(reserved.iter()) {
            let text = req(verb, "{}");
            let query = Query {
                level: Level::Control,
                ..binary(text.as_bytes())
            };
            assert_refusal(&query, Code::UnknownVerb, 5, &Detail::Empty);
        }
    }

    #[test]
    fn step_5_beats_step_6_and_step_8() {
        let text = req("gc", "{}");
        assert_plain(&binary(text.as_bytes()), Code::UnknownVerb, 5);
        let text = line("export", "{}", 6);
        let query = Query {
            level: Level::Operator,
            ..json(text.as_bytes())
        };
        assert_refusal(&query, Code::UnknownVerb, 6, &Detail::Empty);
    }

    #[test]
    fn step_5_never_looks_inside_the_args_of_an_unknown_verb() {
        let text = req("nope", "{\"a\":1,\"a\":2}");
        assert_plain(&binary(text.as_bytes()), Code::UnknownVerb, 5);
    }

    // ---- step 6: the level

    #[test]
    fn step_6_a_verb_the_level_does_not_know_is_forbidden() {
        let text = req("annotate", "{}");
        assert_refusal(&binary(text.as_bytes()), Code::Forbidden, 5, &Detail::Empty);
        let text = req("status", "{}");
        let query = Query {
            level: Level::Observer,
            ..binary(text.as_bytes())
        };
        assert_refusal(&query, Code::Forbidden, 5, &Detail::Empty);
        let text = req("current", "{}");
        let query = Query {
            level: Level::Observer,
            ..binary(text.as_bytes())
        };
        assert_refusal(&query, Code::Forbidden, 5, &Detail::Empty);
    }

    #[test]
    fn step_6_beats_step_8_step_9_and_step_10() {
        let text = line("current", "{}", 8);
        let query = Query {
            level: Level::Observer,
            ..json(text.as_bytes())
        };
        assert_refusal(&query, Code::Forbidden, 8, &Detail::Empty);
        let text = req("status", "{\"x\":1}");
        let query = Query {
            level: Level::Observer,
            ..binary(text.as_bytes())
        };
        assert_refusal(&query, Code::Forbidden, 5, &Detail::Empty);
    }

    // ---- step 7: a second hello

    #[test]
    fn step_7_hello_when_ready_is_bad_request_key_verb_in_both_modes() {
        let text = req("hello", HELLO_ARGS);
        assert_refusal(&binary(text.as_bytes()), Code::BadRequest, 5, &key("verb"));
        let text = line("hello", HELLO_ARGS, 6);
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 6, &key("verb"));
    }

    #[test]
    fn step_7_beats_step_10() {
        let text = req("hello", "{\"x\":1}");
        assert_refusal(&binary(text.as_bytes()), Code::BadRequest, 5, &key("verb"));
        let text = line("hello", &hello_with(2, 1), 6);
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 6, &key("verb"));
    }

    // ---- step 8: block verbs in JSON mode

    #[test]
    fn step_8_a_block_verb_in_json_mode_is_binary_required() {
        for verb in ["current", "subscribe", "snapshot_at", "series_batch"] {
            let text = line(verb, "{}", 3);
            assert_plain(&json(text.as_bytes()), Code::BinaryRequired, 3);
        }
    }

    #[test]
    fn step_8_beats_step_9_and_step_10() {
        let text = line("current", "{\"a\":1,\"a\":2}", 3);
        assert_plain(&json(text.as_bytes()), Code::BinaryRequired, 3);
        let text = req("current", "{}");
        assert_plain(&binary(text.as_bytes()), Code::UnknownVerb, 5);
    }

    // ---- step 9: known but not served

    #[test]
    fn step_9_a_known_verb_that_is_not_served_is_unknown_verb() {
        for verb in ["events", "spans", "procs", "unsubscribe", "current"] {
            let text = req(verb, "{}");
            assert_plain(&binary(text.as_bytes()), Code::UnknownVerb, 5);
        }
        for verb in ["events", "spans", "procs", "unsubscribe"] {
            let text = line(verb, "{}", 5);
            assert_refusal(&json(text.as_bytes()), Code::UnknownVerb, 5, &Detail::Empty);
        }
    }

    #[test]
    fn step_9_beats_step_10() {
        let text = req("events", "{\"a\":1,\"a\":2}");
        assert_plain(&binary(text.as_bytes()), Code::UnknownVerb, 5);
    }

    // ---- step 10: the argument schemas

    #[test]
    fn step_10_status_takes_no_arguments() {
        let text = req("status", "{\"a\":1}");
        assert_refusal(&binary(text.as_bytes()), Code::BadRequest, 5, &key("a"));
        let text = req("status", "{\"uid\":0}");
        assert_refusal(&binary(text.as_bytes()), Code::BadRequest, 5, &key("uid"));
        let text = line("status", "{\"pid\":1,\"pid\":1}", 6);
        assert_refusal(&json(text.as_bytes()), Code::BadRequest, 6, &key("pid"));
    }

    #[test]
    fn step_10_hello_arguments_name_the_member() {
        let cases = [
            ("{\"proto\":{\"major\":1,\"minor\":1}}", "client"),
            (
                "{\"client\":1,\"proto\":{\"major\":1,\"minor\":1}}",
                "client",
            ),
            ("{\"client\":\"t\"}", "proto"),
            ("{\"client\":\"t\",\"proto\":{\"minor\":1}}", "proto.major"),
            ("{\"client\":\"t\",\"proto\":{\"major\":1}}", "proto.minor"),
            (
                "{\"client\":\"t\",\"proto\":{\"major\":1,\"minor\":65536}}",
                "proto.minor",
            ),
            (
                "{\"client\":\"t\",\"proto\":{\"major\":-1,\"minor\":1}}",
                "proto.major",
            ),
            (
                "{\"client\":\"t\",\"proto\":{\"major\":1,\"minor\":1.0}}",
                "proto.minor",
            ),
            (
                "{\"client\":\"t\",\"proto\":{\"major\":1,\"minor\":1},\"uid\":0}",
                "uid",
            ),
        ];
        for (args, path) in cases {
            let text = req("hello", args);
            let query = Query {
                corr: HELLO_CORR,
                ..before_hello(text.as_bytes())
            };
            assert_refusal(&query, Code::BadRequest, 1, &key(path));
        }
    }

    #[test]
    fn r6_invalid_hello_arguments_keep_the_connection() {
        let text = req("hello", "{}");
        let query = before_hello(text.as_bytes());
        assert!(!refused(&query).fatal());
    }

    #[test]
    fn r7_a_binary_hello_must_equal_the_client_prefix() {
        let text = req("hello", &hello_with(1, 3));
        let query = Query {
            prefix: prefix(1, 3),
            ..before_hello(text.as_bytes())
        };
        let done = dispatched(&query);
        match done.call {
            Call::Hello(hello) => assert_eq!(hello.minor, 3),
            _ => panic!("status"),
        }
        let query = Query {
            prefix: prefix(1, 2),
            ..before_hello(text.as_bytes())
        };
        assert_refusal(&query, Code::BadRequest, 1, &key("proto.minor"));
        let text = req("hello", &hello_with(2, 1));
        let query = before_hello(text.as_bytes());
        assert_refusal(&query, Code::BadRequest, 1, &key("proto.major"));
        assert!(!refused(&query).fatal());
        let text = req("hello", &hello_with(2, 9));
        let query = before_hello(text.as_bytes());
        assert_refusal(&query, Code::BadRequest, 1, &key("proto.major"));
    }

    #[test]
    fn r7_a_binary_hello_with_major_0_or_minor_0_is_refused_against_prefix_1_1() {
        let text = req("hello", &hello_with(0, 1));
        let query = before_hello(text.as_bytes());
        assert_refusal(&query, Code::BadRequest, 1, &key("proto.major"));
        assert!(!refused(&query).fatal());
        let text = req("hello", &hello_with(1, 0));
        let query = before_hello(text.as_bytes());
        assert_refusal(&query, Code::BadRequest, 1, &key("proto.minor"));
        assert!(!refused(&query).fatal());
    }

    #[test]
    fn r8_a_json_hello_negotiates_from_any_minor_and_refuses_another_major() {
        let phase = Phase::BeforeHello;
        for minor in [0, 1, 7, 65_535] {
            let text = line("hello", &hello_with(1, minor), 2);
            let query = Query {
                phase,
                ..json(text.as_bytes())
            };
            match dispatched(&query).call {
                Call::Hello(hello) => assert_eq!(u32::from(hello.minor), minor),
                _ => panic!("status"),
            }
        }
        for major in [0, 2, 65_535] {
            let text = line("hello", &hello_with(major, 1), 2);
            let query = Query {
                phase,
                ..json(text.as_bytes())
            };
            assert_refusal(&query, Code::UnsupportedVersion, 2, &Detail::Empty);
            assert!(refused(&query).fatal());
        }
        assert_eq!(SERVER_MAJOR, 1);
    }

    #[test]
    fn r8_the_schema_is_checked_before_the_json_major() {
        let args = "{\"client\":1,\"proto\":{\"major\":2,\"minor\":1}}";
        let text = line("hello", args, 2);
        let query = Query {
            phase: Phase::BeforeHello,
            ..json(text.as_bytes())
        };
        assert_refusal(&query, Code::BadRequest, 2, &key("client"));
    }

    #[test]
    fn r23_a_member_naming_an_identity_is_bad_request() {
        for name in ["uid", "pid", "cgroup"] {
            let base = hello_with(1, 1);
            let open = base.strip_suffix('}').unwrap_or(&base);
            let args = format!("{open},\"{name}\":1}}");
            let text = req("hello", &args);
            let query = Query {
                corr: HELLO_CORR,
                ..before_hello(text.as_bytes())
            };
            assert_refusal(&query, Code::BadRequest, 1, &key(name));
        }
    }

    // ---- P5b-2a: connection facts and the new calls

    const CURRENT_ARGS: &str = "{\"selection\":{\"entities\":[{\"inc\":1}],\"metrics\":[\"cpu\"]}}";
    const SUBSCRIBE_ARGS: &str = concat!(
        "{\"selection\":{\"entities\":[{\"inc\":1}],\"metrics\":[\"cpu\"]},",
        "\"interval_ms\":1000}"
    );

    fn full_facts() -> ConnFacts {
        ConnFacts {
            corr_in_use: false,
            outstanding_full: true,
            interval: IntervalRule {
                min_ms: 1_000,
                tick_ms: 1_000,
            },
        }
    }

    fn assert_default_facts_unchanged(query: &Query<'_>) {
        let facts = ConnFacts::default();
        assert_eq!(classify(query), classify_with(query, &facts));
    }

    #[test]
    fn classify_step_2_corr_in_use_is_bad_frame_before_check_req() {
        let valid = req("status", "{}");
        let facts = ConnFacts {
            corr_in_use: true,
            ..ConnFacts::default()
        };
        let refusal = classify_with(&binary(valid.as_bytes()), &facts).expect_err("corr in use");
        assert_eq!(
            (refusal.code, refusal.corr, refusal.detail),
            (Code::BadFrame, 0, Detail::Empty)
        );
        let refusal = classify_with(&binary(b"{"), &facts).expect_err("before check_req");
        assert_eq!(
            (refusal.code, refusal.corr, refusal.detail),
            (Code::BadFrame, 0, Detail::Empty)
        );
    }

    #[test]
    fn classify_step_10_too_many_requests_after_served_before_schema() {
        let current = find(b"current").expect("current row");
        let query = binary(b"{}");
        let refusal = call_after_served(&query, 5, current, b"{}", &full_facts())
            .expect_err("the outstanding cap wins");
        assert_eq!(
            (refusal.code, refusal.corr, refusal.detail),
            (Code::TooManyRequests, 5, Detail::Empty)
        );
        let unknown = req("nope", "{}");
        assert_plain(&binary(unknown.as_bytes()), Code::UnknownVerb, 5);
        let line = line("current", "{}", 6);
        assert_plain(&json(line.as_bytes()), Code::BinaryRequired, 6);
        assert_eq!(current.shape, Shape::Block);
    }

    #[test]
    fn classify_step_10_spares_json_shape_verbs() {
        let status = find(b"status").expect("status row");
        let status_text = req("status", "{}");
        let query = binary(status_text.as_bytes());
        let call = call_after_served(&query, 5, status, b"{}", &full_facts())
            .expect("status is JSON-shaped");
        assert_eq!(call, Call::Status);
        let unsubscribe = find(b"unsubscribe").expect("unsubscribe row");
        let text = req("unsubscribe", "{\"sub\":4}");
        let query = binary(text.as_bytes());
        let call = call_after_served(&query, 5, unsubscribe, b"{\"sub\":4}", &full_facts())
            .expect("unsubscribe is JSON-shaped");
        assert_eq!(call, Call::Unsubscribe(4));
        assert_eq!(status.shape, Shape::Json);
        assert_eq!(unsubscribe.shape, Shape::Json);
    }

    #[test]
    fn classify_new_calls_via_call_of() {
        let current = find(b"current").expect("current row");
        let current_text = req("current", CURRENT_ARGS);
        let query = binary(current_text.as_bytes());
        let call = call_of(&query, 5, current, CURRENT_ARGS.as_bytes(), &full_facts())
            .expect("current schema");
        match call {
            Call::Current(selection) => assert_eq!(selection.entities().len(), 1),
            other => panic!("current: {other:?}"),
        }

        let subscribe = find(b"subscribe").expect("subscribe row");
        let subscribe_text = req("subscribe", SUBSCRIBE_ARGS);
        let query = binary(subscribe_text.as_bytes());
        let call = call_of(
            &query,
            5,
            subscribe,
            SUBSCRIBE_ARGS.as_bytes(),
            &full_facts(),
        )
        .expect("subscribe schema");
        match call {
            Call::Subscribe(args) => assert_eq!(args.interval_ms, 1_000),
            other => panic!("subscribe: {other:?}"),
        }

        let unsubscribe = find(b"unsubscribe").expect("unsubscribe row");
        let text = req("unsubscribe", "{\"sub\":0}");
        let query = binary(text.as_bytes());
        let call = call_of(&query, 5, unsubscribe, b"{\"sub\":0}", &full_facts())
            .expect("unsubscribe schema");
        assert_eq!(call, Call::Unsubscribe(0));
    }

    #[test]
    fn classify_default_facts_equal_old_classify() {
        let status = req("status", "{}");
        let query = binary(status.as_bytes());
        assert_default_facts_unchanged(&query);

        let status = line("status", "{}", 3);
        let query = json(status.as_bytes());
        assert_default_facts_unchanged(&query);

        let hello = req("hello", HELLO_ARGS);
        let query = Query {
            corr: HELLO_CORR,
            ..before_hello(hello.as_bytes())
        };
        assert_default_facts_unchanged(&query);

        let query = binary(b"garbage");
        assert_default_facts_unchanged(&query);

        let events = req("events", "{\"a\":1,\"a\":2}");
        let query = binary(events.as_bytes());
        assert_default_facts_unchanged(&query);
    }

    // ---- a deterministic hostile table

    fn hostile() -> Vec<Vec<u8>> {
        let mut table: Vec<Vec<u8>> = vec![
            Vec::new(),
            b"x".to_vec(),
            b"{".to_vec(),
            b"}".to_vec(),
            b"[]".to_vec(),
            b"null".to_vec(),
            b"{}".to_vec(),
            b"{\"verb\":\"status\",\"args\":{}} x".to_vec(),
            b"{\"verb\":\"status\",\"args\":{}".to_vec(),
            b"{\"verb\":\"status\",\"args\":{\"a\":".to_vec(),
            b"\xEF\xBB\xBF{\"verb\":\"status\",\"args\":{}}".to_vec(),
            b"{\"verb\":\"\xFF\",\"args\":{}}".to_vec(),
            b"{\"verb\":\"status\",\"args\":{}}\r".to_vec(),
            b"{\"verb\":\"status\",\"args\":{},}".to_vec(),
        ];
        let deep = format!("{}1{}", "{\"a\":".repeat(9), "}".repeat(9));
        let text = format!("{{\"verb\":\"status\",\"args\":{deep}}}");
        table.push(text.into_bytes());
        let huge = "k".repeat(30_000);
        table.push(format!("{{\"{huge}\":1}}").into_bytes());
        table.push(vec![b'{'; 5_000]);
        table.push(vec![0; 64]);
        table
    }

    #[test]
    fn hostile_payloads_are_non_fatal_bad_requests_in_both_modes() {
        for payload in hostile() {
            let refusal = refused(&binary(&payload));
            assert_eq!(refusal.code, Code::BadRequest);
            assert_eq!(refusal.corr, 5);
            assert!(!refusal.fatal());
            match &refusal.detail {
                Detail::Key(path) => assert!(path.len() <= Detail::MAX_KEY_BYTES),
                Detail::Empty => panic!("an error detail is a key"),
                Detail::Pool(pool) => panic!("unexpected pool {pool:?}"),
            }
            let refusal = refused(&json(&payload));
            assert_eq!(refusal.code, Code::BadRequest);
            assert!(!refusal.fatal());
        }
    }

    #[test]
    fn hostile_payloads_before_hello_are_bad_request_not_hello_required() {
        for payload in hostile() {
            let refusal = refused(&before_hello(&payload));
            assert_eq!(refusal.code, Code::BadRequest);
            assert_eq!(refusal.corr, 1);
        }
    }

    #[test]
    fn the_closed_list_of_refusal_codes_is_the_documented_one() {
        let text = req("status", "{}");
        let queries = [
            Query {
                corr: 0,
                ..binary(text.as_bytes())
            },
            Query {
                corr: 3,
                ..before_hello(text.as_bytes())
            },
            Query {
                level: Level::Observer,
                ..binary(text.as_bytes())
            },
        ];
        let allowed = [
            Code::BadFrame,
            Code::HelloRequired,
            Code::Forbidden,
            Code::UnknownVerb,
            Code::BadRequest,
            Code::BinaryRequired,
            Code::UnsupportedVersion,
        ];
        for query in queries {
            assert!(allowed.contains(&refused(&query).code));
        }
    }
}
