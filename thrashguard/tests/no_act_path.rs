//! The mechanical no-act-path proof (design §5 row P1, §2 D6, TG-1).
//!
//! P1 `observe` compiles **no act path**. This test is what makes that claim
//! falsifiable instead of a sentence in a README: it reads the crate's own
//! sources and refuses an executable mention of a cgroup control file, a signal,
//! or a write API outside the two modules allowed to write (the journal and the
//! test-only fixture helper).
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
const FORBIDDEN_CALLS: [&str; 4] = [
    "libc::kill",
    "nix::sys::signal",
    "Command::new(\"kill",
    "process::abort",
];

/// The only files allowed to write anything at all.
const WRITERS: [&str; 2] = ["src/events.rs", "src/testutil.rs"];

/// Write APIs. A cgroup write can only exist if one of these is called from a
/// file that is not the journal.
const WRITE_CALLS: [&str; 6] = [
    "fs::write",
    "File::create",
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

/// The executable part of a line: everything before a `//` comment.
///
/// Comments may name `cgroup.freeze` and `memory.reclaim` freely — the design
/// has to discuss them — but an executable line may not.
fn code_of(line: &str) -> &str {
    line.split("//").next().unwrap_or("")
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

    let mut problems = Vec::new();
    for file in &files {
        let name = relative(&project, file);
        let text = std::fs::read_to_string(file)
            .unwrap_or_else(|error| panic!("cannot read {name}: {error}"));
        for (index, line) in text.lines().enumerate() {
            let code = code_of(line);
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
                if code.contains(write) && !WRITERS.contains(&name.as_str()) {
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
fn the_journal_and_the_fixture_helper_are_the_whole_write_surface() {
    // The two allowance sets are checked BOTH ways: every allowed writer exists,
    // and no other file writes. A stale allowance would silently widen the
    // surface, which is the "allowance that describes a file that is not there"
    // defect in miniature.
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
