//! Live hostobs client: one `current` reply and then N subscription ticks.
//!
//! Run as:
//!
//! ```text
//! watch --socket <path> --level observe|operator --ticks N \
//!       [--interval-ms M] [--selection <json>]
//! ```

use std::env;
use std::fmt;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process;
use std::thread;
use std::time::{Duration, Instant};

use hostobs_client::{
    Block, BlockStream, Client, ClientError, ClientInfo, Level, ProtocolKind, Subscription,
};

const DEFAULT_SELECTION: &str =
    r#"{"entities":[{"all":true}],"metrics":["cpu.usage_usec","mem.current","pids.current"]}"#;

#[derive(Clone, Debug, Eq, PartialEq)]
struct WatchConfig {
    socket: PathBuf,
    level: Level,
    ticks: u64,
    interval_ms: Option<u32>,
    selection: String,
}

enum RunError {
    Usage(String),
    Client(ClientError),
    Output(io::Error),
}

impl fmt::Display for RunError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RunError::Usage(message) => f.write_str(message),
            RunError::Client(error) => error.fmt(f),
            RunError::Output(error) => write!(f, "cannot write output: {error}"),
        }
    }
}

impl From<ClientError> for RunError {
    fn from(error: ClientError) -> RunError {
        match error {
            ClientError::Usage(name) => RunError::Usage(name.to_owned()),
            other => RunError::Client(other),
        }
    }
}

impl From<io::Error> for RunError {
    fn from(error: io::Error) -> RunError {
        RunError::Output(error)
    }
}

fn one_line(text: &str) -> String {
    text.replace(['\n', '\r'], " ")
}

fn parse_args(args: &[String]) -> Result<WatchConfig, String> {
    let mut socket = None;
    let mut level = None;
    let mut ticks = None;
    let mut interval_ms = None;
    let mut selection = None;
    let mut index = 0;
    while index < args.len() {
        let option = args.get(index).map(String::as_str).unwrap_or_default();
        let value = args.get(index + 1).map(String::as_str);
        match option {
            "--socket" => {
                let found = value.ok_or_else(|| String::from("--socket requires a path"))?;
                if socket.replace(PathBuf::from(found)).is_some() {
                    return Err(String::from("--socket may be given once"));
                }
            }
            "--level" => {
                let found = value.ok_or_else(|| String::from("--level requires a value"))?;
                let parsed = match found {
                    "observe" => Level::Observe,
                    "operator" => Level::Operator,
                    _ => {
                        return Err(format!(
                            "unknown level {found:?}; expected observe or operator"
                        ))
                    }
                };
                if level.replace(parsed).is_some() {
                    return Err(String::from("--level may be given once"));
                }
            }
            "--ticks" => {
                let found = value.ok_or_else(|| String::from("--ticks requires a count"))?;
                let parsed = found
                    .parse::<u64>()
                    .map_err(|_| format!("invalid tick count {found:?}"))?;
                if parsed == 0 {
                    return Err(String::from("--ticks must be greater than zero"));
                }
                if ticks.replace(parsed).is_some() {
                    return Err(String::from("--ticks may be given once"));
                }
            }
            "--interval-ms" => {
                let found = value.ok_or_else(|| String::from("--interval-ms requires a value"))?;
                let parsed = found
                    .parse::<u32>()
                    .map_err(|_| format!("invalid interval {found:?}"))?;
                if parsed == 0 {
                    return Err(String::from("--interval-ms must be greater than zero"));
                }
                if interval_ms.replace(parsed).is_some() {
                    return Err(String::from("--interval-ms may be given once"));
                }
            }
            "--selection" => {
                let found = value.ok_or_else(|| String::from("--selection requires JSON"))?;
                if selection.replace(found.to_owned()).is_some() {
                    return Err(String::from("--selection may be given once"));
                }
            }
            other => {
                return Err(format!(
                    "unknown argument {other:?}; run `watch` without arguments for usage"
                ))
            }
        }
        index += 2;
    }
    Ok(WatchConfig {
        socket: socket.ok_or_else(|| String::from("--socket is required"))?,
        level: level.ok_or_else(|| String::from("--level is required"))?,
        ticks: ticks.ok_or_else(|| String::from("--ticks is required"))?,
        interval_ms,
        selection: selection.unwrap_or_else(|| DEFAULT_SELECTION.to_owned()),
    })
}

fn current_args(selection: &str) -> String {
    format!(r#"{{"selection":{selection}}}"#)
}

fn validate_interval(interval_ms: u32, tick_ms: u32, min_interval_ms: u32) -> Result<(), String> {
    if tick_ms == 0 {
        return Err(String::from("this level does not serve subscriptions"));
    }
    let minimum = tick_ms.max(min_interval_ms);
    if interval_ms < minimum || interval_ms > 60_000 || !interval_ms.is_multiple_of(tick_ms) {
        return Err(format!(
            "interval {interval_ms} ms must be a multiple of tick_ms {tick_ms} and in {minimum}..=60000"
        ));
    }
    Ok(())
}

fn status_tick_seq(result_json: &str) -> Option<u64> {
    let tick = result_json.find("\"tick\":")?;
    let tail = result_json.get(tick..)?;
    let seq = tail.find("\"seq\":")?;
    let digits = tail.get(seq.saturating_add(6)..)?;
    let end = digits
        .find(|ch: char| !ch.is_ascii_digit())
        .unwrap_or(digits.len());
    digits.get(..end)?.parse().ok()
}

fn wait_for_tick(client: &mut Client) -> Result<(), RunError> {
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        let status = client.request("status", "{}")?;
        if status_tick_seq(status.result_json()).is_some_and(|seq| seq > 0) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(ClientError::Timeout.into());
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn name_at(names: &[String], index: u32) -> String {
    names
        .get(usize::try_from(index).unwrap_or(usize::MAX))
        .cloned()
        .unwrap_or_else(|| format!("#{index}"))
}

fn row_line(seq: u64, entity: &str, metric: &str, value: &str, quality: &str) -> String {
    format!("tick\tseq={seq}\tentity={entity}\tmetric={metric}\tvalue={value}\tquality={quality}")
}

fn update_names(block: &Block, names: &mut Vec<String>) -> Result<(), RunError> {
    if let Some(dictionary) = block.dictionary.as_ref() {
        *names = dictionary
            .entries()
            .iter()
            .enumerate()
            .map(|(index, entry)| {
                if entry.text().is_empty() {
                    format!("#{index}")
                } else {
                    entry.text().to_owned()
                }
            })
            .collect();
    } else if names.is_empty() {
        return Err(ClientError::Protocol(ProtocolKind::BadReply).into());
    }
    Ok(())
}

fn tick_lines(seq: u64, block: &Block, names: &[String]) -> Result<Vec<String>, RunError> {
    if block.rows.is_empty() || block.columns.is_empty() {
        return Ok(vec![format!(
            "tick\tseq={seq}\trows={}\tcolumns={}",
            block.rows.len(),
            block.columns.len()
        )]);
    }
    let mut lines = Vec::with_capacity(block.rows.len().saturating_mul(block.columns.len()));
    for (row_index, row) in block.rows.iter().enumerate() {
        let entity = name_at(names, row.ent);
        for column in &block.columns {
            let sample = column
                .sample(row_index)
                .map_err(|error| RunError::Client(ClientError::Wire(error)))?;
            let value = if sample.quality.is_measurement() {
                format!("{:?}", sample.value)
            } else {
                String::from("<not-measured>")
            };
            lines.push(row_line(
                seq,
                &entity,
                &name_at(names, column.metric),
                &value,
                sample.quality.name(),
            ));
        }
    }
    Ok(lines)
}

fn emit_to<W: Write>(writer: &mut W, lines: &[String]) -> Result<(), RunError> {
    for line in lines {
        writeln!(writer, "{line}")?;
    }
    Ok(())
}

fn read_current<W: Write>(
    client: &mut Client,
    config: &WatchConfig,
    writer: &mut W,
) -> Result<(), RunError> {
    let mut stream: BlockStream<'_> = client.blocks("current", &current_args(&config.selection))?;
    for block in stream.by_ref() {
        let block = block?;
        emit_to(
            writer,
            &[format!(
                "current\tseq={}\trows={}",
                block.header.seq,
                block.rows.len()
            )],
        )?;
    }
    match stream.end() {
        Some(end) if end.reason == "complete" => Ok(()),
        _ => Err(ClientError::Protocol(ProtocolKind::BadReply).into()),
    }
}

fn read_subscription<W: Write>(
    client: &mut Client,
    config: &WatchConfig,
    interval_ms: u32,
    writer: &mut W,
) -> Result<(), RunError> {
    let mut subscription: Subscription<'_> = client.subscribe(&config.selection, interval_ms)?;
    let mut names = Vec::new();
    for _ in 0..config.ticks {
        let block = match subscription.next() {
            Some(Ok(block)) => block,
            Some(Err(error)) => return Err(error.into()),
            None => return Err(ClientError::Protocol(ProtocolKind::BadReply).into()),
        };
        update_names(&block, &mut names)?;
        emit_to(writer, &tick_lines(block.header.seq, &block, &names)?)?;
    }
    subscription.close()?;
    let reason = subscription
        .end()
        .map(|end| end.reason.as_str())
        .unwrap_or("closed");
    emit_to(
        writer,
        &[format!(
            "closed\tblocks={}\treason={reason}",
            subscription.received()
        )],
    )
}

fn run<W: Write>(args: &[String], writer: &mut W) -> Result<(), RunError> {
    let config = parse_args(args).map_err(RunError::Usage)?;
    let mut client = Client::connect(
        &config.socket,
        config.level,
        ClientInfo::new("hostobs-watch"),
    )?;
    let hello = client.hello().clone();
    let interval_ms = config.interval_ms.unwrap_or(hello.limits.tick_ms);
    validate_interval(
        interval_ms,
        hello.limits.tick_ms,
        hello.limits.min_interval_ms,
    )
    .map_err(RunError::Usage)?;
    emit_to(
        writer,
        &[format!(
            "hello\tlevel={}\ttick_ms={}\tmin_interval_ms={}\tmax_rows={}\tmax_block_bytes={}\tinterval_ms={interval_ms}",
            hello.level.name(),
            hello.limits.tick_ms,
            hello.limits.min_interval_ms,
            hello.limits.max_rows,
            hello.limits.max_block_bytes,
        )],
    )?;
    wait_for_tick(&mut client)?;
    read_current(&mut client, &config, writer)?;
    read_subscription(&mut client, &config, interval_ms, writer)
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    let stdout = io::stdout();
    let mut output = stdout.lock();
    let code = match run(&args, &mut output) {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("watch: {}", one_line(&error.to_string()));
            match error {
                RunError::Usage(_) => 2,
                RunError::Client(_) | RunError::Output(_) => 1,
            }
        }
    };
    process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::{
        current_args, parse_args, row_line, status_tick_seq, validate_interval, WatchConfig,
        DEFAULT_SELECTION,
    };
    use hostobs_client::Level;
    use std::path::PathBuf;

    fn words(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_owned()).collect()
    }

    #[test]
    fn arguments_accept_defaults_and_explicit_values() {
        let minimal = parse_args(&words(&[
            "--socket",
            "/run/hostobs/observe/hostobs.sock",
            "--level",
            "observe",
            "--ticks",
            "10",
        ]));
        assert_eq!(
            minimal,
            Ok(WatchConfig {
                socket: PathBuf::from("/run/hostobs/observe/hostobs.sock"),
                level: Level::Observe,
                ticks: 10,
                interval_ms: None,
                selection: DEFAULT_SELECTION.to_owned(),
            })
        );
        let explicit = parse_args(&words(&[
            "--selection",
            r#"{"entities":[{"inc":1}],"metrics":["cpu.usage_usec"]}"#,
            "--interval-ms",
            "2000",
            "--ticks",
            "2",
            "--level",
            "operator",
            "--socket",
            "/tmp/operator.sock",
        ]));
        assert!(matches!(
            explicit,
            Ok(WatchConfig {
                interval_ms: Some(2000),
                level: Level::Operator,
                ..
            })
        ));
    }

    #[test]
    fn argument_errors_name_the_missing_or_bad_option() {
        for args in [
            words(&["--ticks", "1"]),
            words(&["--socket", "s", "--level", "observe"]),
            words(&["--socket", "s", "--level", "control", "--ticks", "1"]),
            words(&["--socket", "s", "--level", "observe", "--ticks", "0"]),
            words(&[
                "--socket",
                "s",
                "--level",
                "observe",
                "--ticks",
                "1",
                "--interval-ms",
            ]),
            words(&[
                "--socket", "s", "--level", "observe", "--ticks", "1", "--wat",
            ]),
        ] {
            assert!(parse_args(&args).is_err(), "{args:?}");
        }
    }

    #[test]
    fn selection_is_embedded_without_reencoding() {
        assert_eq!(
            current_args(DEFAULT_SELECTION),
            format!(r#"{{"selection":{DEFAULT_SELECTION}}}"#)
        );
    }

    #[test]
    fn interval_is_an_exact_multiple_of_the_tick() {
        assert_eq!(validate_interval(1000, 1000, 1000), Ok(()));
        assert_eq!(validate_interval(2000, 1000, 1000), Ok(()));
        assert!(validate_interval(1500, 1000, 1000).is_err());
        assert!(validate_interval(500, 1000, 1000).is_err());
        assert!(validate_interval(61_000, 1000, 1000).is_err());
        assert!(validate_interval(1000, 0, 1000).is_err());
    }

    #[test]
    fn row_format_is_one_tab_separated_line() {
        assert_eq!(
            row_line(7, "/work", "cpu.usage_usec", "U64(42)", "ok"),
            "tick\tseq=7\tentity=/work\tmetric=cpu.usage_usec\tvalue=U64(42)\tquality=ok"
        );
    }

    #[test]
    fn status_readiness_finds_the_tick_sequence() {
        assert_eq!(
            status_tick_seq(r#"{"tick":{"seq":17,"last_mono_ns":4,"scan_ms":1}}"#),
            Some(17)
        );
        assert_eq!(status_tick_seq(r#"{"tick":{"last_mono_ns":4}}"#), None);
        assert_eq!(status_tick_seq("{}"), None);
    }
}
