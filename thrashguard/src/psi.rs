//! PSI polling: the trigger is the host `memory` `full` row and nothing else.
//!
//! Design §2: only host-wide memory PSI `full` triggers (time lost to reclaim or
//! swap, i.e. "heavy swapping"); CPU and IO pressure never trigger and a
//! per-cgroup `memory.pressure` is reported, never acted on. Missing,
//! unparseable or reset counters are **`unknown`, not zero** — the design states
//! that rule explicitly, and it is the reason this module has three states
//! rather than a number and a default.
//!
//! The pressure path is injectable (design §5, D9): tests read a fake file in a
//! temp tree and never the real `/proc/pressure/memory`.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// `avg10` is carried as integer hundredths (`avg10 = 3.42` is `342`) so no
/// comparison in this crate depends on floating-point behaviour.
pub const HUNDREDTHS_PER_POINT: u32 = 100;

/// One PSI row: the kernel's `avg10`, `avg60`, `avg300` and `total` fields.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Row {
    /// `avg10` in hundredths of a percentage point.
    pub avg10: u32,
    /// `avg60` in hundredths of a percentage point.
    pub avg60: u32,
    /// `avg300` in hundredths of a percentage point.
    pub avg300: u32,
    /// `total` in microseconds of stalled time.
    pub total_us: u64,
}

/// The whole `memory` PSI document.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Pressure {
    /// The `some` row: at least one task stalled.
    pub some: Row,
    /// The `full` row: every task stalled in a relevant window.
    pub full: Row,
}

impl Pressure {
    /// The `full` row's `avg10`, the only field the trigger reads.
    pub fn full_avg10(&self) -> u32 {
        self.full.avg10
    }
}

/// Why a pressure reading is not a number.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Unreadable {
    /// The file could not be read at all.
    Read {
        /// The injected path.
        path: PathBuf,
        /// The underlying reason, rendered.
        detail: String,
    },
    /// The file was read but one of the four fields was missing or not a number.
    Malformed(String),
    /// `total` went backwards since the previous reading: the counters were reset.
    Reset {
        /// The row that moved backwards.
        row: &'static str,
        /// The previous total.
        previous_us: u64,
        /// The current total.
        current_us: u64,
    },
}

/// A pressure reading: a measured value, or the fact that it is unknown.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Reading {
    /// The host memory `full avg10`, in hundredths of a point.
    Known(Pressure),
    /// Pressure could not be determined; nothing is frozen and what was frozen is thawed.
    Unknown(Unreadable),
}

impl Reading {
    /// The `avg10` in hundredths when the reading is known.
    pub fn avg10(&self) -> Option<u32> {
        match self {
            Self::Known(pressure) => Some(pressure.full_avg10()),
            Self::Unknown(_) => None,
        }
    }

    /// The unknown reason, when the reading is unknown.
    pub fn unknown(&self) -> Option<&Unreadable> {
        match self {
            Self::Unknown(reason) => Some(reason),
            Self::Known(_) => None,
        }
    }
}

/// A poller bound to one injected pressure path.
#[derive(Debug)]
pub struct Poller {
    path: PathBuf,
    previous: Option<Pressure>,
}

impl Poller {
    /// A poller reading `path` (production: `/proc/pressure/memory`).
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into(), previous: None }
    }

    /// The injected path this poller reads.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Read the document once and decide whether it is a number.
    ///
    /// A reset is judged against the previous *successful* reading in this
    /// process; an unreadable or malformed document does not clear that baseline,
    /// so a transient garbage read cannot hide a real counter reset.
    pub fn poll(&mut self) -> Reading {
        let text = match fs::read_to_string(&self.path) {
            Ok(text) => text,
            Err(error) => {
                return Reading::Unknown(Unreadable::Read {
                    path: self.path.clone(),
                    detail: io_error_text(&error),
                })
            }
        };
        let pressure = match parse(&text) {
            Ok(pressure) => pressure,
            Err(detail) => return Reading::Unknown(Unreadable::Malformed(detail)),
        };
        if let Some(previous) = self.previous {
            for (row, previous_us, current_us) in [
                ("some", previous.some.total_us, pressure.some.total_us),
                ("full", previous.full.total_us, pressure.full.total_us),
            ] {
                if current_us < previous_us {
                    return Reading::Unknown(Unreadable::Reset { row, previous_us, current_us });
                }
            }
        }
        self.previous = Some(pressure);
        Reading::Known(pressure)
    }
}

fn io_error_text(error: &io::Error) -> String {
    match error.kind() {
        io::ErrorKind::NotFound => "not present".to_string(),
        io::ErrorKind::PermissionDenied => "not readable by this process".to_string(),
        _ => error.to_string(),
    }
}

/// Parse a `memory.pressure` document.
///
/// Both rows are required: the `full` row is the trigger and the `some` row is
/// reported, so a file with only one of them is not a measurement of this host.
pub fn parse(text: &str) -> Result<Pressure, String> {
    let mut some = None;
    let mut full = None;
    for (index, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let mut words = line.split_whitespace();
        let Some(kind) = words.next() else { continue };
        let mut row = Row::default();
        let mut seen_avg10 = false;
        let mut seen_total = false;
        for word in words {
            let Some((key, value)) = word.split_once('=') else {
                return Err(format!("line {}: field {word:?} has no '='", index + 1));
            };
            match key {
                "avg10" => {
                    row.avg10 = parse_avg10(value).map_err(|why| format!("line {}: avg10 {why}", index + 1))?;
                    seen_avg10 = true;
                }
                "avg60" => {
                    row.avg60 = parse_avg10(value).map_err(|why| format!("line {}: avg60 {why}", index + 1))?;
                }
                "avg300" => {
                    row.avg300 = parse_avg10(value).map_err(|why| format!("line {}: avg300 {why}", index + 1))?;
                }
                "total" => {
                    row.total_us = value
                        .parse::<u64>()
                        .map_err(|_| format!("line {}: total {value:?} is not an integer", index + 1))?;
                    seen_total = true;
                }
                other => return Err(format!("line {}: unknown field {other:?}", index + 1)),
            }
        }
        if !seen_avg10 || !seen_total {
            return Err(format!("line {}: row {kind:?} lacks avg10 or total", index + 1));
        }
        match kind {
            "some" => some = Some(row),
            "full" => full = Some(row),
            other => return Err(format!("line {}: unknown row {other:?}", index + 1)),
        }
    }
    let some = some.ok_or_else(|| "the `some` row is missing".to_string())?;
    let full = full.ok_or_else(|| "the `full` row is missing".to_string())?;
    Ok(Pressure { some, full })
}

fn parse_avg10(text: &str) -> Result<u32, String> {
    let (whole, fraction) = match text.split_once('.') {
        Some((whole, fraction)) => (whole, fraction),
        None => (text, ""),
    };
    if whole.is_empty() && fraction.is_empty() {
        return Err(format!("{text:?} is empty"));
    }
    if fraction.len() > 2 && fraction[2..].bytes().any(|b| b != b'0') {
        return Err(format!("{text:?} is more precise than hundredths"));
    }
    let whole: u32 = whole
        .parse()
        .map_err(|_| format!("{text:?} is not a decimal number"))?;
    let fraction: u32 = match fraction.get(..2) {
        Some(digits) if !digits.is_empty() => digits
            .parse()
            .map_err(|_| format!("{text:?} is not a decimal number"))?,
        _ => fraction.parse().unwrap_or(0),
    };
    Ok(whole.saturating_mul(HUNDREDTHS_PER_POINT).saturating_add(fraction))
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = "some avg10=1.02 avg60=0.53 avg300=0.20 total=1234\nfull avg10=3.42 avg60=1.10 avg300=0.40 total=567\n";

    #[test]
    fn parses_both_rows_into_hundredths() {
        let pressure = parse(GOOD).expect("parses");
        assert_eq!(pressure.some.avg10, 102);
        assert_eq!(pressure.full.avg10, 342);
        assert_eq!(pressure.full_avg10(), 342);
        assert_eq!(pressure.full.total_us, 567);
    }

    #[test]
    fn a_missing_or_unknown_row_is_malformed_not_zero() {
        assert!(parse("some avg10=1.00 avg60=0.50 avg300=0.10 total=1\n").is_err());
        assert!(parse("full avg10=1.00 avg60=0.50 avg300=0.10 total=1\n").is_err());
        assert!(parse("some avg10=1.00 avg60=0.50 avg300=0.10 total=1\nfull avg10=1.00 total=1\n").is_ok());
        assert!(parse("cpu avg10=1.00 avg60=0.50 avg300=0.10 total=1\n").is_err());
        assert!(parse("some avg10=1.00 avg60=0.50 avg300=0.10\n").is_err());
        assert!(parse("some avg10=x avg60=0.50 avg300=0.10 total=1\nfull avg10=1 total=1\n").is_err());
    }

    #[test]
    fn an_unreadable_file_and_a_reset_are_both_unknown() {
        let mut poller = Poller::new("/nonexistent/thrashguard/pressure");
        match poller.poll() {
            Reading::Unknown(Unreadable::Read { .. }) => {}
            other => panic!("expected a read refusal, got {other:?}"),
        }
        let dir = crate::testutil::TempTree::new("psi");
        let path = dir.write("memory", GOOD);
        let mut poller = Poller::new(&path);
        assert!(matches!(poller.poll(), Reading::Known(_)));
        dir.write("memory", "some avg10=1.00 avg60=0.50 avg300=0.10 total=2000\nfull avg10=1.00 avg60=0.50 avg300=0.10 total=8\n");
        match poller.poll() {
            Reading::Unknown(Unreadable::Reset { row, previous_us, current_us }) => {
                assert_eq!((row, previous_us, current_us), ("full", 567, 8));
            }
            other => panic!("expected a reset, got {other:?}"),
        }
    }
}
