//! The two sockets: framing and role, no policy (design §3, D7).
//!
//! `Accept=no`: one long-lived daemon owns each listener, so a restart does not
//! refuse a client that connects during it. The wire is newline-delimited JSON,
//! one request one reply ([`crate::wire`]); this module owns only the bytes
//! around it — a bounded read, one reply line, and the *role* of the socket.
//!
//! ## Role is a socket's fact, not a client's claim
//!
//! The registrant socket serves the four registrant verbs and the reader socket
//! the four reader verbs. A reader verb arriving on the registrant socket is
//! refused (`reason: wrong-socket`) rather than served, because serving it would
//! hand a registrant the right to read what it may park. Group gating controls
//! socket access but is advisory (container root bypasses socket DAC, design §3).
//! Scope resolution bounds the target cgroup, not ownership of a registration id;
//! P1 does not enforce that issuer relationship (TG-12).
//!
//! ## Why nothing here binds a path in tests
//!
//! A test that binds a real socket leaves a file behind on a host that is
//! already serving something, and the design's rule is that every test takes an
//! injected root. So the tests drive the framing over
//! [`std::os::unix::net::UnixStream::pair`] — a real socket fd, no path, nothing
//! to clean up. In a foreground run, `main` binds the explicit CLI socket paths
//! or the defaults in this module; under systemd it adopts the configured
//! listeners instead. Socket paths are not configuration keys.

use std::io::{BufRead, BufReader, Read, Write};

use crate::json::Value;
use crate::wire::{self, Request, WireError};
use crate::{EVENT_VERSION, REGISTRATION_VERSION, STATE_VERSION};

/// The default registrant socket path (production; systemd/tmpfiles own the dir).
pub const REGISTRANT_PATH: &str = "/run/thrashguard/registrant/thrashguard.sock";
/// The default reader socket path.
pub const READER_PATH: &str = "/run/thrashguard/reader/thrashguard.sock";

/// Which of the two sockets a session is on.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    /// `register|update|heartbeat|deregister`.
    Registrant,
    /// `status|candidates|events|version`.
    Reader,
}

impl Role {
    /// The spelling the journal and a refusal use.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Registrant => "registrant",
            Self::Reader => "reader",
        }
    }

    /// The version tag a refusal on this socket carries.
    pub fn version(self) -> &'static str {
        match self {
            Self::Registrant => REGISTRATION_VERSION,
            Self::Reader => STATE_VERSION,
        }
    }

    /// Whether this socket serves `request`'s verb.
    pub fn serves(self, request: &Request) -> bool {
        match self {
            Self::Registrant => request.is_registrant(),
            Self::Reader => !request.is_registrant(),
        }
    }

    /// The default path of this socket.
    pub fn default_path(self) -> &'static str {
        match self {
            Self::Registrant => REGISTRANT_PATH,
            Self::Reader => READER_PATH,
        }
    }
}

/// One client's conversation: a bounded line reader and a reply writer.
pub struct Session<R, W> {
    reader: BufReader<R>,
    writer: W,
    role: Role,
}

impl<R: std::io::Read, W: Write> Session<R, W> {
    /// A session over a connected pair.
    pub fn new(reader: R, writer: W, role: Role) -> Self {
        Self {
            reader: BufReader::new(reader),
            writer,
            role,
        }
    }

    /// Read one request line. `Ok(None)` means the peer closed cleanly.
    pub fn read_request(&mut self) -> std::io::Result<Option<String>> {
        let mut bytes = Vec::new();
        let mut bounded = self
            .reader
            .by_ref()
            .take((crate::wire::MAX_LINE_BYTES + 1) as u64);
        let read = bounded.read_until(b'\n', &mut bytes)?;
        if read == 0 {
            return Ok(None);
        }
        // Lossy decoding preserves the byte bound: if the cut lands inside a
        // multibyte character, its replacement makes the request over limit so
        // the normal parser returns the named `too-large` refusal.
        Ok(Some(String::from_utf8_lossy(&bytes).into_owned()))
    }

    /// Write one reply line, newline-terminated.
    pub fn write_reply(&mut self, value: &Value) -> std::io::Result<()> {
        let mut line = value.to_json();
        line.push('\n');
        self.writer.write_all(line.as_bytes())?;
        self.writer.flush()
    }

    /// The socket's role.
    pub fn role(&self) -> Role {
        self.role
    }

    /// The client's half of the contract: write one request line, read one reply
    /// line, and hand it back parsed.
    ///
    /// A reply that is not JSON is an I/O-level failure here, not an empty value:
    /// a client must never read a garbled answer as "nothing happened".
    pub fn request(&mut self, line: &str) -> std::io::Result<Value> {
        let mut out = line.trim_end().to_string();
        out.push('\n');
        self.writer.write_all(out.as_bytes())?;
        self.writer.flush()?;
        let mut reply = String::new();
        self.reader.read_line(&mut reply)?;
        if reply.trim().is_empty() {
            return Err(std::io::Error::other(
                "the daemon closed the connection without a reply",
            ));
        }
        Value::parse(reply.trim()).map_err(|error| std::io::Error::other(error.to_string()))
    }
}

/// Turn one line into the reply value, given the daemon and the socket's role.
///
/// Every refusal keeps `ok: false` and names its reason, so a client always
/// learns *why* rather than only that the line was not accepted.
pub fn answer<G: Dispatch>(guard: &mut G, role: Role, line: &str) -> Value {
    let request = match wire::parse_request(line) {
        Ok(request) => request,
        Err(error) => return refuse(role, &error),
    };
    if !role.serves(&request) {
        return wire::refused_reader(
            role.version(),
            "wrong-socket",
            &format!(
                "the {} verb belongs on the {} socket",
                request.verb_name(),
                if request.is_registrant() {
                    Role::Registrant
                } else {
                    Role::Reader
                }
                .as_str()
            ),
        );
    }
    guard.respond(role, request)
}

/// The reason word for a wire refusal, and the version tag its reply carries.
fn refuse(role: Role, error: &WireError) -> Value {
    let reason = match error {
        WireError::UnknownKey(_) => "unknown-key",
        WireError::UnknownVerb(_) => "unknown-verb",
        WireError::Version { .. } => "version",
        WireError::TooLarge { .. } | WireError::FieldTooLarge { .. } => "too-large",
        WireError::Value { .. } => "value",
        WireError::Empty
        | WireError::Json(_)
        | WireError::MemberKind { .. }
        | WireError::MissingMember { .. } => "malformed",
    };
    // `events` is the one reader verb with a version of its own, so a refusal
    // about it names that version rather than the state version.
    let version = match error {
        WireError::Version { expected, .. } => Some(*expected),
        _ if role == Role::Reader && error.to_string().contains("events") => Some(EVENT_VERSION),
        _ => None,
    };
    wire::refused_reader(
        version.unwrap_or_else(|| role.version()),
        reason,
        &error.to_string(),
    )
}

/// A daemon that answers requests: the one method a socket session needs.
pub trait Dispatch {
    /// Answer one well-formed request.
    fn respond(&mut self, role: Role, request: Request) -> Value;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::os::unix::net::UnixStream;

    /// A dispatcher that records what it was asked, so framing and role are
    /// testable without a daemon (no clock, no cgroup root, no journal).
    #[derive(Default, Debug)]
    struct Recorder {
        seen: Vec<Request>,
    }

    impl Dispatch for Recorder {
        fn respond(&mut self, _role: Role, request: Request) -> Value {
            self.seen.push(request.clone());
            match request {
                Request::Version => wire::version(),
                Request::Register(ref body) => wire::register_ok(&body.id, "docker-1.scope", 1, 90),
                Request::Heartbeat { ref id } | Request::Deregister { ref id } => wire::ok_id(id),
                _ => wire::ok(STATE_VERSION, Vec::new()),
            }
        }
    }

    #[test]
    fn a_reply_is_one_newline_terminated_line_over_a_real_socketpair() {
        // A read that never returns is a defect of this test, not a slow host, so
        // every socket read here is bounded: the timeout exists to turn a hang
        // into a failure with a name, and never to race a slow machine.
        let (client, server) = UnixStream::pair().expect("socketpair");
        client
            .set_read_timeout(Some(std::time::Duration::from_secs(60)))
            .expect("a readable timeout");
        let mut client_reader = client.try_clone().expect("clone");
        let mut session = Session::new(client, server, Role::Reader);
        let reply = wire::version();
        let expected = reply.to_json();
        session.write_reply(&reply).expect("write the reply");
        let mut text = String::new();
        BufReader::new(&mut client_reader)
            .read_line(&mut text)
            .expect("read the reply back");
        assert_eq!(
            text,
            format!("{expected}\n"),
            "exactly one line, newline-terminated"
        );
        assert_eq!(session.role(), Role::Reader);
    }

    #[test]
    fn a_request_line_arrives_whole_and_a_closed_peer_is_a_clean_end() {
        let (mut writer, reader) = UnixStream::pair().expect("socketpair");
        reader
            .set_read_timeout(Some(std::time::Duration::from_secs(60)))
            .expect("a readable timeout");
        // The session's write half is a buffer: the only open endpoint of the pair
        // is `writer`, so closing it is a real EOF rather than a clone kept alive.
        let mut session = Session::new(reader, Vec::<u8>::new(), Role::Registrant);
        let line =
            format!("{{\"v\":\"{REGISTRATION_VERSION}\",\"verb\":\"heartbeat\",\"id\":\"a\"}}\n");
        writer
            .write_all(line.as_bytes())
            .expect("write the request");
        let received = session.read_request().expect("read").expect("one line");
        assert_eq!(
            received, line,
            "one request line, whole, including its newline"
        );
        assert_eq!(session.role(), Role::Registrant);
        drop(writer);
        assert_eq!(
            session.read_request().expect("a closed peer"),
            None,
            "a closed peer is a clean end"
        );
    }

    #[test]
    fn an_oversized_request_is_stopped_at_the_wire_bound() {
        let (mut writer, reader) = UnixStream::pair().expect("socketpair");
        let mut session = Session::new(reader, Vec::<u8>::new(), Role::Reader);
        let oversized = format!("{}\n", "x".repeat(crate::wire::MAX_LINE_BYTES + 8));
        writer
            .write_all(oversized.as_bytes())
            .expect("write oversized request");

        let line = session
            .read_request()
            .expect("bounded read")
            .expect("request line");
        assert!(
            line.len() <= crate::wire::MAX_LINE_BYTES + 1,
            "read more than the protocol bound: {} bytes",
            line.len()
        );
    }

    #[test]
    fn an_oversized_line_cut_inside_utf8_gets_the_too_large_reply() {
        let fixed = format!("{{\"v\":\"{STATE_VERSION}\",\"verb\":\"status\",\"x\":\"");
        let padding = "x".repeat(crate::wire::MAX_LINE_BYTES - fixed.len());
        let line = format!("{fixed}{padding}é\"}}\n");
        let mut session = Session::new(
            std::io::Cursor::new(line.into_bytes()),
            Vec::<u8>::new(),
            Role::Reader,
        );
        let request = session
            .read_request()
            .expect("bounded byte read")
            .expect("oversized request");
        let reply = answer(&mut Recorder::default(), Role::Reader, &request);
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(false));
        assert_eq!(
            reply.get("reason").and_then(Value::as_str),
            Some("too-large")
        );
    }

    #[test]
    fn a_reader_verb_on_the_registrant_socket_is_refused_not_served() {
        let mut recorder = Recorder::default();
        let line = format!("{{\"v\":\"{STATE_VERSION}\",\"verb\":\"status\"}}");
        let reply = answer(&mut recorder, Role::Registrant, &line);
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(false));
        assert_eq!(
            reply.get("reason").and_then(Value::as_str),
            Some("wrong-socket")
        );
        assert_eq!(
            reply.get("v").and_then(Value::as_str),
            Some(REGISTRATION_VERSION)
        );
        assert!(
            recorder.seen.is_empty(),
            "the dispatcher never sees a foreign verb"
        );

        let line =
            format!("{{\"v\":\"{REGISTRATION_VERSION}\",\"verb\":\"heartbeat\",\"id\":\"a\"}}");
        let reply = answer(&mut recorder, Role::Reader, &line);
        assert_eq!(
            reply.get("reason").and_then(Value::as_str),
            Some("wrong-socket")
        );
        assert!(recorder.seen.is_empty());
    }

    #[test]
    fn the_registrant_socket_serves_the_four_verbs() {
        for (verb, body) in [
            ("register", "\"id\":\"rg-1\",\"container\":\"abc\""),
            ("update", "\"id\":\"rg-1\",\"score\":3"),
            ("heartbeat", "\"id\":\"rg-1\""),
            ("deregister", "\"id\":\"rg-1\""),
        ] {
            let mut recorder = Recorder::default();
            let line = format!("{{\"v\":\"{REGISTRATION_VERSION}\",\"verb\":\"{verb}\",{body}}}");
            let reply = answer(&mut recorder, Role::Registrant, &line);
            assert_eq!(
                reply.get("ok").and_then(Value::as_bool),
                Some(true),
                "{verb}: {reply:?}"
            );
            assert_eq!(recorder.seen.len(), 1, "{verb} reached the dispatcher");
        }
        let mut recorder = Recorder::default();
        let line = format!("{{\"v\":\"{REGISTRATION_VERSION}\",\"verb\":\"register\",\"id\":\"a\",\"container\":\"c\"}}");
        let reply = answer(&mut recorder, Role::Registrant, &line);
        assert!(recorder
            .seen
            .iter()
            .any(|seen| matches!(seen, Request::Register(body) if body.id == "a")));
        assert_eq!(
            reply.get("cgroup").and_then(Value::as_str),
            Some("docker-1.scope")
        );
        assert_eq!(reply.get("ttl_s").and_then(Value::as_i64), Some(90));
    }

    #[test]
    fn the_reader_socket_serves_the_four_reader_verbs() {
        for (version, verb, extra) in [
            (STATE_VERSION, "status", ""),
            (STATE_VERSION, "candidates", ""),
            (STATE_VERSION, "version", ""),
            (EVENT_VERSION, "events", ",\"since_seq\":3"),
        ] {
            let mut recorder = Recorder::default();
            let line = format!("{{\"v\":\"{version}\",\"verb\":\"{verb}\"{extra}}}");
            let reply = answer(&mut recorder, Role::Reader, &line);
            assert_eq!(
                reply.get("ok").and_then(Value::as_bool),
                Some(true),
                "{verb}: {reply:?}"
            );
            assert_eq!(recorder.seen.len(), 1);
        }
    }

    #[test]
    fn a_malformed_line_is_refused_with_a_named_reason_and_the_sockets_version() {
        let mut recorder = Recorder::default();
        let reply = answer(&mut recorder, Role::Reader, "not json");
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(false));
        assert_eq!(
            reply.get("reason").and_then(Value::as_str),
            Some("malformed")
        );
        assert_eq!(reply.get("v").and_then(Value::as_str), Some(STATE_VERSION));
        assert!(recorder.seen.is_empty());

        let line = format!(
            "{{\"v\":\"{REGISTRATION_VERSION}\",\"verb\":\"register\",\"id\":\"a\",\"container\":\"c\",\"ttl_s\":90}}"
        );
        let reply = answer(&mut recorder, Role::Registrant, &line);
        assert_eq!(
            reply.get("reason").and_then(Value::as_str),
            Some("unknown-key")
        );
        assert!(reply
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .contains("ttl_s"));

        let reply = answer(
            &mut recorder,
            Role::Registrant,
            "{\"v\":\"thrashguard-registration/1\",\"verb\":\"freeze\"}",
        );
        assert_eq!(
            reply.get("reason").and_then(Value::as_str),
            Some("unknown-verb")
        );

        // An `events` request with the wrong version is refused naming the
        // version `events` speaks, not the reader's own.
        let reply = answer(
            &mut recorder,
            Role::Reader,
            &format!("{{\"v\":\"{STATE_VERSION}\",\"verb\":\"events\"}}"),
        );
        assert_eq!(reply.get("reason").and_then(Value::as_str), Some("version"));
        assert_eq!(reply.get("v").and_then(Value::as_str), Some(EVENT_VERSION));
    }

    #[test]
    fn an_over_limit_registration_field_gets_the_named_too_large_reason() {
        let mut recorder = Recorder::default();
        let id = "i".repeat(crate::wire::MAX_ID_BYTES + 1);
        let line =
            format!("{{\"v\":\"{REGISTRATION_VERSION}\",\"verb\":\"heartbeat\",\"id\":\"{id}\"}}");
        let reply = answer(&mut recorder, Role::Registrant, &line);
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(false));
        assert_eq!(
            reply.get("reason").and_then(Value::as_str),
            Some("too-large")
        );
        assert!(
            recorder.seen.is_empty(),
            "oversized fields never reach dispatch"
        );
    }
}
