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
use std::io::Read;
use std::process::ExitCode;

use std::sync::Arc;

use thrashguard::activation;
use thrashguard::cli::{self, Args, Verb};
use thrashguard::clock::{Clock, SystemClock};
use thrashguard::config::{self, Config};
use thrashguard::daemon::{self, Core};
use thrashguard::events;
use thrashguard::json::Value;
use thrashguard::psi::Poller;
use thrashguard::report::{self, ParkingState};
use thrashguard::socket::{self, Role};
use thrashguard::units::Store;
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
    let config = load_config(args, Path::new(config::DEFAULT_CONFIG))?;
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
        Verb::Serve => serve(&config, args),
    }
}

/// Load the explicit config path, or the package's documented host default.
fn load_config(args: &Args, default_path: &Path) -> Result<Config, Fatal> {
    let path = args.config.as_deref().unwrap_or(default_path);
    config::load(path).map_err(|error| {
        Fatal::usage(format!(
            "cannot load the configuration {}: {error}",
            path.display()
        ))
    })
}

/// The candidate store for this configuration, with every `[[static_unit]]`
/// resolved by the one resolver the daemon uses too ([`daemon::store_for`]).
fn store_for(config: &Config, root: &Path) -> Result<Store, Fatal> {
    daemon::store_for(config, root, 0).map_err(Fatal::unknown)
}

fn status(config: &Config, args: &Args) -> Result<(), Fatal> {
    let reading = Poller::new(&args.pressure_path).poll();
    let store = store_for(config, &args.cgroup_root)?;
    let would = report::choose_victim(
        config,
        &store,
        ParkingState {
            parked: 0,
            candidates: store.len(),
        },
        0,
        &|_| None,
    );
    println!(
        "{}",
        daemon::status_reply(config, &reading, &store, &would, 0).to_json()
    );
    Ok(())
}

fn candidates(config: &Config, args: &Args) -> Result<(), Fatal> {
    let store = store_for(config, &args.cgroup_root)?;
    let would = report::choose_victim(
        config,
        &store,
        ParkingState {
            parked: 0,
            candidates: store.len(),
        },
        0,
        &|_| None,
    );
    println!(
        "{}",
        daemon::candidates_reply(config, &store, &would, 0).to_json()
    );
    Ok(())
}

/// `events` without a socket reads the journal the daemon wrote, paged by
/// `--since-seq`. Reading it writes nothing.
fn events(config: &Config, args: &Args) -> Result<(), Fatal> {
    let boot_id = daemon::boot_id();
    let reply = local_events_reply(&config.state_dir, &boot_id, args.since_seq)
        .map_err(Fatal::unknown)?;
    println!("{}", reply.to_json());
    Ok(())
}

/// Read the rotated and current journal generations into one bounded page.
fn local_events_reply(state_dir: &Path, boot_id: &str, since_seq: u64) -> Result<Value, String> {
    let mut selected = Vec::<(u64, Value)>::new();
    let mut damaged = false;
    for name in [events::ROTATED_FILE, events::JOURNAL_FILE] {
        let path = state_dir.join(name);
        let metadata = match std::fs::metadata(&path) {
            Ok(metadata) if metadata.is_file() => metadata,
            Ok(_) => return Err(format!("{} is not a journal file", path.display())),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("cannot inspect {}: {error}", path.display())),
        };
        if metadata.len() > events::MAX_JOURNAL_FILE_BYTES {
            return Err(format!(
                "{} is {} bytes, above the {}-byte journal-file bound",
                path.display(),
                metadata.len(),
                events::MAX_JOURNAL_FILE_BYTES
            ));
        }
        let mut text = String::new();
        std::fs::File::open(&path)
            .map_err(|error| format!("cannot open {}: {error}", path.display()))?
            .take(events::MAX_JOURNAL_FILE_BYTES + 1)
            .read_to_string(&mut text)
            .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
        if text.len() as u64 > events::MAX_JOURNAL_FILE_BYTES {
            return Err(format!(
                "{} grew beyond the {}-byte journal-file bound while reading",
                path.display(),
                events::MAX_JOURNAL_FILE_BYTES
            ));
        }
        for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
            match Value::parse(line) {
                Ok(value) if value.get("boot_id").and_then(Value::as_str) == Some(boot_id) => {
                    let Some(seq) = value
                        .get("seq")
                        .and_then(Value::as_i64)
                        .and_then(|seq| u64::try_from(seq).ok())
                    else {
                        damaged = true;
                        continue;
                    };
                    if seq > since_seq {
                        selected.push((seq, value));
                    }
                }
                Ok(_) => {}
                // A torn or malformed line may contain a missing sequence.
                Err(_) => damaged = true,
            }
        }
    }

    selected.sort_by_key(|(seq, _)| *seq);
    let mut duplicate = false;
    selected.dedup_by(|next, prior| {
        if next.0 == prior.0 {
            duplicate = true;
            true
        } else {
            false
        }
    });
    let mut truncated = damaged || duplicate;
    let mut expected = since_seq.saturating_add(1);
    for (seq, _) in &selected {
        if *seq > expected {
            truncated = true;
        }
        expected = seq.saturating_add(1);
    }

    let mut page = Vec::new();
    let mut limited = false;
    for (_, row) in selected {
        if page.len() == events::RECENT_ROWS {
            limited = true;
            break;
        }
        let mut candidate = page.clone();
        candidate.push(row.clone());
        if daemon::events_reply(boot_id, candidate, truncated)
            .to_json()
            .len()
            + 1
            > thrashguard::json::MAX_DOCUMENT_BYTES
        {
            if page.is_empty() {
                return Err(format!(
                    "the next event row exceeds the {}-byte reply bound",
                    thrashguard::json::MAX_DOCUMENT_BYTES
                ));
            }
            limited = true;
            break;
        }
        page.push(row);
    }
    truncated |= limited;

    loop {
        let reply = daemon::events_reply(boot_id, page.clone(), truncated);
        if reply.to_json().len() < thrashguard::json::MAX_DOCUMENT_BYTES {
            return Ok(reply);
        }
        if page.pop().is_none() {
            return Err("the empty events reply exceeds its document bound".to_string());
        }
        truncated = true;
    }
}

/// `serve`: the tick loop and the two sockets (design §2, §3 D7, §5 row P1).
///
/// `mode = off` exits without serving (an operator's explicit "do nothing").
/// The sockets are **adopted** when systemd activated us, and bound directly only
/// for a foreground run — a daemon that binds its own path cannot set the
/// `root:thrashguard-{registrant,reader}` group the design requires.
fn serve(config: &Config, args: &Args) -> Result<(), Fatal> {
    if config.mode == config::Mode::Off {
        // Nothing to do is a fact, not a failure: exit 0 with one line.
        println!("thrashguard: mode=off, nothing to serve");
        return Ok(());
    }
    let clock: Arc<dyn Clock> = Arc::new(
        SystemClock::new()
            .map_err(|error| Fatal::usage(format!("cannot read boot-relative uptime: {error}")))?,
    );
    let boot = daemon::boot_id();
    let core = Core::new(
        config.clone(),
        clock,
        &args.pressure_path,
        &args.cgroup_root,
        &boot,
    )
    .map_err(Fatal::usage)?;
    let announced = activation::plan(
        std::env::var(activation::LISTEN_FDS).ok().as_deref(),
        std::env::var(activation::LISTEN_PID).ok().as_deref(),
        std::env::var(activation::LISTEN_FDNAMES).ok().as_deref(),
        config.registrant,
    )
    .map_err(|error| Fatal::usage(format!("socket activation: {error}")))?;
    let endpoints = match announced {
        Some(plan) => {
            let adopted = activation::adopt(plan)
                .map_err(|error| Fatal::usage(format!("socket activation: {error}")))?;
            let reader = adopted
                .reader
                .ok_or_else(|| Fatal::usage("systemd announced no reader socket"))?;
            if config.registrant && adopted.registrant.is_none() {
                return Err(Fatal::usage("systemd announced no registrant socket"));
            }
            daemon::Endpoint {
                registrant: adopted.registrant,
                reader,
            }
        }
        None => {
            // A foreground run: the paths come from the flags, the daemon's own
            // defaults otherwise.
            let registrant = config
                .registrant
                .then(|| cli::registrant_path(args));
            let reader = cli::reader_path(args);
            daemon::bind(registrant.as_deref(), &reader).map_err(Fatal::usage)?
        }
    };
    let tick_s = config.tick_s;
    daemon::serve(core, &endpoints, tick_s).map_err(|error| Fatal::unknown(error.to_string()))
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
#[path = "testutil.rs"]
mod testutil;

#[cfg(test)]
mod tests {
    use super::*;
    use thrashguard::config::{StaticUnit, VictimRule};
    use thrashguard::json::Value;
    use thrashguard::psi::Poller;
    use thrashguard::report::{self, ParkingState, Would};
    use crate::testutil::TempTree;
    use thrashguard::units::Store;

    #[test]
    fn an_omitted_config_reads_the_injected_default_file() {
        let tree = TempTree::new("main-default-config");
        let path = tree.write("thrashguard.toml", "mode = \"off\"\ntrigger_avg10 = 4.50\n");
        let args = Args {
            verb: Verb::Status,
            ..Args::defaults()
        };
        let loaded = load_config(&args, &path).expect("default configuration file");
        assert_eq!(loaded.mode, config::Mode::Off);
        assert_eq!(loaded.trigger_avg10, 450);
    }

    fn static_unit(name: &str, cgroup: &str) -> StaticUnit {
        StaticUnit {
            name: name.to_string(),
            cgroup: cgroup.to_string(),
        }
    }

    fn local_event_line(boot_id: &str, seq: u64, detail: &str) -> String {
        Value::Object(vec![
            ("boot_id".to_string(), Value::String(boot_id.to_string())),
            ("seq".to_string(), Value::Integer(seq as i64)),
            ("event".to_string(), Value::String("x".to_string())),
            ("detail".to_string(), Value::String(detail.to_string())),
        ])
        .to_json()
    }

    fn would_for(config: &Config, store: &Store) -> Would {
        report::choose_victim(
            config,
            store,
            ParkingState {
                parked: 0,
                candidates: store.len(),
            },
            0,
            &|_| None,
        )
    }

    #[test]
    fn a_static_unit_that_cannot_resolve_is_a_refusal_not_a_silent_candidate() {
        let tree = TempTree::new("main-static");
        let mut config = Config::defaults();
        config.static_units = vec![static_unit("missing", "dev-gates.slice/nothing.scope")];
        let error = store_for(&config, tree.path()).expect_err("cannot resolve");
        assert_eq!(error.code, 3, "an unreadable fact is not a usage error");
        assert!(error.message.contains("missing"), "{}", error.message);
        // The same unit, once the cgroup exists, is a candidate.
        std::fs::create_dir_all(tree.at("dev-gates.slice/nothing.scope")).expect("create");
        let store = store_for(&config, tree.path()).expect("resolves");
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
        let store = store_for(&config, tree.path()).expect("resolves");
        let reply = daemon::status_reply(&config, &reading, &store, &would_for(&config, &store), 0);
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
        let store = store_for(&config, tree.path()).expect("resolves");
        let reply = daemon::status_reply(&config, &reading, &store, &would_for(&config, &store), 0);
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
            Some(true),
            "P1 froze nothing, and the document says so"
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
        let reply = daemon::status_reply(&config, &reading, &store, &would, 0);
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
        let store = store_for(&config, tree.path()).expect("resolves");
        let reply = daemon::candidates_reply(&config, &store, &would_for(&config, &store), 0);
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
        let reply = daemon::events_reply("boot-x", Vec::new(), false);
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

    #[test]
    fn local_events_reads_both_generations_and_marks_a_lost_prefix() {
        let tree = TempTree::new("main-events-rotated");
        tree.write(
            "run/events.jsonl.1",
            format!(
                "{}\n{}\n",
                local_event_line("boot-x", 3, ""),
                local_event_line("boot-x", 4, "")
            )
            .as_str(),
        );
        tree.write(
            "run/events.jsonl",
            format!(
                "{}\n{}\n",
                local_event_line("boot-x", 5, ""),
                local_event_line("boot-x", 6, "")
            )
            .as_str(),
        );

        let reply = local_events_reply(&tree.path().join("run"), "boot-x", 0)
            .expect("bounded combined page");
        let seqs: Vec<i64> = reply
            .get("rows")
            .and_then(Value::as_array)
            .expect("rows")
            .iter()
            .filter_map(|row| row.get("seq").and_then(Value::as_i64))
            .collect();
        assert_eq!(seqs, vec![3, 4, 5, 6]);
        assert_eq!(reply.get("truncated").and_then(Value::as_bool), Some(true));

        let complete = local_events_reply(&tree.path().join("run"), "boot-x", 2)
            .expect("page after available prefix");
        assert_eq!(
            complete.get("truncated").and_then(Value::as_bool),
            Some(false)
        );
    }

    #[test]
    fn local_events_limits_rows_to_the_daemon_window() {
        let tree = TempTree::new("main-events-row-bound");
        let text = (1..=events::RECENT_ROWS as u64 + 1)
            .map(|seq| local_event_line("boot-x", seq, ""))
            .collect::<Vec<_>>()
            .join("\n");
        tree.write("run/events.jsonl", &text);
        let reply = local_events_reply(&tree.path().join("run"), "boot-x", 0)
            .expect("bounded rows");
        let rows = reply.get("rows").and_then(Value::as_array).expect("rows");
        assert_eq!(rows.len(), events::RECENT_ROWS);
        assert_eq!(rows[0].get("seq").and_then(Value::as_i64), Some(1));
        assert_eq!(
            rows.last().and_then(|row| row.get("seq")).and_then(Value::as_i64),
            Some(events::RECENT_ROWS as i64)
        );
        assert_eq!(reply.get("truncated").and_then(Value::as_bool), Some(true));
    }

    #[test]
    fn local_events_refuses_a_single_row_that_exceeds_the_reply_byte_bound() {
        let tree = TempTree::new("main-events-byte-bound");
        let row = local_event_line(
            "boot-x",
            1,
            &"x".repeat(thrashguard::json::MAX_DOCUMENT_BYTES - 64),
        );
        assert!(row.len() <= thrashguard::json::MAX_DOCUMENT_BYTES);
        let parsed = Value::parse(&row).expect("the journal row is within its input bound");
        assert!(
            daemon::events_reply("boot-x", vec![parsed], false)
                .to_json()
                .len()
                + 1
                > thrashguard::json::MAX_DOCUMENT_BYTES
        );
        tree.write("run/events.jsonl", &row);
        let error = local_events_reply(&tree.path().join("run"), "boot-x", 0)
            .expect_err("one oversized row is refused rather than printed unbounded");
        assert!(error.contains("reply bound"), "{error}");
    }

    #[test]
    fn serve_refuses_enforce_and_exits_cleanly_on_off() {
        // `enforce` is refused at the run boundary with one line (design §5 D6).
        let mut enforcing = Config::defaults();
        enforcing.mode = config::Mode::Enforce;
        let error = enforcing.check_for_run().expect_err("enforce is refused");
        assert_eq!(error.to_string(), thrashguard::ENFORCE_REFUSAL);
        // `mode = off` is an operator's explicit do-nothing: exit 0, one line, and
        // no socket is bound (the temp tree stays empty).
        let tree = TempTree::new("main-off");
        let mut off = Config::defaults();
        off.mode = config::Mode::Off;
        let args = Args {
            verb: Verb::Serve,
            registrant_socket: Some(tree.at("reg/thrashguard.sock")),
            reader_socket: Some(tree.at("reader/thrashguard.sock")),
            ..Args::defaults()
        };
        serve(&off, &args).expect("mode=off succeeds");
        assert!(
            !tree.at("reg/thrashguard.sock").exists(),
            "nothing was bound"
        );
    }

    #[test]
    fn a_serve_refuses_a_bad_configuration_before_it_binds_anything() {
        let tree = TempTree::new("main-bad");
        let mut config = Config::defaults();
        config.static_units = vec![static_unit("missing", "dev-gates.slice/nothing.scope")];
        let args = Args {
            verb: Verb::Serve,
            cgroup_root: tree.path().to_path_buf(),
            pressure_path: tree.at("memory.pressure"),
            registrant_socket: Some(tree.at("reg.sock")),
            reader_socket: Some(tree.at("reader.sock")),
            ..Args::defaults()
        };
        let error = serve(&config, &args).expect_err("an unresolvable static unit");
        assert_eq!(error.code, 2, "the operator must fix /etc");
        assert!(
            error.message.contains("[[static_unit]]"),
            "{}",
            error.message
        );
        assert!(!tree.at("reg.sock").exists(), "no socket was created");
    }

    #[test]
    fn the_socket_flags_select_the_paths_serve_binds() {
        let args = Args {
            verb: Verb::Serve,
            registrant_socket: Some(Path::new("/run/x/reg.sock").to_path_buf()),
            reader_socket: Some(Path::new("/run/x/reader.sock").to_path_buf()),
            ..Args::defaults()
        };
        assert_eq!(cli::registrant_path(&args), Path::new("/run/x/reg.sock"));
        assert_eq!(cli::reader_path(&args), Path::new("/run/x/reader.sock"));
        let shortest = Args {
            verb: Verb::Serve,
            ..Args::defaults()
        };
        assert_eq!(
            cli::registrant_path(&shortest),
            Path::new(socket::REGISTRANT_PATH)
        );
        assert_eq!(cli::reader_path(&shortest), Path::new(socket::READER_PATH));
    }
}
