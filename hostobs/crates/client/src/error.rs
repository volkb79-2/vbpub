//! The client's closed error type (WIRE.md §5.2, §10, §12 `P5a`).
//!
//! Every failure of a [`Client`](crate::Client) is one [`ClientError`]. The variants say who is
//! at fault: the local caller (`Usage`), the operating system (`Io`), the server's bytes
//! (`Wire`, `Envelope`, `Protocol`), the server's refusal of one request (`Server`) or the
//! clock (`Timeout`). [`ClientError::kind`] names each one with a stable string.

use core::fmt;
use std::io;

use hostobs_proto::envelope::EnvelopeError;
use hostobs_proto::error::ProtoError;

/// The connection-fatal codes of WIRE.md §5.4: after one of them the server closes the
/// connection, so the client closes its side too.
pub const FATAL_CODES: [&str; 9] = [
    "unknown-mode",
    "unsupported-version",
    "hello-required",
    "bad-frame",
    "frame-too-large",
    "deadline",
    "idle",
    "slow-client",
    "shutting-down",
];

/// The closed list of wire error codes (WIRE.md §10).
pub const ERROR_CODES: [&str; 23] = [
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
    "cursor-invalid",
    "span-unknown",
    "unsupported-feature",
    "session-lost",
    "window-lost",
    "internal",
];

/// Whether `code` is one of the connection-fatal codes of WIRE.md §5.4.
#[must_use]
pub fn is_fatal_code(code: &str) -> bool {
    FATAL_CODES.contains(&code)
}

/// A rule of the protocol the server broke that is not a decoder error (the server is the
/// faulty peer, so the client closes the connection).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProtocolKind {
    /// A frame of a type the client never receives (a REQ or a DICT).
    UnexpectedFrame,
    /// A REP, BLOCK or END whose `corr` names no request or stream of this client.
    UnknownCorr,
    /// A reply whose content contradicts the request or the envelope rules.
    BadReply,
    /// An END whose counters disagree with the BLOCK frames received.
    EndCounts,
    /// A hello that disagrees with the prefix, the requested level or the limits rules.
    HelloMismatch,
    /// Every `corr` of the connection has been used.
    CorrExhausted,
}

impl ProtocolKind {
    /// The stable name of this refusal (the names of the Python client's `ProtocolError`).
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            ProtocolKind::UnexpectedFrame => "unexpected-frame",
            ProtocolKind::UnknownCorr => "unknown-corr",
            ProtocolKind::BadReply => "bad-reply",
            ProtocolKind::EndCounts => "end-counts",
            ProtocolKind::HelloMismatch => "hello-mismatch",
            ProtocolKind::CorrExhausted => "corr-exhausted",
        }
    }
}

/// Every way a client call fails.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ClientError {
    /// An operating-system error other than a lost connection (connect, read, write).
    Io(io::ErrorKind),
    /// The server's bytes are not a valid frame or block (the connection is closed).
    Wire(ProtoError),
    /// A REP, END or EVT payload broke the envelope rules (the connection is closed).
    Envelope(EnvelopeError),
    /// The server answered a request with an error reply or an `END ok:false`.
    Server {
        /// The §10 code (any text: the client does not trust the server to stay in the list).
        code: String,
        /// The human-readable message.
        message: String,
        /// The `detail` object as JSON text.
        detail_json: String,
    },
    /// A wait ran past its deadline (the connection is closed).
    Timeout,
    /// The connection is closed (EOF, reset, `close()`, or an earlier error).
    ConnectionLost,
    /// The server broke a protocol rule (the connection is closed).
    Protocol(ProtocolKind),
    /// The caller passed something the client refuses to send; the connection is untouched.
    Usage(&'static str),
}

impl ClientError {
    /// The stable machine name of this error: the wire kind of a decoder or envelope error,
    /// the §10 code of a server error, the protocol or usage name otherwise.
    #[must_use]
    pub fn kind(&self) -> &str {
        match self {
            ClientError::Io(_) => "io",
            ClientError::Wire(error) => error.kind(),
            ClientError::Envelope(error) => error.kind(),
            ClientError::Server { code, .. } => code,
            ClientError::Timeout => "timeout",
            ClientError::ConnectionLost => "connection-lost",
            ClientError::Protocol(kind) => kind.name(),
            ClientError::Usage(name) => name,
        }
    }
}

impl fmt::Display for ClientError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ClientError::Io(kind) => write!(f, "i/o error: {kind}"),
            ClientError::Wire(error) => write!(f, "wire error: {error}"),
            ClientError::Envelope(error) => write!(f, "envelope error: {}", error.kind()),
            ClientError::Server { code, message, .. } => {
                write!(f, "server error {code}: {message}")
            }
            ClientError::Timeout => f.write_str("the server did not answer in time"),
            ClientError::ConnectionLost => f.write_str("the connection is closed"),
            ClientError::Protocol(kind) => write!(f, "protocol violation: {}", kind.name()),
            ClientError::Usage(name) => write!(f, "usage error: {name}"),
        }
    }
}

impl core::error::Error for ClientError {}

impl From<ProtoError> for ClientError {
    fn from(error: ProtoError) -> ClientError {
        ClientError::Wire(error)
    }
}

impl From<EnvelopeError> for ClientError {
    fn from(error: EnvelopeError) -> ClientError {
        ClientError::Envelope(error)
    }
}

impl From<ProtocolKind> for ClientError {
    fn from(kind: ProtocolKind) -> ClientError {
        ClientError::Protocol(kind)
    }
}

#[cfg(test)]
mod tests {
    use super::{is_fatal_code, ClientError, ProtocolKind, ERROR_CODES, FATAL_CODES};
    use hostobs_proto::envelope::EnvelopeError;
    use hostobs_proto::error::ProtoError;
    use std::io;

    #[test]
    fn every_variant_has_a_kind_and_a_display() {
        let cases = [
            (ClientError::Io(io::ErrorKind::NotFound), "io"),
            (ClientError::Wire(ProtoError::Deadline), "deadline"),
            (ClientError::Envelope(EnvelopeError::Json), "json-text"),
            (
                ClientError::Server {
                    code: "budget".to_owned(),
                    message: "no".to_owned(),
                    detail_json: "{}".to_owned(),
                },
                "budget",
            ),
            (ClientError::Timeout, "timeout"),
            (ClientError::ConnectionLost, "connection-lost"),
            (ClientError::Protocol(ProtocolKind::BadReply), "bad-reply"),
            (ClientError::Usage("bad-verb"), "bad-verb"),
        ];
        for (error, kind) in cases {
            assert_eq!(error.kind(), kind);
            assert!(!error.to_string().is_empty());
        }
    }

    #[test]
    fn protocol_kind_names_are_distinct() {
        let all = [
            ProtocolKind::UnexpectedFrame,
            ProtocolKind::UnknownCorr,
            ProtocolKind::BadReply,
            ProtocolKind::EndCounts,
            ProtocolKind::HelloMismatch,
            ProtocolKind::CorrExhausted,
        ];
        let mut names: Vec<&str> = all.iter().map(|kind| kind.name()).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), all.len());
    }

    #[test]
    fn fatal_codes_are_a_subset_of_the_closed_list() {
        for code in FATAL_CODES {
            assert!(ERROR_CODES.contains(&code));
            assert!(is_fatal_code(code));
        }
        assert!(!is_fatal_code("budget"));
        assert!(!is_fatal_code(""));
    }
}
