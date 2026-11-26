//! The wire vocabulary: newline-delimited JSON, one request one reply, versioned
//! (design §3, D7).
//!
//! Two closed verb sets, in two socket groups:
//!
//! - **registrant** (`thrashguard-registration/1`): `register`, `update`,
//!   `heartbeat`, `deregister`;
//! - **reader** (`thrashguard-state/1` for `status|candidates|version`,
//!   `thrashguard-event/1` for `events`).
//!
//! Every request carries its own version tag, so a client of the wrong version
//! learns *which* version was expected instead of the request failing for an
//! unrelated reason. **Unknown keys are refused by name** (`fleetd/docs/SPEC.md`
//! "unknown fields stay rejected"): a key this daemon does not know cannot
//! silently become a setting. Verbs are refused per version, so a reader verb on
//! the registrant socket is refused as a vocabulary mismatch, not served.
//!
//! The reply shapes are built here and nowhere else, so the documents
//! (`docs/CONSUMERS.md`) and the tests compare the same bytes.

use core::fmt;

use crate::json::{UnknownKey, Value, MAX_DOCUMENT_BYTES};
use crate::units::Priority;
use crate::{EVENT_VERSION, REGISTRATION_VERSION, STATE_VERSION, VERSION};

/// The largest accepted request line, in bytes.
pub const MAX_LINE_BYTES: usize = MAX_DOCUMENT_BYTES;
/// The largest registrant id, in UTF-8 bytes.
pub const MAX_ID_BYTES: usize = 128;
/// The largest container id or scope name, in UTF-8 bytes.
pub const MAX_CONTAINER_BYTES: usize = 128;

/// The version tag of a registrant verb.
pub const fn registration_version() -> &'static str {
    REGISTRATION_VERSION
}

/// The version tag of `status`, `candidates` and `version`.
pub const fn state_version() -> &'static str {
    STATE_VERSION
}

/// The version tag of `events`.
pub const fn event_version() -> &'static str {
    EVENT_VERSION
}

/// The keys every request carries, whatever its verb.
const COMMON_KEYS: [&str; 2] = ["v", "verb"];

/// A parsed `register` request.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RegisterRequest {
    /// The registrant's own unit id.
    pub id: String,
    /// The container the client named (an id or a `docker-<id>.scope`).
    pub container: String,
    /// The registrant's ranking fact.
    pub score: i64,
    /// The registrant's priority flag.
    pub priority: Priority,
    /// The kind of unit, free text (`gate`, `campaign`, ...).
    pub kind: String,
    /// The owning tool.
    pub owner: String,
    /// The run or lane id.
    pub run: String,
    /// The cadence the client declares, recorded for reporting only.
    pub heartbeat_s: Option<u64>,
}

/// A parsed request.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Request {
    /// `register {id, container, score, priority, kind, owner, run, heartbeat_s}`
    Register(Box<RegisterRequest>),
    /// `update {id, score, priority}` — either fact may be absent.
    Update {
        /// The unit id.
        id: String,
        /// The new ranking fact.
        score: Option<i64>,
        /// The new priority flag.
        priority: Option<Priority>,
    },
    /// `deregister {id}`: the registrant is about to stop or `rm` (design §3 M3).
    Deregister {
        /// The unit id.
        id: String,
    },
    /// `heartbeat {id}`.
    Heartbeat {
        /// The unit id.
        id: String,
    },
    /// `status`: mode, state, pressure, frozen units, parked seconds, desync.
    Status,
    /// `candidates`: the next tick's order, with the victim it implies.
    Candidates,
    /// `events {since_seq}`: journal rows above that sequence number.
    Events {
        /// The last row the client already has (0 asks for everything kept).
        since_seq: u64,
    },
    /// `version`: the daemon's package version and the wire versions it speaks.
    Version,
}

impl Request {
    /// The verb's wire spelling, for a refusal that must name it.
    pub fn verb_name(&self) -> &'static str {
        match self {
            Self::Register(_) => "register",
            Self::Update { .. } => "update",
            Self::Deregister { .. } => "deregister",
            Self::Heartbeat { .. } => "heartbeat",
            Self::Status => "status",
            Self::Candidates => "candidates",
            Self::Events { .. } => "events",
            Self::Version => "version",
        }
    }

    /// The version tag this verb must carry.
    pub fn expected_version(&self) -> &'static str {
        match self {
            Self::Events { .. } => EVENT_VERSION,
            Self::Status | Self::Candidates | Self::Version => STATE_VERSION,
            Self::Register(_)
            | Self::Update { .. }
            | Self::Deregister { .. }
            | Self::Heartbeat { .. } => REGISTRATION_VERSION,
        }
    }

    /// Whether this verb belongs to the registrant socket.
    pub fn is_registrant(&self) -> bool {
        !matches!(
            self,
            Self::Status | Self::Candidates | Self::Events { .. } | Self::Version
        )
    }
}

/// Why a line could not be read as a request.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WireError {
    /// The line is empty: nothing to answer.
    Empty,
    /// The line is larger than [`MAX_LINE_BYTES`].
    TooLarge {
        /// The observed size.
        bytes: usize,
    },
    /// A bounded identifier field exceeded its own byte limit.
    FieldTooLarge {
        /// The field.
        key: &'static str,
        /// The observed size.
        bytes: usize,
        /// The field's accepted maximum.
        limit: usize,
    },
    /// The document is not valid JSON.
    Json(String),
    /// A required protocol member is missing.
    MissingMember {
        /// The member.
        member: &'static str,
    },
    /// A protocol member has the wrong kind.
    MemberKind {
        /// The member.
        member: &'static str,
        /// What was required.
        expected: &'static str,
    },
    /// The version tag is not the one this verb speaks.
    Version {
        /// The verb.
        verb: String,
        /// The version the verb speaks.
        expected: &'static str,
        /// The version the client sent.
        found: String,
    },
    /// A member this vocabulary does not know: refused, never ignored.
    UnknownKey(String),
    /// A member value the vocabulary does not accept.
    Value {
        /// The member.
        key: &'static str,
        /// Why it was refused.
        detail: String,
    },
    /// A verb this vocabulary does not know.
    UnknownVerb(String),
}

impl fmt::Display for WireError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty => f.write_str("the request line is empty"),
            Self::TooLarge { bytes } => {
                write!(
                    f,
                    "the request is {bytes} bytes, above the {MAX_LINE_BYTES}-byte bound"
                )
            }
            Self::FieldTooLarge { key, bytes, limit } => {
                write!(f, "{key:?} is {bytes} bytes, above its {limit}-byte bound")
            }
            Self::Json(detail) => write!(f, "malformed JSON: {detail}"),
            Self::MissingMember { member } => write!(f, "the request lacks {member:?}"),
            Self::MemberKind { member, expected } => write!(f, "{member:?} must be {expected}"),
            Self::Version {
                verb,
                expected,
                found,
            } => {
                write!(
                    f,
                    "verb {verb:?} speaks {expected}, the request carries {found:?}"
                )
            }
            Self::UnknownKey(key) => write!(f, "unknown key {key:?}"),
            Self::Value { key, detail } => write!(f, "key {key}: {detail}"),
            Self::UnknownVerb(verb) => write!(f, "unknown verb {verb:?}"),
        }
    }
}

impl std::error::Error for WireError {}

impl From<UnknownKey> for WireError {
    fn from(error: UnknownKey) -> Self {
        Self::UnknownKey(error.0)
    }
}

/// The registrant verbs, in the group that owns the registrant socket.
pub const REGISTRANT_VERBS: [&str; 4] = ["register", "update", "heartbeat", "deregister"];
/// The reader verbs, in the group that owns the reader socket.
pub const READER_VERBS: [&str; 4] = ["status", "candidates", "events", "version"];

/// Parse one request line.
pub fn parse_request(line: &str) -> Result<Request, WireError> {
    let text = line.trim();
    if text.is_empty() {
        return Err(WireError::Empty);
    }
    if text.len() > MAX_LINE_BYTES {
        return Err(WireError::TooLarge { bytes: text.len() });
    }
    let value = Value::parse(text).map_err(|error| WireError::Json(error.to_string()))?;
    let members = value.members().ok_or(WireError::MemberKind {
        member: "request",
        expected: "an object",
    })?;
    let version = member_str(members, "v")?;
    let verb = member_str(members, "verb")?;
    let mut allowed: Vec<&str> = COMMON_KEYS.to_vec();
    let request = match verb.as_str() {
        "register" => {
            allowed.extend([
                "id",
                "container",
                "score",
                "priority",
                "kind",
                "owner",
                "run",
                "heartbeat_s",
            ]);
            let pairs = into_object(members.to_vec(), &allowed)?;
            Request::Register(Box::new(RegisterRequest {
                id: required_str(&pairs, "id")?,
                container: required_str(&pairs, "container")?,
                score: optional_i64(&pairs, "score")?.unwrap_or(0),
                priority: match optional_str(&pairs, "priority")? {
                    Some(name) => Priority::from_name(&name).ok_or(WireError::Value {
                        key: "priority",
                        detail: format!("{name:?} is not one of: freezable, last-resort"),
                    })?,
                    None => Priority::Freezable,
                },
                kind: optional_str(&pairs, "kind")?.unwrap_or_default(),
                owner: optional_str(&pairs, "owner")?.unwrap_or_default(),
                run: optional_str(&pairs, "run")?.unwrap_or_default(),
                heartbeat_s: optional_u64(&pairs, "heartbeat_s")?,
            }))
        }
        "update" => {
            allowed.extend(["id", "score", "priority"]);
            let pairs = into_object(members.to_vec(), &allowed)?;
            let score = optional_i64(&pairs, "score")?;
            let priority = match optional_str(&pairs, "priority")? {
                Some(name) => Some(Priority::from_name(&name).ok_or(WireError::Value {
                    key: "priority",
                    detail: format!("{name:?} is not one of: freezable, last-resort"),
                })?),
                None => None,
            };
            if score.is_none() && priority.is_none() {
                return Err(WireError::Value {
                    key: "update",
                    detail: "carries neither `score` nor `priority`".into(),
                });
            }
            Request::Update {
                id: required_str(&pairs, "id")?,
                score,
                priority,
            }
        }
        "heartbeat" => {
            allowed.push("id");
            let pairs = into_object(members.to_vec(), &allowed)?;
            Request::Heartbeat {
                id: required_str(&pairs, "id")?,
            }
        }
        "deregister" => {
            allowed.push("id");
            let pairs = into_object(members.to_vec(), &allowed)?;
            Request::Deregister {
                id: required_str(&pairs, "id")?,
            }
        }
        "status" => {
            // No optional members: the verb stands alone, and any extra key is
            // already refused by `into_object`.
            into_object(members.to_vec(), &allowed)?;
            Request::Status
        }
        "candidates" => {
            into_object(members.to_vec(), &allowed)?;
            Request::Candidates
        }
        "events" => {
            allowed.push("since_seq");
            let pairs = into_object(members.to_vec(), &allowed)?;
            Request::Events {
                since_seq: optional_u64(&pairs, "since_seq")?.unwrap_or(0),
            }
        }
        "version" => {
            into_object(members.to_vec(), &allowed)?;
            Request::Version
        }
        other => return Err(WireError::UnknownVerb(other.to_string())),
    };
    let expected = request.expected_version();
    if version != expected {
        return Err(WireError::Version {
            verb,
            expected,
            found: version,
        });
    }
    Ok(request)
}

fn into_object(
    pairs: Vec<(String, Value)>,
    allowed: &[&str],
) -> Result<Vec<(String, Value)>, WireError> {
    // `into_object` on a reconstructed value keeps one implementation of the
    // closed-key rule (json.rs) instead of a second copy here.
    Value::Object(pairs)
        .into_object(allowed)
        .map_err(WireError::from)
}

fn member_str(members: &[(String, Value)], name: &'static str) -> Result<String, WireError> {
    let value = members
        .iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value)
        .ok_or(WireError::MissingMember { member: name })?;
    value
        .as_str()
        .map(str::to_string)
        .ok_or(WireError::MemberKind {
            member: name,
            expected: "a string",
        })
}

fn required_str(pairs: &[(String, Value)], name: &'static str) -> Result<String, WireError> {
    let value = pairs
        .iter()
        .find(|(key, _)| key == name)
        .map(|(_, value)| value)
        .ok_or(WireError::MissingMember { member: name })?;
    let text = value.as_str().ok_or(WireError::MemberKind {
        member: name,
        expected: "a string",
    })?;
    if text.trim().is_empty() {
        return Err(WireError::Value {
            key: name,
            detail: "must not be empty".into(),
        });
    }
    let limit = match name {
        "id" => MAX_ID_BYTES,
        "container" => MAX_CONTAINER_BYTES,
        _ => usize::MAX,
    };
    if text.len() > limit {
        return Err(WireError::FieldTooLarge {
            key: name,
            bytes: text.len(),
            limit,
        });
    }
    Ok(text.to_string())
}

fn optional_str(
    pairs: &[(String, Value)],
    name: &'static str,
) -> Result<Option<String>, WireError> {
    match pairs.iter().find(|(key, _)| key == name) {
        None => Ok(None),
        Some((_, Value::Null)) => Ok(None),
        Some((_, value)) => {
            value
                .as_str()
                .map(|text| Some(text.to_string()))
                .ok_or(WireError::MemberKind {
                    member: name,
                    expected: "a string",
                })
        }
    }
}

fn optional_i64(pairs: &[(String, Value)], name: &'static str) -> Result<Option<i64>, WireError> {
    match pairs.iter().find(|(key, _)| key == name) {
        None => Ok(None),
        Some((_, Value::Null)) => Ok(None),
        Some((_, value)) => value.as_i64().map(Some).ok_or(WireError::MemberKind {
            member: name,
            expected: "an integer",
        }),
    }
}

fn optional_u64(pairs: &[(String, Value)], name: &'static str) -> Result<Option<u64>, WireError> {
    match optional_i64(pairs, name)? {
        None => Ok(None),
        Some(number) => u64::try_from(number)
            .map(Some)
            .map_err(|_| WireError::Value {
                key: name,
                detail: format!("must not be negative, got {number}"),
            }),
    }
}

fn text(value: &str) -> Value {
    Value::String(value.to_string())
}

fn integer(value: i64) -> Value {
    Value::Integer(value)
}

/// A reply member list, with `v` and `ok` always first: a refusal is
/// recognisable without parsing further.
fn reply(version: &str, ok: bool, rest: Vec<(String, Value)>) -> Value {
    let mut pairs = Vec::with_capacity(rest.len() + 2);
    pairs.push(("v".to_string(), text(version)));
    pairs.push(("ok".to_string(), Value::Bool(ok)));
    pairs.extend(rest);
    Value::Object(pairs)
}

/// `{"v": <registration>, "ok": false, "id": ..., "reason": ..., "detail": ...}`
pub fn refused(id: Option<&str>, reason: &str, detail: &str) -> Value {
    let mut rest = Vec::new();
    if let Some(id) = id {
        rest.push(("id".to_string(), text(id)));
    }
    rest.push(("reason".to_string(), text(reason)));
    rest.push(("detail".to_string(), text(detail)));
    reply(REGISTRATION_VERSION, false, rest)
}

/// `{"v": <registration>, "ok": true, "id": ..., "cgroup": ..., "cgroup_id": ..., "ttl_s": ...}`
pub fn register_ok(id: &str, scope: &str, cgroup_id: u64, ttl_s: u64) -> Value {
    reply(
        REGISTRATION_VERSION,
        true,
        vec![
            ("id".to_string(), text(id)),
            ("cgroup".to_string(), text(scope)),
            ("cgroup_id".to_string(), integer(cgroup_id as i64)),
            ("ttl_s".to_string(), integer(ttl_s as i64)),
        ],
    )
}

/// `{"v": <registration>, "ok": true, "id": ..., "score": ..., "priority": ...}` for `update`,
/// which answers with the facts the registration now carries (design §3).
pub fn update_ok(id: &str, score: i64, priority: Priority) -> Value {
    reply(
        REGISTRATION_VERSION,
        true,
        vec![
            ("id".to_string(), text(id)),
            ("score".to_string(), integer(score)),
            ("priority".to_string(), text(priority.as_str())),
        ],
    )
}

/// `{"v": <registration>, "ok": true, "id": ...}` for `heartbeat` and `deregister`.
pub fn ok_id(id: &str) -> Value {
    reply(
        REGISTRATION_VERSION,
        true,
        vec![("id".to_string(), text(id))],
    )
}

/// Any other successful reply (reader verbs fill their own body).
pub fn ok(version: &str, rest: Vec<(String, Value)>) -> Value {
    reply(version, true, rest)
}

/// A refusal on a reader socket: `{"v": <version>, "ok": false, "reason": ..., "detail": ...}`.
pub fn refused_reader(version: &str, reason: &str, detail: &str) -> Value {
    reply(
        version,
        false,
        vec![
            ("reason".to_string(), text(reason)),
            ("detail".to_string(), text(detail)),
        ],
    )
}

/// The `version` reply: the package version and the three wire versions.
pub fn version() -> Value {
    ok(
        STATE_VERSION,
        vec![
            ("version".to_string(), text(VERSION)),
            ("registration".to_string(), text(REGISTRATION_VERSION)),
            ("event".to_string(), text(EVENT_VERSION)),
            ("state".to_string(), text(STATE_VERSION)),
        ],
    )
}

/// One candidate row of a `candidates` reply (and of the `next` field of `status`).
pub fn candidate_row(
    id: &str,
    unit: &str,
    cgroup: &str,
    cgroup_id: u64,
    score: i64,
    priority: Priority,
    silent_s: u64,
) -> Value {
    Value::Object(vec![
        ("id".to_string(), text(id)),
        ("unit".to_string(), text(unit)),
        ("cgroup".to_string(), text(cgroup)),
        ("cgroup_id".to_string(), integer(cgroup_id as i64)),
        ("score".to_string(), integer(score)),
        ("priority".to_string(), text(priority.as_str())),
        ("silent_s".to_string(), integer(silent_s as i64)),
    ])
}

/// Add a member to an object reply, keeping the member order the caller set.
pub fn with(mut value: Value, key: &str, member: Value) -> Value {
    if let Value::Object(ref mut pairs) = value {
        pairs.push((key.to_string(), member));
    }
    value
}

#[cfg(test)]
mod tests {
    use super::*;

    fn registration_line(body: &str) -> String {
        format!("{{\"v\":\"{REGISTRATION_VERSION}\",{body}}}")
    }

    #[test]
    fn every_registrant_verb_parses_and_carries_its_version() {
        let register = parse_request(&registration_line(
            "\"verb\":\"register\",\"id\":\"rg-1\",\"container\":\"abc123\",\"score\":7,\
             \"priority\":\"last-resort\",\"kind\":\"gate\",\"owner\":\"run-gate\",\"run\":\"selftest-r7\",\
             \"heartbeat_s\":30",
        ))
        .expect("register parses");
        let Request::Register(request) = &register else {
            panic!("{register:?}")
        };
        assert_eq!(request.id, "rg-1");
        assert_eq!(request.score, 7);
        assert_eq!(request.priority, Priority::LastResort);
        assert_eq!(request.heartbeat_s, Some(30));
        assert_eq!(register.expected_version(), REGISTRATION_VERSION);
        assert!(register.is_registrant());

        let update = parse_request(&registration_line(
            "\"verb\":\"update\",\"id\":\"rg-1\",\"score\":9",
        ))
        .expect("update parses");
        assert_eq!(
            update,
            Request::Update {
                id: "rg-1".to_string(),
                score: Some(9),
                priority: None
            }
        );
        let heartbeat = parse_request(&registration_line("\"verb\":\"heartbeat\",\"id\":\"rg-1\""))
            .expect("heartbeat parses");
        assert_eq!(
            heartbeat,
            Request::Heartbeat {
                id: "rg-1".to_string()
            }
        );
        let deregister = parse_request(&registration_line(
            "\"verb\":\"deregister\",\"id\":\"rg-1\"",
        ))
        .expect("deregister parses");
        assert_eq!(
            deregister,
            Request::Deregister {
                id: "rg-1".to_string()
            }
        );
    }

    #[test]
    fn registration_identifiers_accept_the_limit_and_refuse_excess_bytes() {
        let id = "i".repeat(MAX_ID_BYTES);
        let container = "c".repeat(MAX_CONTAINER_BYTES);
        let line = registration_line(&format!(
            "\"verb\":\"register\",\"id\":\"{id}\",\"container\":\"{container}\""
        ));
        assert!(matches!(parse_request(&line), Ok(Request::Register(_))));

        let too_long_id = "i".repeat(MAX_ID_BYTES + 1);
        let line = registration_line(&format!(
            "\"verb\":\"heartbeat\",\"id\":\"{too_long_id}\""
        ));
        assert_eq!(
            parse_request(&line),
            Err(WireError::FieldTooLarge {
                key: "id",
                bytes: MAX_ID_BYTES + 1,
                limit: MAX_ID_BYTES,
            })
        );

        let too_long_container = "é".repeat(MAX_CONTAINER_BYTES / 2 + 1);
        let line = registration_line(&format!(
            "\"verb\":\"register\",\"id\":\"i\",\"container\":\"{too_long_container}\""
        ));
        assert_eq!(
            parse_request(&line),
            Err(WireError::FieldTooLarge {
                key: "container",
                bytes: MAX_CONTAINER_BYTES + 2,
                limit: MAX_CONTAINER_BYTES,
            })
        );
    }

    #[test]
    fn register_defaults_are_the_guards_own_and_absent_facts_stay_absent() {
        let request = parse_request(&registration_line(
            "\"verb\":\"register\",\"id\":\"x\",\"container\":\"c\"",
        ))
        .expect("minimal register");
        let Request::Register(request) = request else {
            panic!()
        };
        assert_eq!(request.priority, Priority::Freezable, "the default flag");
        assert_eq!(request.score, 0);
        assert_eq!(
            request.heartbeat_s, None,
            "a client declares a cadence, never a job bound"
        );
        assert!(request.kind.is_empty() && request.run.is_empty());
    }

    #[test]
    fn the_reader_verbs_parse_with_their_own_version_tags() {
        let status = parse_request(&format!(
            "{{\"v\":\"{STATE_VERSION}\",\"verb\":\"status\"}}"
        ))
        .expect("status");
        assert_eq!(status, Request::Status);
        assert!(!status.is_registrant());
        let candidates = parse_request(&format!(
            "{{\"v\":\"{STATE_VERSION}\",\"verb\":\"candidates\"}}"
        ))
        .expect("candidates");
        assert_eq!(candidates, Request::Candidates);
        let version = parse_request(&format!(
            "{{\"v\":\"{STATE_VERSION}\",\"verb\":\"version\"}}"
        ))
        .expect("version");
        assert_eq!(version, Request::Version);
        let events = parse_request(&format!(
            "{{\"v\":\"{EVENT_VERSION}\",\"verb\":\"events\",\"since_seq\":17}}"
        ))
        .expect("events");
        assert_eq!(events, Request::Events { since_seq: 17 });
        // `since_seq` is optional: 0 asks for everything the journal kept.
        let events = parse_request(&format!(
            "{{\"v\":\"{EVENT_VERSION}\",\"verb\":\"events\"}}"
        ))
        .expect("events");
        assert_eq!(events, Request::Events { since_seq: 0 });
    }

    #[test]
    fn a_wrong_version_names_the_version_the_verb_speaks() {
        let line = format!(
            "{{\"v\":\"{STATE_VERSION}\",\"verb\":\"register\",\"id\":\"a\",\"container\":\"c\"}}"
        );
        let error =
            parse_request(&line).expect_err("a reader version on a registrant verb is refused");
        assert_eq!(
            error,
            WireError::Version {
                verb: "register".to_string(),
                expected: REGISTRATION_VERSION,
                found: STATE_VERSION.to_string()
            }
        );
        let line = format!("{{\"v\":\"{REGISTRATION_VERSION}\",\"verb\":\"status\"}}");
        let error =
            parse_request(&line).expect_err("a registrant version on a reader verb is refused");
        assert!(error.to_string().contains(STATE_VERSION), "{error}");
    }

    #[test]
    fn unknown_keys_unknown_verbs_and_bad_values_are_refused_by_name() {
        let cases = [
            registration_line(
                "\"verb\":\"register\",\"id\":\"a\",\"container\":\"c\",\"ttl_s\":90",
            ),
            registration_line(
                "\"verb\":\"register\",\"id\":\"a\",\"container\":\"c\",\"cgroup\":\"/dev.slice\"",
            ),
            format!("{{\"v\":\"{EVENT_VERSION}\",\"verb\":\"status\"}}"),
        ];
        let error = parse_request(&cases[0]).expect_err("ttl_s is never sent by a client");
        assert_eq!(
            error,
            WireError::UnknownKey("ttl_s".to_string()),
            "the key is named"
        );
        let error = parse_request(&cases[1]).expect_err("a client never names a cgroup path");
        assert_eq!(error, WireError::UnknownKey("cgroup".to_string()));
        let error = parse_request(&cases[2]).expect_err("version/verb mismatch");
        assert!(matches!(error, WireError::Version { .. }));

        let error = parse_request(&registration_line("\"verb\":\"freeze\",\"id\":\"a\""))
            .expect_err("no act verb");
        assert_eq!(error, WireError::UnknownVerb("freeze".to_string()));
        let error = parse_request(&registration_line(
            "\"verb\":\"register\",\"container\":\"c\"",
        ))
        .expect_err("no id");
        assert_eq!(error, WireError::MissingMember { member: "id" });
        let error = parse_request(&registration_line("\"verb\":\"update\",\"id\":\"a\""))
            .expect_err("nothing to update");
        assert!(matches!(error, WireError::Value { key: "update", .. }));
        let error = parse_request(&registration_line(
            "\"verb\":\"register\",\"id\":\"a\",\"container\":\"c\",\"priority\":\"high\"",
        ))
        .expect_err("priority is a closed vocabulary");
        assert!(matches!(
            error,
            WireError::Value {
                key: "priority",
                ..
            }
        ));
        let error = parse_request(&registration_line(
            "\"verb\":\"register\",\"id\":\"\",\"container\":\"c\"",
        ))
        .expect_err("an empty id");
        assert!(matches!(error, WireError::Value { key: "id", .. }));
        let error = parse_request(&registration_line(
            "\"verb\":\"register\",\"id\":\"a\",\"container\":\"c\",\"heartbeat_s\":-1",
        ))
        .expect_err("a negative cadence");
        assert!(matches!(
            error,
            WireError::Value {
                key: "heartbeat_s",
                ..
            }
        ));
        let error = parse_request("").expect_err("an empty line");
        assert_eq!(error, WireError::Empty);
        let error = parse_request(&"x".repeat(MAX_LINE_BYTES + 1)).expect_err("above the bound");
        assert!(matches!(error, WireError::TooLarge { .. }));
    }

    #[test]
    fn the_reply_shapes_are_what_the_documents_show() {
        let refused_reply = refused(
            Some("x"),
            "none",
            "no docker-<id>.scope under the allowed ancestors",
        );
        assert_eq!(
            refused_reply.to_json(),
            format!(
                "{{\"v\":\"{REGISTRATION_VERSION}\",\"ok\":false,\"id\":\"x\",\"reason\":\"none\",\
                 \"detail\":\"no docker-<id>.scope under the allowed ancestors\"}}"
            )
        );
        let registered = register_ok("rg-abc123", "docker-0123.scope", 4242, 90);
        let text = registered.to_json();
        assert!(
            text.starts_with(&format!(
                "{{\"v\":\"{REGISTRATION_VERSION}\",\"ok\":true,\"id\":\"rg-abc123\""
            )),
            "{text}"
        );
        assert!(text.contains("\"ttl_s\":90"), "{text}");
        assert_eq!(
            ok_id("rg-1").to_json(),
            format!("{{\"v\":\"{REGISTRATION_VERSION}\",\"ok\":true,\"id\":\"rg-1\"}}")
        );
        let refused_reply = refused_reader(EVENT_VERSION, "unsupported", "unknown version");
        assert!(refused_reply.to_json().contains("\"ok\":false"));
        let version_reply = version();
        assert_eq!(
            version_reply.get("version").and_then(Value::as_str),
            Some(VERSION)
        );
        assert_eq!(
            version_reply.get("state").and_then(Value::as_str),
            Some(STATE_VERSION)
        );
        let row = candidate_row("gr", "unit", "docker-1.scope", 7, 3, Priority::Freezable, 4);
        let text = with(ok(STATE_VERSION, Vec::new()), "next", row).to_json();
        assert!(text.contains("\"priority\":\"freezable\""), "{text}");
    }
}
