use std::path::PathBuf;
use std::sync::Arc;

use hostobs_kernel::clock::{ManualClock, Timeline};
use hostobs_kernel::fs::RealFs;
use hostobs_kernel::procfs::Roots;
use hostobs_model::quality::Quality;
use hostobs_probe::live::ProbeBackend;
use hostobs_probe::sampler::{Published, Sampler, SamplerConfig, Tick};
use hostobs_proto::dict::Entry;
use hostobs_proto::envelope::{check_end, check_rep};
use hostobs_proto::frame::{decode_block, Block, FrameType};
use hostobs_serve::backend::{Backend, BackendError, Identity};
use hostobs_serve::conn::Peer;
use hostobs_serve::hub::ConnId;
use hostobs_serve::limits::Level;
use hostobs_serve::selection::{Sel, Selection};
use hostobs_serve::server::{Accept, Server, ServerLimits};

const WALL: i64 = 1_700_000_000_000_000_000;
const ID_BOOT: &str = "00112233-4455-6677-8899-aabbccddeeff";
const ID_EPOCH: u64 = 42;
const HELLO: &str = r#"{"client":"t","proto":{"major":1,"minor":1}}"#;

fn identity() -> Identity {
    Identity {
        server: "hostobs-probe".into(),
        boot_id: ID_BOOT.into(),
        epoch: ID_EPOCH,
        features: Vec::new(),
    }
}

fn fixture_base() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../crates/kernel/tests/fixtures")
}

fn fixture_sampler() -> (Sampler<RealFs, ManualClock>, Arc<Published>) {
    let base = fixture_base();
    let started = 1_000_000;
    let cfg = SamplerConfig {
        boot_id: [7; 16],
        epoch: 11,
        tick_ms: 1_000,
        cgroup_root: base.join("cgroupfs"),
        max_rows: 16,
    };
    let roots = Roots {
        proc: base.join("proc"),
        sys: base.join("sys"),
        cgroup: base.join("cgroupfs"),
    };
    let sampler = Sampler::new(
        RealFs::new(),
        roots,
        ManualClock::new(Timeline::new([7; 16], 11), started, WALL),
        cfg,
        64,
    )
    .expect("the compiled metric baseline is valid");
    (sampler, Arc::new(Published::new(started, WALL)))
}

fn first_tick() -> (Sampler<RealFs, ManualClock>, Arc<Published>, Arc<Tick>) {
    let (mut sampler, published) = fixture_sampler();
    let tick = Arc::new(sampler.tick());
    published.store(Arc::clone(&tick));
    (sampler, published, tick)
}

fn server(published: Arc<Published>, max_rows: u32) -> (Server<ProbeBackend>, ConnId) {
    let mut limits = ServerLimits::new();
    limits.probe.max_rows = max_rows;
    let mut server = Server::new(limits, ProbeBackend::new(identity(), published));
    let now = 1_003_000_000;
    let id = match server.accept(
        Level::Observe,
        Some(Peer {
            uid: 1000,
            pid: Some(7),
            comm: None,
            cgroup: None,
        }),
        now,
    ) {
        Accept::Accepted(id) => id,
        Accept::Refused => panic!("observe connection refused"),
    };
    (server, id)
}

fn request(corr: u32, verb: &str, args: &str) -> Vec<u8> {
    let json = format!(r#"{{"verb":"{verb}","args":{args}}}"#);
    let len = u32::try_from(12usize + json.len()).expect("small request");
    let mut out = Vec::with_capacity(len as usize);
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(&[FrameType::Req.code(), 0, 0, 0]);
    out.extend_from_slice(&corr.to_le_bytes());
    out.extend_from_slice(json.as_bytes());
    out
}

fn prefix() -> [u8; 8] {
    [b'H', b'O', b'B', 1, 1, 0, 1, 0]
}

fn selection(entities: Vec<Sel>, metrics: &[&str]) -> String {
    format!(r#"{{"selection":{}}}"#, selection_value(entities, metrics))
}

fn selection_value(entities: Vec<Sel>, metrics: &[&str]) -> String {
    let entities: Vec<String> = entities
        .into_iter()
        .map(|entity| match entity {
            Sel::All => r#"{"all":true}"#.to_string(),
            Sel::Inc(inc) => format!(r#"{{"inc":{inc}}}"#),
            Sel::Path(path) => format!(r#"{{"path":{}}}"#, json_string(&path)),
            Sel::Prefix(prefix) => {
                format!(r#"{{"prefix":{}}}"#, json_string(&prefix))
            }
        })
        .collect();
    let metrics: Vec<String> = metrics.iter().map(|name| json_string(name)).collect();
    format!(
        r#"{{"entities":[{}],"metrics":[{}]}}"#,
        entities.join(","),
        metrics.join(",")
    )
}

fn json_string(text: &str) -> String {
    format!("\"{}\"", text.replace('\\', "\\\\").replace('"', "\\\""))
}

#[derive(Debug)]
struct Frame {
    kind: FrameType,
    corr: u32,
    bytes: Vec<u8>,
}

fn drain(server: &mut Server<ProbeBackend>, id: ConnId, now: u64) -> Vec<u8> {
    let mut out = Vec::new();
    while let Some([head, tail]) = server.output(id, now) {
        let mut bytes = head.to_vec();
        bytes.extend_from_slice(tail);
        out.extend_from_slice(&bytes);
        server.wrote(id, bytes.len(), now);
    }
    out
}

fn frames(bytes: &[u8]) -> Vec<Frame> {
    let bytes = bytes.strip_prefix(prefix().as_slice()).unwrap_or(bytes);
    let mut out = Vec::new();
    let mut rest = bytes;
    while rest.len() >= 12 {
        let len = usize::try_from(u32::from_le_bytes(
            rest[..4].try_into().expect("four-byte frame length"),
        ))
        .expect("frame length fits usize");
        let kind = FrameType::from_code(rest[4]).expect("known frame type");
        let corr = u32::from_le_bytes(rest[8..12].try_into().expect("four-byte corr"));
        out.push(Frame {
            kind,
            corr,
            bytes: rest[..len].to_vec(),
        });
        rest = &rest[len..];
    }
    out
}

fn send(server: &mut Server<ProbeBackend>, id: ConnId, now: u64, input: &[u8]) -> Vec<Frame> {
    server.on_bytes(id, input, now);
    frames(&drain(server, id, now))
}

fn hello_frame(server: &mut Server<ProbeBackend>, id: ConnId, now: u64) -> Frame {
    let mut input = prefix().to_vec();
    input.extend_from_slice(&request(1, "hello", HELLO));
    let mut frames = send(server, id, now, &input);
    assert_eq!(frames.len(), 1);
    frames.remove(0)
}

fn body(frame: &Frame) -> String {
    String::from_utf8_lossy(&frame.bytes[12..]).into_owned()
}

fn current(
    server: &mut Server<ProbeBackend>,
    id: ConnId,
    now: u64,
    corr: u32,
    args: String,
) -> (Block, String) {
    let frames = send(server, id, now, &request(corr, "current", &args));
    assert_eq!(frames.len(), 3, "REP, one BLOCK, END");
    assert_eq!(frames[0].kind, FrameType::Rep);
    let rep = check_rep(body(&frames[0]).as_bytes(), false).expect("REP");
    assert!(rep.ok, "{}", body(&frames[0]));
    assert_eq!(frames[1].kind, FrameType::Block);
    let block = decode_block(&frames[1].bytes).expect("dictionary-bearing block");
    let end = check_end(body(&frames[2]).as_bytes()).expect("END");
    let reason = end
        .reason
        .map(|range| String::from_utf8_lossy(&body(&frames[2]).as_bytes()[range]).into_owned());
    assert_eq!(reason.as_deref(), Some("complete"));
    (block, body(&frames[0]))
}

fn label(block: &Block, row: usize) -> &str {
    let ent = block.rows[row].ent;
    match block
        .dictionary
        .as_ref()
        .expect("dictionary")
        .lookup(ent)
        .expect("entity")
    {
        Entry::Entity { label, .. } => label,
        _ => panic!("row is not an entity"),
    }
}

fn metric(block: &Block, name: &str, row: usize) -> (u64, u8) {
    let dictionary = block.dictionary.as_ref().expect("dictionary");
    let entry = dictionary
        .entries()
        .iter()
        .position(|entry| matches!(entry, Entry::Metric { name: found, .. } if found == name))
        .expect("registered metric entry");
    let entry = u32::try_from(entry).expect("small dictionary");
    let column = block
        .columns
        .iter()
        .find(|column| column.metric == entry)
        .expect("column");
    (column.values[row], column.quality[row])
}

fn inc(tick: &Tick, wanted: &str) -> u64 {
    tick.data()
        .entries
        .iter()
        .find_map(|entry| match entry {
            Entry::Entity { inc, label } if label == wanted => Some(*inc),
            _ => None,
        })
        .expect("fixture label")
}

#[test]
fn hello_and_status_carry_identity_and_survive_the_first_tick() {
    let published = Arc::new(Published::new(1_000_000, WALL));
    let (mut server, id) = server(Arc::clone(&published), 16);
    let hello = hello_frame(&mut server, id, 1_003_000_000);
    let text = body(&hello);
    assert!(text.contains(&format!("\"boot_id\":\"{ID_BOOT}\"")));
    assert!(text.contains(&format!("\"epoch\":{ID_EPOCH}")));

    let _ = server.backend_mut().begin_turn(1_003_000_000);
    let frames = send(&mut server, id, 1_003_000_000, &request(2, "status", "{}"));
    let text = body(&frames[0]);
    assert!(
        text.contains(
            r#""started_real_ns":1700000000000000000,"uptime_ms":1002,"tick":{"seq":0,"last_mono_ns":0,"scan_ms":0}"#
        ),
        "{text}"
    );

    let (_, _, tick) = first_tick();
    published.store(tick);
    assert!(server.backend_mut().begin_turn(1_004_000_000));
    let frames = send(&mut server, id, 1_004_000_000, &request(3, "status", "{}"));
    let text = body(&frames[0]);
    assert!(
        text.contains(r#""uptime_ms":1003,"tick":{"seq":1,"last_mono_ns":1000000,"scan_ms":0}"#),
        "{text}"
    );
}

#[test]
fn current_all_path_prefix_and_inc_decode_fixture_values() {
    let (_, published, tick) = first_tick();
    let (mut server, id) = server(Arc::clone(&published), 16);
    hello_frame(&mut server, id, 1_003_000_000);
    let _ = server.backend_mut().begin_turn(1_003_000_000);

    let (all, _) = current(
        &mut server,
        id,
        1_003_000_000,
        2,
        selection(
            vec![Sel::All],
            &["cpu.usage_usec", "mem.current", "psi.cpu.some.total"],
        ),
    );
    assert_eq!(all.header.seq, 1);
    assert_eq!(label(&all, 0), "/");
    assert_eq!(
        metric(&all, "cpu.usage_usec", 0),
        (10_000_000, Quality::Ok.code())
    );
    assert_eq!(
        metric(&all, "mem.current", 3),
        (0, Quality::Absent.code()),
        "a cgroup without the file is absent, never a measured zero"
    );
    assert_eq!(
        metric(&all, "psi.cpu.some.total", 0),
        (5000, Quality::Ok.code()),
        "the configured root publishes its own PSI, not host-wide PSI"
    );

    let system = "/system.slice";
    let child = format!("{system}/docker-{}.scope", "a".repeat(64));
    let child_inc = inc(&tick, &child);
    let (selected, _) = current(
        &mut server,
        id,
        1_003_000_000,
        3,
        selection(
            vec![
                Sel::Path(child.clone()),
                Sel::Inc(child_inc),
                Sel::Prefix(system.into()),
            ],
            &["cpu.usage_usec", "pids.current"],
        ),
    );
    assert_eq!(
        selected.rows.len(),
        2,
        "the duplicate child is resolved once"
    );
    assert_eq!(label(&selected, 0), system);
    assert_eq!(label(&selected, 1), child);
    assert_eq!(
        metric(&selected, "cpu.usage_usec", 1),
        (5_000_000, Quality::Ok.code())
    );
    assert_eq!(
        metric(&selected, "pids.current", 1),
        (34, Quality::Ok.code())
    );

    let (all, _) = current(
        &mut server,
        id,
        1_003_000_000,
        4,
        selection(vec![Sel::All], &["cpu.usage_usec"]),
    );
    assert_eq!(all.rows.len(), tick.data().rows.len());

    let names: Vec<&str> = tick
        .data()
        .entries
        .iter()
        .filter_map(|entry| match entry {
            Entry::Metric { name, .. } => Some(name.as_str()),
            _ => None,
        })
        .collect();
    assert_eq!(names.len(), 43, "34 cgroup + 7 host + 2 daemon metrics");
    let (complete, _) = current(
        &mut server,
        id,
        1_003_000_000,
        5,
        selection(vec![Sel::All], &names),
    );
    assert_eq!(complete.rows.len(), tick.data().rows.len());
    assert_eq!(complete.columns.len(), names.len());
}

#[test]
fn current_budget_missing_path_and_unregistered_metric_are_client_facts() {
    let (_, published, _) = first_tick();
    let (mut server, id) = server(Arc::clone(&published), 9);
    hello_frame(&mut server, id, 1_003_000_000);
    let _ = server.backend_mut().begin_turn(1_003_000_000);

    let frames = send(
        &mut server,
        id,
        1_003_000_000,
        &request(
            2,
            "current",
            &selection(vec![Sel::All], &["cpu.usage_usec"]),
        ),
    );
    assert_eq!(frames.len(), 1, "budget is refused before REP data");
    let rep = check_rep(body(&frames[0]).as_bytes(), false).expect("REP");
    assert!(!rep.ok);
    let text = body(&frames[0]);
    assert!(text.contains(r#""code":"budget""#), "{text}");
    assert!(text.contains(r#""detail":{"pool":"rows"}"#), "{text}");

    let (block, _) = current(
        &mut server,
        id,
        1_003_000_000,
        3,
        selection(
            vec![Sel::Path("/does-not-exist".into())],
            &["cpu.usage_usec", "not.registered"],
        ),
    );
    assert!(block.rows.is_empty());
    assert_eq!(
        block.columns.len(),
        1,
        "an unregistered request name yields no column"
    );
}

#[test]
fn subscribe_pushes_one_new_tick_and_nothing_when_begin_turn_is_stale() {
    let (mut sampler, published, tick1) = first_tick();
    assert_eq!(tick1.seq(), 1);
    let (mut server, id) = server(Arc::clone(&published), 16);
    hello_frame(&mut server, id, 1_003_000_000);
    let _ = server.backend_mut().begin_turn(1_003_000_000);

    let args = format!(
        r#"{{"selection":{},"interval_ms":1000}}"#,
        selection_value(vec![Sel::Path("/system.slice".into())], &["cpu.usage_usec"],)
    );
    let replies = send(
        &mut server,
        id,
        1_003_000_000,
        &request(2, "subscribe", &args),
    );
    assert_eq!(replies.len(), 1);
    assert_eq!(replies[0].kind, FrameType::Rep);
    assert!(drain(&mut server, id, 1_003_000_000).is_empty());

    let tick2 = Arc::new(sampler.tick());
    assert_eq!(tick2.seq(), 2);
    published.store(tick2);
    assert!(server.backend_mut().begin_turn(1_004_000_000));
    server.on_tick(1_004_000_000);
    let pushed = frames(&drain(&mut server, id, 1_004_000_000));
    assert_eq!(pushed.len(), 1);
    assert_eq!((pushed[0].kind, pushed[0].corr), (FrameType::Block, 2));
    let block = decode_block(&pushed[0].bytes).expect("subscription block");
    assert_eq!(block.header.seq, 2);

    assert!(!server.backend_mut().begin_turn(1_005_000_000));
    assert!(drain(&mut server, id, 1_005_000_000).is_empty());
}

#[test]
fn direct_backend_unavailable_is_distinct_from_a_nonexistent_path() {
    let published = Arc::new(Published::new(0, WALL));
    let mut backend = ProbeBackend::new(identity(), Arc::clone(&published));
    let selection =
        Selection::new(vec![Sel::All], vec!["cpu.usage_usec".into()]).expect("selection");
    assert_eq!(
        backend.current(&selection, 16),
        Err(BackendError::Unavailable)
    );
    assert!(!backend.begin_turn(1));
    let (_, _, tick) = first_tick();
    published.store(tick);
    assert!(backend.begin_turn(2));
}
