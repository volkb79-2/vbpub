//! `thrashguard` — the daemon and its local verbs (design §3 "Standalone", §6d).
//!
//! P1 `observe` compiles **no act path**: this binary reads pressure, resolves a
//! candidate to its scope, and reports what it *would* do. `mode = enforce` is
//! refused here, at the run boundary, with one line (design §5, D6).
//!
//! `status`, `candidates` and `events` answer from the configuration and the
//! journal alone when `--socket` is absent, which is what makes a standalone host
//! complete without a daemon, fleetd or a registrant. With `--socket` they ask a
//! running daemon instead. The decisions themselves live in the library
//! ([`thrashguard::report`]) so the same planner serves both paths and is tested
//! once, without a host.

#![forbid(unsafe_code)]

use std::path::Path;
use std::process::ExitCode;

use thrashguard::cli::{self, Args, Verb};
use thrashguard::config::StaticUnit;
use thrashguard::config::{self, Config, Mode};
use thrashguard::events;
use thrashguard::json::Value;
use thrashguard::psi::{Poller, Reading};
use thrashguard::report::{self, ParkingState, Would};
use thrashguard::socket::{self, Role};
use thrashguard::units::{self, Priority, Registration, Store};
use thrashguard::{EVENT_VERSION, STATE_VERSION};

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = match cli::parse(argv) {
        Ok(args) => args,
        Err(error) => {
            eprintln!("thrashguard: {error}");
            eprintln!("{}", cli::USAGE);
            return ExitCode::from(2);
        }
    };
    match run(&args) {
        Ok(()) => ExitCode::SUCCESS,
        Err(fatal) => {
            for line in fatal.message.lines() {
                eprintln!("thrashguard: {line}");
            }
            ExitCode::from(fatal.code)
        }
    }
}

/// Why a verb could not complete, and the exit status it earns.
///
/// Two codes, deliberately distinct: 2 means the operator must fix an
/// invocation or a configuration, 3 means a fact the guard needs is unreadable —
/// the guard cannot claim, and must not print a synthetic value.
#[derive(Debug)]
struct Fatal {
    message: String,
    code: u8,
}

impl Fatal {
    fn usage(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            code: 2,
        }
    }

    fn unknown(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            code: 3,
        }
    }
}

fn run(args: &Args) -> Result<(), Fatal> {
    if args.verb == Verb::Version {
        // `version` describes this binary, so it never needs a daemon.
        println!(
            "thrashguard {} ({STATE_VERSION}, {EVENT_VERSION}, {})",
            thrashguard::VERSION,
            thrashguard::REGISTRATION_VERSION
        );
        return Ok(());
    }
    let config = match &args.config {
        Some(path) => config::load(path)
            .map_err(|error| Fatal::usage(format!("cannot load the configuration: {error}")))?,
        None => Config::defaults(),
    };
    if args.verb == Verb::Serve {
        // `enforce` is valid vocabulary, and `status` must be able to report it,
        // but this build cannot honour it: one line, at the run boundary.
        config
            .check_for_run()
            .map_err(|error| Fatal::usage(error.to_string()))?;
    }
    if let Some(path) = &args.socket {
        return ask_socket(path, args);
    }
    match args.verb {
        Verb::Version => unreachable!("handled above"),
        Verb::Status => status(&config, args),
        Verb::Candidates => candidates(&config, args),
        Verb::Events => events(&config, args),
        Verb::Serve => serve(&config),
    }
}

/// The store for this configuration with every `[[static_unit]]` resolved.
///
/// A static unit that cannot resolve is a loud refusal, never a candidate quietly
/// dropped: a host whose `/etc` names a cgroup that is not there must hear about
/// it (design §3 "refuses the registration naming the reason", §6d).
fn store_for(config: &Config, root: &Path, now_ns: u64) -> Result<Store, Fatal> {
    let mut store = Store::from_config(config);
    for unit in &config.static_units {
        let scope = units::resolve_static_unit(root, unit, config).map_err(|refusal| {
            Fatal::unknown(format!(
                "[[static_unit]] {}: {}",
                unit.name,
                refusal.detail()
            ))
        })?;
        store
            .register(static_registration(unit, scope, now_ns))
            .map_err(|error| Fatal::usage(error.to_string()))?;
    }
    Ok(store)
}

/// A `/etc`-supplied unit becomes the same candidate shape a registrant creates,
/// so one victim rule sees one set (design §3, §6d).
fn static_registration(unit: &StaticUnit, scope: units::Scope, now_ns: u64) -> Registration {
    Registration {
        id: unit.name.clone(),
        container: unit.cgroup.clone(),
        scope,
        score: 0,
        priority: Priority::Freezable,
        kind: "static".to_string(),
        owner: "/etc".to_string(),
        run: unit.name.clone(),
        heartbeat_s: None,
        registered_seq: 0,
        last_heartbeat_ns: now_ns,
    }
}

fn status(config: &Config, args: &Args) -> Result<(), Fatal> {
    let reading = Poller::new(&args.pressure_path).poll();
    let store = store_for(config, &args.cgroup_root, 0)?;
    let state = ParkingState {
        parked: 0,
        candidates: store.len(),
    };
    let would = report::choose_victim(config, &store, state, 0, &|_| None);
    let reply = status_reply(config, &reading, &store, &would, 0);
    println!("{}", reply.to_json());
    Ok(())
}

fn candidates(config: &Config, args: &Args) -> Result<(), Fatal> {
    let store = store_for(config, &args.cgroup_root, 0)?;
    let state = ParkingState {
        parked: 0,
        candidates: store.len(),
    };
    let would = report::choose_victim(config, &store, state, 0, &|_| None);
    println!("{}", candidates_reply(config, &store, &would, 0).to_json());
    Ok(())
}

/// `events` without a socket reads the journal the daemon wrote, paged by
/// `--since-seq`. Reading it writes nothing.
fn events(config: &Config, args: &Args) -> Result<(), Fatal> {
    let boot_id = events::read_boot_id(Path::new(events::BOOT_ID_PATH))
        .unwrap_or_else(|| "unknown".to_string());
    let current = config.state_dir.join(events::JOURNAL_FILE);
    let rotated = config.state_dir.join(events::ROTATED_FILE);
    let file = if current.is_file() {
        current
    } else if rotated.is_file() {
        rotated
    } else {
        // No journal is an empty page with a false `truncated`, not an error: a
        // guard that has not run yet has recorded nothing, which is a fact.
        println!("{}", events_reply(&boot_id, Vec::new(), false).to_json());
        return Ok(());
    };
    let text = std::fs::read_to_string(&file)
        .map_err(|error| Fatal::unknown(format!("cannot read {}: {error}", file.display())))?;
    let mut rows = Vec::new();
    let mut skipped = 0u64;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match Value::parse(line) {
            Ok(value) => {
                let seq = value.get("seq").and_then(Value::as_i64).unwrap_or(0);
                if seq > i64::try_from(args.since_seq).unwrap_or(i64::MAX) {
                    rows.push(value);
                }
            }
            // A torn line at a rotation is a fact about the file, not a reason to
            // call the whole journal unreadable; it is counted, never served.
            Err(_) => skipped += 1,
        }
    }
    let truncated = skipped > 0;
    println!("{}", events_reply(&boot_id, rows, truncated).to_json());
    Ok(())
}

fn events_reply(boot_id: &str, rows: Vec<Value>, truncated: bool) -> Value {
    thrashguard::wire::ok(
        EVENT_VERSION,
        vec![
            ("boot_id".to_string(), Value::String(boot_id.to_string())),
            ("rows".to_string(), Value::Array(rows)),
            ("truncated".to_string(), Value::Bool(truncated)),
        ],
    )
}

/// `serve` is the tick loop (P1 subtask 5's daemon): refused here rather than
/// half-started, because a daemon that resolves candidates and journals nothing
/// would look like a running guard while observing nothing.
fn serve(config: &Config) -> Result<(), Fatal> {
    let _ = config;
    Err(Fatal::usage(
        "serve is not wired in this build: the observe cut ships the config reader, the wire, \
         the journal and the report-mode planner; the tick loop is the next P1 commit",
    ))
}

/// `status` as the wire document: mode, pressure (or its unknown reason), the
/// candidate count, the next decision and an empty frozen set (nothing is frozen
/// in P1 — `frozen[]` is the P2 fact and is reported as empty, never invented).
fn status_reply(
    config: &Config,
    reading: &Reading,
    store: &Store,
    would: &Would,
    parked_s: u64,
) -> Value {
    let psi = match reading {
        Reading::Known(pressure) => Value::Object(vec![
            (
                "v".to_string(),
                Value::Integer(i64::from(pressure.full.avg10)),
            ),
            (
                "some".to_string(),
                Value::Integer(i64::from(pressure.some.avg10)),
            ),
            (
                "full".to_string(),
                Value::Integer(i64::from(pressure.full.avg10)),
            ),
            (
                "total_us".to_string(),
                Value::Integer(as_i64(pressure.full.total_us)),
            ),
        ]),
        Reading::Unknown(why) => Value::Object(vec![
            ("v".to_string(), Value::Null),
            (
                "reason".to_string(),
                Value::String(why.reason().to_string()),
            ),
            ("detail".to_string(), Value::String(why.detail())),
        ]),
    };
    let mut body = vec![
        (
            "mode".to_string(),
            Value::String(config.mode.as_str().to_string()),
        ),
        (
            "enforce_refused".to_string(),
            Value::Bool(config.mode == Mode::Enforce),
        ),
        ("psi".to_string(), psi),
        ("candidates".to_string(), Value::Integer(store.len() as i64)),
        ("frozen".to_string(), Value::Array(Vec::new())),
        ("parked_s".to_string(), Value::Integer(as_i64(parked_s))),
        ("desync".to_string(), Value::Array(Vec::new())),
        ("ttl_s".to_string(), Value::Integer(as_i64(config.ttl_s()))),
        ("would".to_string(), would_value(would)),
    ];
    let (rule, unknown) = report::effective_rule(config);
    body.push(("rule".to_string(), Value::String(rule.as_str().to_string())));
    if let Some(reason) = unknown {
        body.push(("rule_reason".to_string(), Value::String(reason.to_string())));
    }
    thrashguard::wire::ok(STATE_VERSION, body)
}

fn candidates_reply(config: &Config, store: &Store, would: &Would, now_ns: u64) -> Value {
    let mut rows = Vec::new();
    for candidate in store.newest_first() {
        rows.push(thrashguard::wire::candidate_row(
            &candidate.id,
            &candidate.run,
            &candidate.scope.name,
            candidate.scope.cgroup_id,
            candidate.score,
            candidate.priority,
            candidate.silent_s(now_ns),
        ));
    }
    let mut body = vec![
        (
            "mode".to_string(),
            Value::String(config.mode.as_str().to_string()),
        ),
        (
            "rule".to_string(),
            Value::String(report::effective_rule(config).0.as_str().to_string()),
        ),
        ("order".to_string(), Value::Array(rows)),
        ("would".to_string(), would_value(would)),
    ];
    if let (_, Some(unknown)) = report::effective_rule(config) {
        body.push((
            "rule_reason".to_string(),
            Value::String(unknown.to_string()),
        ));
    }
    thrashguard::wire::ok(STATE_VERSION, body)
}

/// One decision as a document, with `simulated: true` — the marker that tells a
/// reader nothing was frozen (P1 has no interval to record).
fn would_value(would: &Would) -> Value {
    match would {
        Would::Park {
            id,
            unit,
            cgroup,
            cgroup_id,
            rule,
        } => Value::Object(vec![
            (
                "action".to_string(),
                Value::String("would-freeze".to_string()),
            ),
            ("id".to_string(), Value::String(id.clone())),
            ("unit".to_string(), Value::String(unit.clone())),
            ("cgroup".to_string(), Value::String(cgroup.clone())),
            ("cgroup_id".to_string(), Value::Integer(as_i64(*cgroup_id))),
            ("rule".to_string(), Value::String(rule.as_str().to_string())),
            ("simulated".to_string(), Value::Bool(true)),
        ]),
        Would::Hold { why } => Value::Object(vec![
            (
                "action".to_string(),
                Value::String("would-hold".to_string()),
            ),
            ("why".to_string(), Value::String(why.as_str().to_string())),
            ("simulated".to_string(), Value::Bool(true)),
        ]),
        Would::Thaw { id, unit, cgroup } => Value::Object(vec![
            (
                "action".to_string(),
                Value::String("would-thaw".to_string()),
            ),
            ("id".to_string(), Value::String(id.clone())),
            ("unit".to_string(), Value::String(unit.clone())),
            ("cgroup".to_string(), Value::String(cgroup.clone())),
            ("simulated".to_string(), Value::Bool(true)),
        ]),
    }
}

fn as_i64(value: u64) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

/// Ask a running daemon over its reader socket: one line out, one line back.
fn ask_socket(path: &Path, args: &Args) -> Result<(), Fatal> {
    let request = match args.verb {
        Verb::Version => unreachable!("`version` is answered locally, before this"),
        Verb::Status => format!("{{\"v\":\"{STATE_VERSION}\",\"verb\":\"status\"}}"),
        Verb::Candidates => format!("{{\"v\":\"{STATE_VERSION}\",\"verb\":\"candidates\"}}"),
        Verb::Events => {
            format!(
                "{{\"v\":\"{EVENT_VERSION}\",\"verb\":\"events\",\"since_seq\":{}}}",
                args.since_seq
            )
        }
        Verb::Serve => unreachable!("`serve` never asks a socket"),
    };
    let stream = std::os::unix::net::UnixStream::connect(path)
        .map_err(|error| Fatal::unknown(format!("cannot reach {}: {error}", path.display())))?;
    let mut session = socket::Session::new(
        stream
            .try_clone()
            .map_err(|error| Fatal::unknown(error.to_string()))?,
        stream,
        Role::Reader,
    );
    let value = session.request(&request).map_err(|error| {
        Fatal::unknown(format!(
            "{} answered nothing usable: {error}",
            path.display()
        ))
    })?;
    println!("{}", value.to_json());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use thrashguard::config::VictimRule;
    use thrashguard::testutil::TempTree;

    fn static_unit(name: &str, cgroup: &str) -> StaticUnit {
        StaticUnit {
            name: name.to_string(),
            cgroup: cgroup.to_string(),
        }
    }

    #[test]
    fn a_static_unit_that_cannot_resolve_is_a_refusal_not_a_silent_candidate() {
        let tree = TempTree::new("main-static");
        let mut config = Config::defaults();
        config.static_units = vec![static_unit("missing", "dev-gates.slice/nothing.scope")];
        let error = store_for(&config, tree.path(), 0).expect_err("cannot resolve");
        assert_eq!(error.code, 3, "an unreadable fact is not a usage error");
        assert!(error.message.contains("missing"), "{}", error.message);
        // The same unit, once the cgroup exists, is a candidate.
        std::fs::create_dir_all(tree.at("dev-gates.slice/nothing.scope")).expect("create");
        let store = store_for(&config, tree.path(), 0).expect("resolves");
        assert_eq!(store.len(), 1);
        assert_eq!(store.get("missing").expect("stored").kind, "static");
    }

    #[test]
    fn status_reports_the_mode_the_pressure_and_an_empty_frozen_set() {
        let tree = TempTree::new("main-status");
        std::fs::create_dir_all(tree.at("dev-gates.slice/runner.scope")).expect("create scope");
        let mut config = Config::defaults();
        config.static_units = vec![static_unit("runner", "dev-gates.slice/runner.scope")];
        let pressure = tree.write(
            "memory.pressure",
            "some avg10=1.00 avg60=0.50 avg300=0.20 total=12\n\
             full avg10=0.50 avg60=0.00 avg300=0.00 total=3\n",
        );
        let reading = Poller::new(&pressure).poll();
        let store = store_for(&config, tree.path(), 0).expect("resolves");
        let would = report::choose_victim(
            &config,
            &store,
            ParkingState {
                parked: 0,
                candidates: 1,
            },
            0,
            &|_| None,
        );
        let reply = status_reply(&config, &reading, &store, &would, 0);
        assert_eq!(reply.get("mode").and_then(Value::as_str), Some("report"));
        assert_eq!(reply.get("candidates").and_then(Value::as_i64), Some(1));
        assert_eq!(
            reply
                .get("frozen")
                .and_then(Value::as_array)
                .map(<[_]>::len),
            Some(0)
        );
        assert_eq!(
            reply
                .get("desync")
                .and_then(Value::as_array)
                .map(<[_]>::len),
            Some(0)
        );
        // One candidate and `never_park_last = true`: parking it would leave nothing
        // running, so the decision is a NAMED hold, not a park (`units`/`report`).
        assert_eq!(
            reply
                .get("would")
                .and_then(|body| body.get("action"))
                .and_then(Value::as_str),
            Some("would-hold")
        );
        assert_eq!(
            reply
                .get("would")
                .and_then(|body| body.get("why"))
                .and_then(Value::as_str),
            Some("never-park-last")
        );
        assert_eq!(
            reply
                .get("psi")
                .and_then(|body| body.get("v"))
                .and_then(Value::as_i64),
            Some(50),
            "hundredths"
        );
        // Two candidates do allow one park, and `status` says what it would be.
        std::fs::create_dir_all(tree.at("dev-gates.slice/second.scope")).expect("create second");
        config
            .static_units
            .push(static_unit("second", "dev-gates.slice/second.scope"));
        let store = store_for(&config, tree.path(), 0).expect("resolves");
        let would = report::choose_victim(
            &config,
            &store,
            ParkingState {
                parked: 0,
                candidates: 2,
            },
            0,
            &|_| None,
        );
        let reply = status_reply(&config, &reading, &store, &would, 0);
        assert_eq!(
            reply
                .get("would")
                .and_then(|body| body.get("action"))
                .and_then(Value::as_str),
            Some("would-freeze")
        );
        assert_eq!(
            reply
                .get("would")
                .and_then(|body| body.get("simulated"))
                .and_then(Value::as_bool),
            Some(true)
        );
    }

    #[test]
    fn an_unreadable_pressure_document_is_reported_with_its_reason_not_as_zero() {
        let tree = TempTree::new("main-unknown");
        let reading = Poller::new(tree.at("absent.pressure")).poll();
        let config = Config::defaults();
        let store = Store::new(90);
        let would = Would::Hold {
            why: report::Why::NoCandidates,
        };
        let reply = status_reply(&config, &reading, &store, &would, 0);
        assert_eq!(
            reply.get("psi").and_then(|body| body.get("v")),
            Some(&Value::Null),
            "unknown is not 0.00"
        );
        assert_eq!(
            reply
                .get("psi")
                .and_then(|body| body.get("reason"))
                .and_then(Value::as_str),
            Some("read")
        );
        assert_eq!(reply.get("mode").and_then(Value::as_str), Some("report"));
    }

    #[test]
    fn candidates_lists_the_order_and_the_decision_it_implies() {
        let tree = TempTree::new("main-candidates");
        std::fs::create_dir_all(tree.at("dev-gates.slice/a.scope")).expect("a");
        std::fs::create_dir_all(tree.at("dev-gates.slice/b.scope")).expect("b");
        let mut config = Config::defaults();
        config.victim_rule = VictimRule::Refault;
        config.static_units = vec![
            static_unit("a", "dev-gates.slice/a.scope"),
            static_unit("b", "dev-gates.slice/b.scope"),
        ];
        let store = store_for(&config, tree.path(), 0).expect("resolves");
        let would = report::choose_victim(
            &config,
            &store,
            ParkingState {
                parked: 0,
                candidates: 2,
            },
            0,
            &|_| None,
        );
        let reply = candidates_reply(&config, &store, &would, 0);
        assert_eq!(
            reply.get("order").and_then(Value::as_array).map(<[_]>::len),
            Some(2)
        );
        assert_eq!(
            reply.get("rule").and_then(Value::as_str),
            Some("newest"),
            "the rule it can honour"
        );
        assert_eq!(
            reply.get("rule_reason").and_then(Value::as_str),
            Some("rule-unknown"),
            "and says so"
        );
        assert_eq!(
            reply
                .get("would")
                .and_then(|body| body.get("action"))
                .and_then(Value::as_str),
            Some("would-freeze")
        );
    }

    #[test]
    fn a_missing_journal_is_an_empty_page_and_reading_it_writes_nothing() {
        let tree = TempTree::new("main-events");
        let mut config = Config::defaults();
        config.state_dir = tree.path().join("run");
        let args = Args {
            verb: Verb::Events,
            ..Args::defaults()
        };
        let _ = &args;
        let reply = events_reply("boot-x", Vec::new(), false);
        assert_eq!(
            reply.get("rows").and_then(Value::as_array).map(<[_]>::len),
            Some(0)
        );
        // The verb's real path, with no journal on disk: an empty page, exit 0.
        events(&config, &args).expect("an absent journal is an empty page");
        assert!(
            !config.state_dir.exists(),
            "reading a journal creates nothing"
        );
    }
}
