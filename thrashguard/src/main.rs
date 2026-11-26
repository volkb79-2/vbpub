//! `thrashguard` — the daemon and its local verbs.

#![forbid(unsafe_code)]

fn main() -> std::process::ExitCode {
    let mut args = std::env::args().skip(1);
    match (args.next().as_deref(), args.next()) {
        (Some("--version" | "version"), None) => {
            println!("thrashguard {}", thrashguard::VERSION);
            std::process::ExitCode::SUCCESS
        }
        (None, _) => {
            eprintln!("usage: thrashguard --version");
            std::process::ExitCode::from(2)
        }
        (Some(unknown), _) => {
            eprintln!("thrashguard: unknown verb {unknown:?}; usage: thrashguard --version");
            std::process::ExitCode::from(2)
        }
    }
}
