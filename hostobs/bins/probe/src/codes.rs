//! The closed list of wire error codes (WIRE.md section 10) and the `detail` object of the error
//! envelope (WIRE.md section 4.1).
//!
//! Every code has one fixed `message` (a `&'static str`: client text is never echoed, design
//! R14) and one fatality (WIRE.md section 5.4: the nine connection-fatal codes close the
//! connection, every other code answers one request and keeps it).

/// One wire error code.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum Code {
    /// The first byte is neither `{` nor a prefix byte (fatal).
    UnknownMode,
    /// The client's major version is not served (fatal).
    UnsupportedVersion,
    /// The first request was not `hello` (fatal).
    HelloRequired,
    /// A frame header or a frame sequence is invalid (fatal).
    BadFrame,
    /// A request frame or line is over `max_request_bytes` (fatal).
    FrameTooLarge,
    /// A deadline ran out (fatal).
    Deadline,
    /// The connection was idle too long (fatal).
    Idle,
    /// The client reads too slowly (fatal).
    SlowClient,
    /// The server is shutting down (fatal).
    ShuttingDown,
    /// The request is malformed.
    BadRequest,
    /// The verb is not served here.
    UnknownVerb,
    /// The verb exists but not at this level.
    Forbidden,
    /// A block verb was asked in JSON mode.
    BinaryRequired,
    /// Too many outstanding requests.
    TooManyRequests,
    /// A budget pool is exhausted.
    Budget,
    /// The issuer is rate limited.
    RateLimited,
    /// The named object does not exist.
    NotFound,
    /// The retained history has no committed bucket at this position.
    DataGap,
    /// The cursor is not valid for this request.
    CursorInvalid,
    /// The span is not open.
    SpanUnknown,
    /// The negotiated minor lacks the feature.
    UnsupportedFeature,
    /// The session was lost with the epoch.
    SessionLost,
    /// The window was lost with the epoch.
    WindowLost,
    /// The server failed internally.
    Internal,
}

/// The budget pool named by a `budget` error's `detail` (WIRE.md section 10).
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum Pool {
    /// `max_subscriptions` per connection.
    Subscriptions,
    /// The peer uid's distinct selections.
    UidSelections,
    /// The probe's distinct live selections.
    Selections,
    /// Rows resolved for one selection.
    Rows,
    /// History spans, reserved for P5b-3.
    Spans,
    /// The 256-entry query cursor table.
    Cursors,
    /// Published process scans and projections.
    Procs,
    /// Provenance cells in one snapshot response.
    Provenance,
    /// One encoded query BLOCK.
    Block,
}

impl Pool {
    /// The wire spelling of the pool.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Pool::Subscriptions => "subscriptions",
            Pool::UidSelections => "uid-selections",
            Pool::Selections => "selections",
            Pool::Rows => "rows",
            Pool::Spans => "spans",
            Pool::Cursors => "cursors",
            Pool::Procs => "procs",
            Pool::Provenance => "provenance",
            Pool::Block => "block",
        }
    }
}

impl Code {
    /// Every code, in the order of WIRE.md section 10.
    pub const ALL: [Code; 24] = [
        Code::UnknownMode,
        Code::UnsupportedVersion,
        Code::HelloRequired,
        Code::BadFrame,
        Code::FrameTooLarge,
        Code::Deadline,
        Code::Idle,
        Code::SlowClient,
        Code::ShuttingDown,
        Code::BadRequest,
        Code::UnknownVerb,
        Code::Forbidden,
        Code::BinaryRequired,
        Code::TooManyRequests,
        Code::Budget,
        Code::RateLimited,
        Code::NotFound,
        Code::DataGap,
        Code::CursorInvalid,
        Code::SpanUnknown,
        Code::UnsupportedFeature,
        Code::SessionLost,
        Code::WindowLost,
        Code::Internal,
    ];

    /// The wire spelling of the code.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Code::UnknownMode => "unknown-mode",
            Code::UnsupportedVersion => "unsupported-version",
            Code::HelloRequired => "hello-required",
            Code::BadFrame => "bad-frame",
            Code::FrameTooLarge => "frame-too-large",
            Code::Deadline => "deadline",
            Code::Idle => "idle",
            Code::SlowClient => "slow-client",
            Code::ShuttingDown => "shutting-down",
            Code::BadRequest => "bad-request",
            Code::UnknownVerb => "unknown-verb",
            Code::Forbidden => "forbidden",
            Code::BinaryRequired => "binary-required",
            Code::TooManyRequests => "too-many-requests",
            Code::Budget => "budget",
            Code::RateLimited => "rate-limited",
            Code::NotFound => "not-found",
            Code::DataGap => "data-gap",
            Code::CursorInvalid => "cursor-invalid",
            Code::SpanUnknown => "span-unknown",
            Code::UnsupportedFeature => "unsupported-feature",
            Code::SessionLost => "session-lost",
            Code::WindowLost => "window-lost",
            Code::Internal => "internal",
        }
    }

    /// The code spelled `name`, if it is one of WIRE.md section 10.
    #[must_use]
    pub fn from_name(name: &str) -> Option<Code> {
        let mut codes = Code::ALL.iter().copied();
        codes.find(|code| code.name() == name)
    }

    /// Whether the code closes the connection (WIRE.md section 5.4, the nine fatal codes).
    #[must_use]
    pub const fn fatal(self) -> bool {
        matches!(
            self,
            Code::UnknownMode
                | Code::UnsupportedVersion
                | Code::HelloRequired
                | Code::BadFrame
                | Code::FrameTooLarge
                | Code::Deadline
                | Code::Idle
                | Code::SlowClient
                | Code::ShuttingDown
        )
    }

    /// The fixed human-readable message of the code (never client text, at most 256 bytes).
    #[must_use]
    pub const fn message(self) -> &'static str {
        match self {
            Code::UnknownMode => "the first byte selects no known mode",
            Code::UnsupportedVersion => "the protocol major version is not served",
            Code::HelloRequired => "the first request must be hello",
            Code::BadFrame => "the frame or the frame sequence is invalid",
            Code::FrameTooLarge => "the request is larger than the level allows",
            Code::Deadline => "the deadline ran out",
            Code::Idle => "the connection was idle for too long",
            Code::SlowClient => "the client does not read fast enough",
            Code::ShuttingDown => "the server is shutting down",
            Code::BadRequest => "the request is malformed",
            Code::UnknownVerb => "the verb is not served",
            Code::Forbidden => "the verb is not available at this level",
            Code::BinaryRequired => "the verb needs the binary protocol",
            Code::TooManyRequests => "too many requests are outstanding",
            Code::Budget => "a budget is exhausted",
            Code::RateLimited => "the request rate is limited",
            Code::NotFound => "the object was not found",
            Code::DataGap => "the retained history has no committed bucket at this position",
            Code::CursorInvalid => "the cursor is not valid",
            Code::SpanUnknown => "the span is not open",
            Code::UnsupportedFeature => "the negotiated version lacks the feature",
            Code::SessionLost => "the session was lost",
            Code::WindowLost => "the window was lost",
            Code::Internal => "the server failed to answer",
        }
    }
}

/// The `detail` object of an error envelope (WIRE.md section 4.1): always present, bounded by
/// construction (design R14).
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Detail {
    /// `{}`.
    Empty,
    /// `{"key": <path>}`: the schema path of the offending member; empty for a text defect.
    Key(String),
    /// `{"pool": <name>}`: the exhausted budget pool.
    Pool(Pool),
    /// `{"feature": <str>}`: the negotiated feature is unavailable.
    Feature(&'static str),
}

impl Detail {
    /// The longest key path a detail carries: a schema path of at most a few 64-byte names.
    pub const MAX_KEY_BYTES: usize = 192;

    /// A `Key` detail for `path`; a path over [`Detail::MAX_KEY_BYTES`] collapses to the empty
    /// path, so a detail never grows with client input.
    #[must_use]
    pub fn key(path: &str) -> Detail {
        if path.len() > Detail::MAX_KEY_BYTES {
            Detail::Key(String::new())
        } else {
            Detail::Key(path.to_string())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Code, Detail, Pool};
    use crate::encode::error_payload;
    use hostobs_proto::envelope::{check_rep, members_up_to};

    #[test]
    fn the_list_is_the_closed_set_of_section_10() {
        let names: Vec<&str> = Code::ALL.iter().map(|code| code.name()).collect();
        assert_eq!(
            names,
            [
                "unknown-mode",
                "unsupported-version",
                "hello-required",
                "bad-frame",
                "frame-too-large",
                "deadline",
                "idle",
                "slow-client",
                "shutting-down",
                "bad-request",
                "unknown-verb",
                "forbidden",
                "binary-required",
                "too-many-requests",
                "budget",
                "rate-limited",
                "not-found",
                "data-gap",
                "cursor-invalid",
                "span-unknown",
                "unsupported-feature",
                "session-lost",
                "window-lost",
                "internal",
            ]
        );
        for (index, name) in names.iter().enumerate() {
            assert!(!names[..index].contains(name), "duplicate {name}");
        }
    }

    #[test]
    fn names_round_trip_and_unknown_names_are_none() {
        for code in Code::ALL {
            assert_eq!(Code::from_name(code.name()), Some(code));
        }
        assert_eq!(Code::from_name("busy"), None);
        assert_eq!(Code::from_name(""), None);
    }

    #[test]
    fn exactly_the_nine_section_5_4_codes_are_fatal() {
        let fatal: Vec<&str> = Code::ALL
            .iter()
            .filter(|code| code.fatal())
            .map(|code| code.name())
            .collect();
        assert_eq!(
            fatal,
            [
                "unknown-mode",
                "unsupported-version",
                "hello-required",
                "bad-frame",
                "frame-too-large",
                "deadline",
                "idle",
                "slow-client",
                "shutting-down",
            ]
        );
    }

    #[test]
    fn messages_are_short_distinct_ascii() {
        for code in Code::ALL {
            let message = code.message();
            assert!(!message.is_empty() && message.len() <= 256);
            assert!(message.is_ascii());
            for other in Code::ALL {
                if other != code {
                    assert_ne!(other.message(), message);
                }
            }
        }
    }

    #[test]
    fn a_long_key_path_collapses_to_the_empty_path() {
        let path = "proto.minor";
        assert_eq!(Detail::key(path), Detail::Key(path.to_string()));
        let at_bound = "a".repeat(Detail::MAX_KEY_BYTES);
        assert_eq!(Detail::key(&at_bound), Detail::Key(at_bound.clone()));
        let over = "a".repeat(Detail::MAX_KEY_BYTES + 1);
        assert_eq!(Detail::key(&over), Detail::Key(String::new()));
    }

    #[test]
    fn codes_pool_names_and_detail_pool_encoding() {
        let names = [
            (Pool::Subscriptions, "subscriptions"),
            (Pool::UidSelections, "uid-selections"),
            (Pool::Selections, "selections"),
            (Pool::Rows, "rows"),
            (Pool::Spans, "spans"),
            (Pool::Cursors, "cursors"),
            (Pool::Procs, "procs"),
            (Pool::Provenance, "provenance"),
            (Pool::Block, "block"),
        ];
        for (pool, name) in names {
            assert_eq!(pool.name(), name);
            let payload = error_payload(Code::Budget, &Detail::Pool(pool));
            let bytes = payload.as_bytes();
            let rep = check_rep(bytes, false).expect("error envelope");
            assert!(!rep.ok);
            let body = bytes.get(rep.body).expect("error body");
            let fields = members_up_to(body, 8).expect("error members");
            let detail = fields
                .iter()
                .find(|member| body.get(member.key.clone()) == Some(b"detail".as_slice()))
                .expect("detail member");
            let detail_text = body.get(detail.value.clone()).expect("detail object");
            let detail_fields = members_up_to(detail_text, 8).expect("detail members");
            assert_eq!(detail_fields.len(), 1);
            let only = detail_fields.first().expect("pool member");
            assert_eq!(detail_text.get(only.key.clone()), Some(b"pool".as_slice()));
            let value = detail_text.get(only.value.clone()).expect("pool value");
            assert_eq!(value, format!("\"{name}\"").as_bytes());
        }
        assert!(Detail::Pool(Pool::Spans) != Detail::Empty);
    }
}
