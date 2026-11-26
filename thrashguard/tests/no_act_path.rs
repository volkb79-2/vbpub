//! The mechanical no-act-path proof (design §5 row P1, §2 D6, TG-1).
//!
//! P1 `observe` compiles **no act path**. This test makes that claim falsifiable
//! with a lexical scan for known cgroup-control names, process/signal entry
//! points, and file-writing APIs outside the journal. The fixture helper is
//! exempt only after both crate entry points prove it is behind `cfg(test)`.
//!
//! It reads files, so it never touches the host: `CARGO_MANIFEST_DIR` is the
//! crate, and the set of files it inspects is discovered, not listed — a new
//! module cannot be added without being covered.

use std::path::{Path, PathBuf};

/// Control-file names a P1 build must never mention in executable code.
/// They appear in prose (comments and doc comments) on purpose: the design's
/// vocabulary has to be nameable to be discussed.
const FORBIDDEN_TARGETS: [&str; 4] = [
    "cgroup.freeze",
    "cgroup.events",
    "memory.reclaim",
    "memory.max",
];

/// Signal and kill entry points, which P1 also must not reach.
const FORBIDDEN_CALLS: [&str; 7] = [
    "libc::kill",
    "nix::sys::signal",
    "Command::new(",
    "process::Command",
    "std::process::Command",
    "process::abort",
    "std::process::abort",
];

/// The only release source file allowed to write anything at all.
const WRITERS: [&str; 1] = ["src/events.rs"];

/// Write APIs. A cgroup write can only exist if one of these is called from a
/// file that is not the journal.
const WRITE_CALLS: [&str; 7] = [
    "fs::write",
    "File::create",
    "File::options",
    "OpenOptions::new",
    "fs::remove_file",
    "fs::remove_dir",
    "fs::rename",
];

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn sources(dir: &Path, into: &mut Vec<PathBuf>) {
    let entries = std::fs::read_dir(dir)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", dir.display()));
    for entry in entries {
        let path = entry.expect("a directory entry").path();
        if path.is_dir() {
            sources(&path, into);
        } else if path.extension().and_then(|extension| extension.to_str()) == Some("rs") {
            into.push(path);
        }
    }
}

/// Remove Rust comments while preserving strings and line numbers.
///
/// This is deliberately a lexical approximation, not a Rust parser. It handles
/// ordinary/raw strings, line comments, and nested block comments so `//` inside
/// a URL remains code and comment text cannot trigger the boundary checks.
fn code_of(source: &str) -> String {
    let mut chars: Vec<char> = source.chars().collect();
    let mut index = 0;
    let mut in_string = false;
    let mut escaped = false;
    let mut raw_hashes = None;
    let mut block_depth = 0usize;

    while index < chars.len() {
        if let Some(hashes) = raw_hashes {
            if chars[index] == '"' {
                let mut end = index + 1;
                while end < chars.len() && chars[end] == '#' {
                    end += 1;
                }
                if end - (index + 1) == hashes {
                    raw_hashes = None;
                    index = end;
                    continue;
                }
            }
            index += 1;
            continue;
        }

        if in_string {
            if escaped {
                escaped = false;
            } else if chars[index] == '\\' {
                escaped = true;
            } else if chars[index] == '"' {
                in_string = false;
            }
            index += 1;
            continue;
        }

        if block_depth > 0 {
            if index + 1 < chars.len() && chars[index] == '/' && chars[index + 1] == '*' {
                chars[index] = ' ';
                chars[index + 1] = ' ';
                block_depth += 1;
                index += 2;
            } else if index + 1 < chars.len() && chars[index] == '*' && chars[index + 1] == '/' {
                chars[index] = ' ';
                chars[index + 1] = ' ';
                block_depth -= 1;
                index += 2;
            } else {
                if chars[index] != '\n' && chars[index] != '\r' {
                    chars[index] = ' ';
                }
                index += 1;
            }
            continue;
        }

        let raw_prefix = if chars[index] == 'r' {
            Some(index)
        } else if chars[index] == 'b' && index + 1 < chars.len() && chars[index + 1] == 'r' {
            Some(index + 1)
        } else {
            None
        };
        if let Some(prefix) = raw_prefix {
            let mut quote = prefix + 1;
            while quote < chars.len() && chars[quote] == '#' {
                quote += 1;
            }
            if quote < chars.len() && chars[quote] == '"' {
                raw_hashes = Some(quote - (prefix + 1));
                index = quote + 1;
                continue;
            }
        }

        if chars[index] == '"' {
            in_string = true;
            index += 1;
        } else if index + 1 < chars.len() && chars[index] == '/' && chars[index + 1] == '/' {
            while index < chars.len() && chars[index] != '\n' {
                chars[index] = ' ';
                index += 1;
            }
        } else if index + 1 < chars.len() && chars[index] == '/' && chars[index + 1] == '*' {
            chars[index] = ' ';
            chars[index + 1] = ' ';
            block_depth = 1;
            index += 2;
        } else {
            index += 1;
        }
    }

    chars.into_iter().collect()
}

fn testutil_is_gated(project: &Path) -> bool {
    let lib = std::fs::read_to_string(project.join("src/lib.rs")).expect("lib.rs is readable");
    let main = std::fs::read_to_string(project.join("src/main.rs")).expect("main.rs is readable");
    code_of(&lib).contains("#[cfg(test)]\n#[doc(hidden)]\npub mod testutil;")
        && code_of(&main).contains("#[cfg(test)]\n#[path = \"testutil.rs\"]\nmod testutil;")
}

fn relative(project: &Path, path: &Path) -> String {
    path.strip_prefix(project)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

#[test]
fn no_source_file_reaches_a_cgroup_control_file_or_a_signal() {
    let project = manifest_dir();
    let mut files = Vec::new();
    sources(&project.join("src"), &mut files);
    assert!(
        !files.is_empty(),
        "no source files found under {}",
        project.join("src").display()
    );
    assert!(
        testutil_is_gated(&project),
        "src/testutil.rs must be test-only in both the library and binary"
    );

    let mut problems = Vec::new();
    for file in &files {
        let name = relative(&project, file);
        let text = std::fs::read_to_string(file)
            .unwrap_or_else(|error| panic!("cannot read {name}: {error}"));
        let code_text = code_of(&text);
        for (index, line) in code_text.lines().enumerate() {
            let code = line;
            for target in FORBIDDEN_TARGETS {
                if code.contains(target) {
                    problems.push(format!(
                        "{name}:{}: {target} appears in executable code",
                        index + 1
                    ));
                }
            }
            for call in FORBIDDEN_CALLS {
                if code.contains(call) {
                    problems.push(format!(
                        "{name}:{}: {call} appears in executable code",
                        index + 1
                    ));
                }
            }
            for write in WRITE_CALLS {
                if code.contains(write)
                    && !WRITERS.contains(&name.as_str())
                    && name != "src/testutil.rs"
                {
                    problems.push(format!(
                        "{name}:{}: {write} writes a file, and only {WRITERS:?} may write",
                        index + 1
                    ));
                }
            }
        }
    }
    assert!(
        problems.is_empty(),
        "the observe cut must compile no act path:\n{}",
        problems.join("\n")
    );
}

#[test]
fn the_journal_is_the_only_release_write_surface_and_fixtures_are_test_only() {
    let project = manifest_dir();
    for writer in WRITERS {
        let path = project.join(writer);
        assert!(
            path.is_file(),
            "{writer} is declared a writer but does not exist"
        );
        let text = std::fs::read_to_string(&path).expect("a declared writer is readable");
        assert!(
            WRITE_CALLS.iter().any(|call| text.contains(call)),
            "{writer} is declared a writer but writes nothing"
        );
    }
    assert!(testutil_is_gated(&project));
}

#[test]
fn comment_markers_inside_regular_and_raw_strings_remain_executable_text() {
    let code = code_of(
        "let url = \"scheme://cgroup.freeze\"; // cgroup.events\nlet raw = r#\"https://memory.reclaim\"#; /* memory.max */\n",
    );
    assert!(code.contains("scheme://cgroup.freeze"));
    assert!(code.contains("https://memory.reclaim"));
    assert!(!code.contains("cgroup.events"));
    assert!(!code.contains("memory.max"));
}

#[test]
fn process_spawn_and_file_options_patterns_are_in_the_lexical_boundary() {
    let code = code_of(
        "let _ = std::process::Command::new(\"echo\"); let _ = std::fs::File::options(); // ignored\n",
    );
    assert!(FORBIDDEN_CALLS.iter().any(|call| code.contains(call)));
    assert!(WRITE_CALLS.iter().any(|call| code.contains(call)));
}

#[test]
fn the_service_write_path_is_created_by_tmpfiles_on_each_boot() {
    let project = manifest_dir();
    let service = std::fs::read_to_string(
        project.join("deploy/systemd/thrashguard.service"),
    )
    .expect("service unit is readable");
    let tmpfiles = std::fs::read_to_string(project.join("deploy/tmpfiles.d/thrashguard.conf"))
        .expect("tmpfiles declaration is readable");
    assert!(!service.contains("github.com/example"));
    assert!(service
        .lines()
        .any(|line| line.starts_with("After=systemd-tmpfiles-setup.service")));
    assert!(service
        .lines()
        .any(|line| line.trim() == "ReadWritePaths=/run/thrashguard"));
    assert!(tmpfiles
        .lines()
        .any(|line| line.starts_with("d /run/thrashguard 0755 ")));
}

#[test]
fn the_binary_and_the_library_refuse_enforce_with_one_line() {
    // The promise is user-visible, so it is asserted from outside the unit tests:
    // the crate-level constant exists and names the cut, and `main` uses it rather
    // than inventing a second sentence.
    assert!(thrashguard::ENFORCE_REFUSAL.starts_with("mode=enforce is not available in this build"));
    assert_eq!(thrashguard::ENFORCE_REFUSAL.lines().count(), 1);
    let main =
        std::fs::read_to_string(manifest_dir().join("src/main.rs")).expect("main.rs is readable");
    assert!(
        main.contains("check_for_run"),
        "the run boundary must call Config::check_for_run, not re-implement the refusal"
    );
}
