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
        Verb::Serve => serve(&config, args),
    }
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
    let current = config.state_dir.join(events::JOURNAL_FILE);
    let rotated = config.state_dir.join(events::ROTATED_FILE);
    let file = if current.is_file() {
        current
    } else if rotated.is_file() {
        rotated
    } else {
        // No journal is an empty page with a false `truncated`, not an error: a
        // guard that has not run yet has recorded nothing, which is a fact.
        println!(
            "{}",
            daemon::events_reply(&boot_id, Vec::new(), false).to_json()
        );
        return Ok(());
    };
    let text = std::fs::read_to_string(&file)
        .map_err(|error| Fatal::unknown(format!("cannot read {}: {error}", file.display())))?;
    let mut rows = Vec::new();
    let mut skipped = 0u64;
    let mark = i64::try_from(args.since_seq).unwrap_or(i64::MAX);
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match Value::parse(line) {
            Ok(value) => {
                let seq = value.get("seq").and_then(Value::as_i64).unwrap_or(0);
                if seq > mark {
                    rows.push(value);
                }
            }
            // A torn line at a rotation is a fact about the file, not a reason to
            // call the whole journal unreadable; it is counted, never served.
            Err(_) => skipped += 1,
        }
    }
    println!(
        "{}",
        daemon::events_reply(&boot_id, rows, skipped > 0).to_json()
    );
    Ok(())
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
    let clock: Arc<dyn Clock> = Arc::new(SystemClock::new());
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
        std::env::var(activation::LISTEN_FDNAMES).ok().as_deref(),
    )
    .map_err(|error| Fatal::usage(format!("socket activation: {error}")))?;
    let endpoints = match announced {
        Some(plan) => {
            let adopted = activation::adopt(&plan)
                .map_err(|error| Fatal::usage(format!("socket activation: {error}")))?;
            let registrant = adopted
                .registrant
                .ok_or_else(|| Fatal::usage("systemd announced no registrant socket"))?;
            let reader = adopted
                .reader
                .ok_or_else(|| Fatal::usage("systemd announced no reader socket"))?;
            daemon::Endpoint { registrant, reader }
        }
        None => {
            // A foreground run: the paths come from the flags, the daemon's own
            // defaults otherwise.
            let registrant = cli::registrant_path(args);
            let reader = cli::reader_path(args);
            daemon::bind(&registrant, &reader).map_err(Fatal::usage)?
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
mod tests {
    use super::*;
    use thrashguard::config::{StaticUnit, VictimRule};
    use thrashguard::json::Value;
    use thrashguard::psi::Poller;
    use thrashguard::report::{self, ParkingState, Would};
    use thrashguard::testutil::TempTree;
    use thrashguard::units::Store;

    fn static_unit(name: &str, cgroup: &str) -> StaticUnit {
        StaticUnit {
            name: name.to_string(),
            cgroup: cgroup.to_string(),
        }
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
