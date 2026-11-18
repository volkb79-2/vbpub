//! The `hello` reply of a connection (WIRE.md §1.4, §4.4) and the checks the client makes on it.

use hostobs_proto::envelope::Kind;
use hostobs_proto::frame::{FRAME_HEADER_LEN, PROTO_MAJOR};

use crate::error::{ClientError, ProtocolKind};
use crate::json::Object;
use crate::level::Level;

/// What the client assumes before the server's limits are known: the observe/operator request
/// bound of WIRE.md §5.1.
pub(crate) const PRE_HELLO_MAX_REQUEST: u32 = 32 * 1024;

/// The codec's own frame bound (WIRE.md §5.2): the client reads with it until the hello.
pub(crate) const PRE_HELLO_MAX_FRAME: u32 = 16 * 1024 * 1024;

/// The refusal of every hello defect (WIRE.md §12 `P3b`: `hello-mismatch`).
const MISMATCH: ClientError = ClientError::Protocol(ProtocolKind::HelloMismatch);

/// The bounds a server announces in `hello.limits` (WIRE.md §4.4, §5.1). Every field is an
/// exact `u32`; a client must not assume a limit it was not given.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Limits {
    /// Largest frame either side accepts.
    pub max_frame_bytes: u32,
    /// Largest request frame the server reads.
    pub max_request_bytes: u32,
    /// Request deadline, first byte to complete, in milliseconds.
    pub request_deadline_ms: u32,
    /// Hello deadline in milliseconds.
    pub hello_deadline_ms: u32,
    /// Idle deadline in milliseconds.
    pub idle_ms: u32,
    /// Writer stall bound in milliseconds.
    pub stall_ms: u32,
    /// Frame completion bound in milliseconds.
    pub frame_completion_ms: u32,
    /// Outstanding requests per connection.
    pub max_outstanding: u32,
    /// Live subscriptions per connection.
    pub max_subscriptions: u32,
    /// Queued subscription block bytes per connection.
    pub client_queue_bytes: u32,
    /// Queued query block bytes per connection.
    pub query_queue_bytes: u32,
    /// Queued REP/EVT/END bytes per connection.
    pub control_queue_bytes: u32,
    /// Largest JSON REP the server builds.
    pub max_reply_bytes: u32,
    /// Largest BLOCK the server builds (0 at levels without block verbs).
    pub max_block_bytes: u32,
    /// Most rows a selection may resolve to.
    pub max_rows: u32,
    /// Most buckets per history block.
    pub max_block_buckets: u32,
    /// Most blocks per `series_batch` page.
    pub max_page_blocks: u32,
    /// Smallest subscription interval in milliseconds.
    pub min_interval_ms: u32,
    /// The sampling tick in milliseconds.
    pub tick_ms: u32,
}

impl Limits {
    /// The assumptions that hold before the hello reply: the request bound of the levels
    /// with the smallest default and the codec's frame bound.
    pub(crate) fn pre_hello() -> Limits {
        Limits {
            max_frame_bytes: PRE_HELLO_MAX_FRAME,
            max_request_bytes: PRE_HELLO_MAX_REQUEST,
            ..Limits::default()
        }
    }
}

/// The validated `hello` result of a connection (WIRE.md §4.4).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HelloResult {
    /// The negotiated protocol minor (the server's prefix minor).
    pub minor: u16,
    /// The level the server confirmed (always the one the client asked for).
    pub level: Level,
    /// The server's name.
    pub server: String,
    /// The probe's boot identity (UUID text).
    pub boot_id: String,
    /// The probe's epoch (WIRE.md §6.4).
    pub epoch: u64,
    /// The optional features the probe offers.
    pub features: Vec<String>,
    /// The verbs the probe serves at this level.
    pub verbs: Vec<String>,
    /// The announced bounds.
    pub limits: Limits,
}

impl HelloResult {
    /// The stand-in held before the hello reply arrives.
    pub(crate) fn pending(level: Level) -> HelloResult {
        HelloResult {
            minor: 0,
            level,
            server: String::new(),
            boot_id: String::new(),
            epoch: 0,
            features: Vec::new(),
            verbs: Vec::new(),
            limits: Limits::pre_hello(),
        }
    }
}

/// The limits object of a hello result: all 19 keys, each an exact `u32`.
fn parse_limits(object: &Object<'_>) -> Result<Limits, ClientError> {
    let get = |key: &str| object.u32(key).ok_or(MISMATCH);
    Ok(Limits {
        max_frame_bytes: get("max_frame_bytes")?,
        max_request_bytes: get("max_request_bytes")?,
        request_deadline_ms: get("request_deadline_ms")?,
        hello_deadline_ms: get("hello_deadline_ms")?,
        idle_ms: get("idle_ms")?,
        stall_ms: get("stall_ms")?,
        frame_completion_ms: get("frame_completion_ms")?,
        max_outstanding: get("max_outstanding")?,
        max_subscriptions: get("max_subscriptions")?,
        client_queue_bytes: get("client_queue_bytes")?,
        query_queue_bytes: get("query_queue_bytes")?,
        control_queue_bytes: get("control_queue_bytes")?,
        max_reply_bytes: get("max_reply_bytes")?,
        max_block_bytes: get("max_block_bytes")?,
        max_rows: get("max_rows")?,
        max_block_buckets: get("max_block_buckets")?,
        max_page_blocks: get("max_page_blocks")?,
        min_interval_ms: get("min_interval_ms")?,
        tick_ms: get("tick_ms")?,
    })
}

/// Validate the `result` object of the hello reply.
///
/// `minor` is the server prefix's minor (the negotiated one), `level` the level the client
/// asked for and `hello_len` the length of the client's own hello frame.
///
/// # Errors
///
/// `Protocol(HelloMismatch)` when `proto` disagrees with the prefix, the `level` is another
/// one, a field is missing or of the wrong type or range (`epoch` an exact `u64`, every limit
/// an exact `u32`), `max_request_bytes` cannot carry the client's own hello, or
/// `max_frame_bytes` is below a frame header. Unknown extra keys are ignored (a later minor
/// adds reply keys, WIRE.md §9.1).
pub(crate) fn parse_hello(
    result_json: &str,
    minor: u16,
    level: Level,
    hello_len: u32,
) -> Result<HelloResult, ClientError> {
    let object = Object::parse(result_json).map_err(|_| MISMATCH)?;
    let proto = object.object("proto").ok_or(MISMATCH)?;
    let major_ok = proto.u64("major") == Some(u64::from(PROTO_MAJOR));
    let minor_ok = proto.u64("minor") == Some(u64::from(minor));
    let named = object.string("level").and_then(|n| Level::from_name(&n));
    if !major_ok || !minor_ok || named != Some(level) {
        return Err(MISMATCH);
    }
    if object.kind("quality_codes") != Some(Kind::Array) {
        return Err(MISMATCH);
    }
    let limits_object = object.object("limits").ok_or(MISMATCH)?;
    let limits = parse_limits(&limits_object)?;
    let header = u32::try_from(FRAME_HEADER_LEN).unwrap_or(u32::MAX);
    if limits.max_request_bytes < hello_len || limits.max_frame_bytes < header {
        return Err(MISMATCH);
    }
    Ok(HelloResult {
        minor,
        level,
        server: object.string("server").ok_or(MISMATCH)?,
        boot_id: object.string("boot_id").ok_or(MISMATCH)?,
        epoch: object.u64("epoch").ok_or(MISMATCH)?,
        features: object.strings("features").ok_or(MISMATCH)?,
        verbs: object.strings("verbs").ok_or(MISMATCH)?,
        limits,
    })
}

#[cfg(test)]
mod tests {
    use super::{parse_hello, HelloResult, Limits, PRE_HELLO_MAX_REQUEST};
    use crate::error::{ClientError, ProtocolKind};
    use crate::level::Level;

    const KEYS: [&str; 19] = [
        "max_frame_bytes",
        "max_request_bytes",
        "request_deadline_ms",
        "hello_deadline_ms",
        "idle_ms",
        "stall_ms",
        "frame_completion_ms",
        "max_outstanding",
        "max_subscriptions",
        "client_queue_bytes",
        "query_queue_bytes",
        "control_queue_bytes",
        "max_reply_bytes",
        "max_block_bytes",
        "max_rows",
        "max_block_buckets",
        "max_page_blocks",
        "min_interval_ms",
        "tick_ms",
    ];

    /// A hello result whose limits are all 1000 except the ones `limits` overrides.
    fn result(level: &str, limits: &[(&str, u32)], tail: &str) -> String {
        let mut text = String::from("{\"proto\":{\"major\":1,\"minor\":1},\"level\":\"");
        text.push_str(level);
        text.push_str("\",\"server\":\"hostobsd\",\"boot_id\":\"b\",\"epoch\":7");
        text.push_str(",\"features\":[\"peak_reset\"],\"verbs\":[\"hello\"]");
        text.push_str(",\"quality_codes\":[],\"limits\":{");
        let mut first = true;
        for key in KEYS {
            let found = limits.iter().find(|(name, _)| *name == key);
            let value = found.map_or(1000, |(_, value)| *value);
            if !first {
                text.push(',');
            }
            first = false;
            text.push_str(&format!("\"{key}\":{value}"));
        }
        text.push('}');
        text.push_str(tail);
        text.push('}');
        text
    }

    fn mismatch() -> Result<HelloResult, ClientError> {
        Err(ClientError::Protocol(ProtocolKind::HelloMismatch))
    }

    #[test]
    fn a_complete_hello_parses_and_ignores_unknown_keys() {
        let text = result("observe", &[], ",\"future\":[1,2]");
        let hello = parse_hello(&text, 1, Level::Observe, 100).expect("hello");
        assert_eq!(hello.minor, 1);
        assert_eq!(hello.epoch, 7);
        assert_eq!(hello.boot_id, "b");
        assert_eq!(hello.features, ["peak_reset"]);
        assert_eq!(hello.verbs, ["hello"]);
        assert_eq!(hello.limits.tick_ms, 1000);
        assert_eq!(hello.limits.max_frame_bytes, 1000);
    }

    /// `count` extra keys `,"x0":0,"x1":0,...`, each preceded by a comma.
    fn extra_keys(count: usize) -> String {
        (0..count).map(|i| format!(",\"x{i}\":0")).collect()
    }

    /// `text` with `count` extra keys appended to its `limits` object.
    fn widen_limits(text: &str, count: usize) -> String {
        let extra = format!("\"tick_ms\":1000{}", extra_keys(count));
        text.replace("\"tick_ms\":1000", &extra)
    }

    fn check(text: &str) -> Result<HelloResult, ClientError> {
        parse_hello(text, 1, Level::Observe, 100)
    }

    #[test]
    fn a_hello_has_nine_keys_and_up_to_twenty_five_limits_but_never_sixty_five() {
        let ok = result("observe", &[], "");
        assert!(check(&widen_limits(&ok, 6)).is_ok());
        assert!(check(&result("observe", &[], &extra_keys(55))).is_ok());
        assert_eq!(check(&result("observe", &[], &extra_keys(56))), mismatch());
        assert_eq!(check(&widen_limits(&ok, 60)), mismatch());
    }

    #[test]
    fn every_defect_is_a_hello_mismatch() {
        let ok = result("observe", &[], "");
        assert_eq!(parse_hello(&ok, 0, Level::Observe, 100), mismatch());
        assert_eq!(parse_hello(&ok, 1, Level::Control, 100), mismatch());
        assert_eq!(parse_hello("[]", 1, Level::Observe, 100), mismatch());
        let bad_level = result("root", &[], "");
        assert_eq!(parse_hello(&bad_level, 1, Level::Observe, 100), mismatch());
        let small = result("observe", &[("max_request_bytes", 99)], "");
        assert_eq!(parse_hello(&small, 1, Level::Observe, 100), mismatch());
        let tiny = result("observe", &[("max_frame_bytes", 11)], "");
        assert_eq!(parse_hello(&tiny, 1, Level::Observe, 100), mismatch());
        let exact = result("observe", &[("max_frame_bytes", 12)], "");
        assert!(parse_hello(&exact, 1, Level::Observe, 100).is_ok());
        let proto = ok.replace("\"major\":1", "\"major\":2");
        assert_eq!(parse_hello(&proto, 1, Level::Observe, 100), mismatch());
    }

    #[test]
    fn missing_or_mistyped_fields_are_refused() {
        let ok = result("observe", &[], "");
        let replacements = [
            ("\"epoch\":7", "\"epoch\":-1"),
            ("\"epoch\":7", "\"epoch\":7.0"),
            ("\"epoch\":7", "\"epoch\":\"7\""),
            ("\"epoch\":7", "\"epoc\":7"),
            ("\"boot_id\":\"b\"", "\"boot_id\":3"),
            ("\"server\":\"hostobsd\"", "\"server\":null"),
            ("\"features\":[\"peak_reset\"]", "\"features\":[1]"),
            ("\"verbs\":[\"hello\"]", "\"verbs\":{}"),
            ("\"quality_codes\":[]", "\"quality_codes\":{}"),
            ("\"tick_ms\":1000", "\"tick_ms\":4294967296"),
            ("\"tick_ms\":1000", "\"tick_ms\":1.5"),
            ("\"idle_ms\":1000", "\"idle_ms\":null"),
            ("\"limits\":{", "\"limits\":[],\"x\":{"),
            ("\"proto\":{\"major\":1,\"minor\":1}", "\"proto\":1"),
        ];
        for (from, to) in replacements {
            assert!(ok.contains(from), "{from}");
            let broken = ok.replace(from, to);
            assert_eq!(parse_hello(&broken, 1, Level::Observe, 100), mismatch());
        }
    }

    #[test]
    fn pending_and_pre_hello_values_are_the_documented_defaults() {
        let pending = HelloResult::pending(Level::Operator);
        assert_eq!(pending.level, Level::Operator);
        assert_eq!(pending.limits, Limits::pre_hello());
        assert_eq!(pending.limits.max_request_bytes, PRE_HELLO_MAX_REQUEST);
    }
}
