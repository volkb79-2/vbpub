use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use hostobs_kernel::clock::{ManualClock, Timeline};
use hostobs_kernel::fs::RealFs;
use hostobs_kernel::identity::cgroup_identity;
use hostobs_kernel::procfs::Roots;
use hostobs_model::quality::Quality;
use hostobs_probe::live::ProbeBackend;
use hostobs_probe::sampler::{Published, Sampler, SamplerConfig, Tick};
use hostobs_proto::dict::Entry;
use hostobs_proto::envelope::{check_end, check_rep};
use hostobs_proto::event::{decode_event_frame, EventPayload};
use hostobs_proto::frame::{decode_block, Block, FrameType};
use hostobs_serve::backend::{
    AnnotateArgs, AnnotateCtxKey, AnnotateKind, AnnotateOutcome, AnnotateRefusal, Backend,
    BackendError, Identity,
};
use hostobs_serve::conn::{CgroupKey as PeerCgroupKey, Peer};
use hostobs_serve::hub::ConnId;
use hostobs_serve::limits::Level;
use hostobs_serve::selection::{Sel, Selection};
use hostobs_serve::server::{Accept, Server, ServerLimits};
use hostobs_store::annotate::Annotator;
use hostobs_store::events::MAX_OPEN_SPANS_PER_ISSUER;
use hostobs_store::fsio::testdir::TempDir;
use hostobs_store::segment::scan_bytes;
use hostobs_store::store::{Store, StoreConfig, StoreDirs};
use hostobs_store::tmpfs::MountTable;

const WALL: i64 = 1_700_000_000_000_000_000;
const ID_BOOT: &str = "00112233-4455-6677-8899-aabbccddeeff";
const ID_EPOCH: u64 = 42;
const HELLO: &str = r#"{"client":"t","proto":{"major":1,"minor":2}}"#;

fn fixture_store(temp: &TempDir) -> Store {
    let config = StoreConfig::plan_v4(temp.path(), ID_EPOCH, 1).expect("store config");
    let mounts = MountTable::parse("tmpfs / tmpfs rw 0 0\n").expect("injected tmpfs mount");
    Store::open_with_mounts(config, Some(mounts)).expect("open fixture store")
}

fn fixture_annotator() -> Annotator {
    Annotator::new(50, 64).expect("deployed annotation limits")
}

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

fn server(
    published: Arc<Published>,
    max_rows: u32,
    temp: &TempDir,
) -> (Server<ProbeBackend>, ConnId) {
    let mut limits = ServerLimits::new();
    limits.probe.max_rows = max_rows;
    let mut server = Server::new(
        limits,
        ProbeBackend::new(
            identity(),
            published,
            fixture_store(temp),
            fixture_annotator(),
            || 0,
        ),
    );
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
    [b'H', b'O', b'B', 1, 1, 0, 2, 0]
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
            Sel::CgroupId { dev, inode } => {
                format!(r#"{{"cgroup_id":{{"dev":{dev},"inode":{inode}}}}}"#)
            }
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

fn cgroup_id(wanted: &str) -> Sel {
    let relative = wanted.strip_prefix('/').unwrap_or(wanted);
    let path = fixture_base().join("cgroupfs").join(relative);
    let identity = cgroup_identity(&RealFs::new(), &path).expect("fixture cgroup identity");
    Sel::CgroupId {
        dev: identity.device(),
        inode: identity.inode(),
    }
}

#[test]
fn hello_and_status_carry_identity_and_survive_the_first_tick() {
    let published = Arc::new(Published::new(1_000_000, WALL));
    let temp = TempDir::new("live-backend");
    let (mut server, id) = server(Arc::clone(&published), 16, &temp);
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
    let temp = TempDir::new("live-backend");
    let (mut server, id) = server(Arc::clone(&published), 16, &temp);
    hello_frame(&mut server, id, 1_003_000_000);
    let _ = server.backend_mut().begin_turn(1_003_000_000);

    let (all, _) = current(
        &mut server,
        id,
        1_003_000_000,
        2,
        selection(
            vec![Sel::All],
            &[
                "cpu.usage_usec",
                "mem.current",
                "psi.cpu.some.total",
                "limit.cpu.max.quota",
                "limit.cpu.max.period",
            ],
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
    assert_eq!(
        metric(&all, "limit.cpu.max.quota", 0),
        (0, Quality::Unsupported.code()),
        "cpu.max max has no measured quota"
    );
    assert_eq!(
        metric(&all, "limit.cpu.max.period", 0),
        (100_000, Quality::Ok.code())
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
                cgroup_id(&child),
                Sel::Prefix(system.into()),
            ],
            &[
                "cpu.usage_usec",
                "pids.current",
                "limit.cpu.max.quota",
                "limit.cpu.max.period",
            ],
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
        metric(&selected, "limit.cpu.max.quota", 0),
        (0, Quality::Unsupported.code()),
        "an absent cpu.max has no measured quota"
    );
    assert_eq!(
        metric(&selected, "limit.cpu.max.period", 0),
        (0, Quality::Absent.code()),
        "an absent cpu.max has no measured period"
    );
    assert_eq!(
        metric(&selected, "limit.cpu.max.quota", 1),
        (200_000, Quality::Ok.code())
    );
    assert_eq!(
        metric(&selected, "limit.cpu.max.period", 1),
        (100_000, Quality::Ok.code())
    );
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
    assert_eq!(names.len(), 45, "36 cgroup + 7 host + 2 daemon metrics");
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
fn cgroup_id_selects_one_row_by_all_keys_and_rejects_unpublished_keys() {
    let (_, published, tick) = first_tick();
    let target = format!("/system.slice/docker-{}.scope", "a".repeat(64));
    let target_inc = inc(&tick, &target);
    let temp = TempDir::new("live-backend");
    let (mut server, id) = server(Arc::clone(&published), 16, &temp);
    hello_frame(&mut server, id, 1_003_000_000);
    let _ = server.backend_mut().begin_turn(1_003_000_000);

    let (selected, rep) = current(
        &mut server,
        id,
        1_003_000_000,
        2,
        selection(
            vec![
                Sel::Inc(target_inc),
                Sel::Path(target.clone()),
                cgroup_id(&target),
            ],
            &["cpu.usage_usec"],
        ),
    );
    assert!(rep.contains(r#""ok":true"#), "{rep}");
    assert_eq!(selected.rows.len(), 1);
    assert_eq!(label(&selected, 0), target);
    assert_eq!(
        metric(&selected, "cpu.usage_usec", 0),
        (5_000_000, Quality::Ok.code())
    );
    let Sel::CgroupId { dev, inode } = cgroup_id(&target) else {
        panic!("cgroup_id helper returned another selector");
    };
    let wrong_dev = if dev == u64::MAX { dev - 1 } else { dev + 1 };
    let (wrong_device, rep) = current(
        &mut server,
        id,
        1_003_000_000,
        3,
        selection(
            vec![Sel::CgroupId {
                dev: wrong_dev,
                inode,
            }],
            &["cpu.usage_usec"],
        ),
    );
    assert!(rep.contains(r#""ok":true"#), "{rep}");
    assert!(wrong_device.rows.is_empty());

    let outside_root = fixture_base().join("cgroupfs-files/state");
    let outside_identity = cgroup_identity(&RealFs::new(), &outside_root)
        .expect("fixture directory identity outside configured cgroup root");
    let (unpublished, rep) = current(
        &mut server,
        id,
        1_003_000_000,
        4,
        selection(
            vec![Sel::CgroupId {
                dev: outside_identity.device(),
                inode: outside_identity.inode(),
            }],
            &["cpu.usage_usec"],
        ),
    );
    assert!(rep.contains(r#""ok":true"#), "{rep}");
    assert!(unpublished.rows.is_empty());

    let (unknown, rep) = current(
        &mut server,
        id,
        1_003_000_000,
        5,
        selection(
            vec![Sel::CgroupId {
                dev: u64::MAX,
                inode: u64::MAX,
            }],
            &["cpu.usage_usec"],
        ),
    );
    assert!(rep.contains(r#""ok":true"#), "{rep}");
    assert!(unknown.rows.is_empty());
}

#[test]
fn current_budget_missing_path_and_unregistered_metric_are_client_facts() {
    let (_, published, _) = first_tick();
    let temp = TempDir::new("live-backend");
    let (mut server, id) = server(Arc::clone(&published), 9, &temp);
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
    let temp = TempDir::new("live-backend");
    let (mut server, id) = server(Arc::clone(&published), 16, &temp);
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
    let temp = TempDir::new("live-backend");
    let mut backend = ProbeBackend::new(
        identity(),
        Arc::clone(&published),
        fixture_store(&temp),
        fixture_annotator(),
        || 0,
    );
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

// ---- annotate: the probe's mapping from the checked arguments to stored event/1 records ---

const STARTED: u64 = 1_000_000;

fn annotating_backend(temp: &TempDir, annotator: Annotator) -> ProbeBackend {
    ProbeBackend::new(
        identity(),
        Arc::new(Published::new(STARTED, WALL)),
        fixture_store(temp),
        annotator,
        || 0,
    )
}

fn emitter() -> Peer {
    Peer {
        uid: 1000,
        pid: Some(7),
        comm: Some("emitter".into()),
        cgroup: Some(PeerCgroupKey { dev: 3, inode: 9 }),
    }
}

fn annotation(kind: AnnotateKind, key: &str) -> AnnotateArgs {
    AnnotateArgs {
        kind,
        name: "lane".into(),
        key: Some(key.into()),
        ctx: BTreeMap::new(),
        outcome: None,
        code: None,
        attrs: BTreeMap::new(),
        idempotency: None,
    }
}

fn ending(key: &str) -> AnnotateArgs {
    let mut args = annotation(AnnotateKind::End, key);
    args.outcome = Some(AnnotateOutcome::Ok);
    args
}

/// Every event record on disk, in server-time order.
fn stored_events(temp: &TempDir) -> Vec<EventPayload> {
    let events_dir = StoreDirs::under(temp.path()).events;
    let mut out = Vec::new();
    for entry in std::fs::read_dir(&events_dir).expect("events directory") {
        let entry = entry.expect("directory entry");
        let name = entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with("events-") {
            continue;
        }
        let bytes = std::fs::read(entry.path()).expect("segment bytes");
        let scan = scan_bytes(&name, &bytes, 4096).expect("events segment");
        for record in scan.records {
            out.push(decode_event_frame(&record.payload).expect("event/1 frame"));
        }
    }
    out.sort_by_key(|event| (event.t_mono_ns, event.span));
    out
}

fn mono(ns: u64) -> i64 {
    i64::try_from(ns).expect("small test time")
}

#[test]
fn annotate_stores_the_checked_fields_with_peer_facts_and_server_time() {
    let temp = TempDir::new("live-backend");
    let mut backend = annotating_backend(&temp, fixture_annotator());
    let now = STARTED + 5_000;
    backend.begin_turn(now);
    let mut begin = annotation(AnnotateKind::Begin, "k");
    begin.ctx = BTreeMap::from([
        (AnnotateCtxKey::Run, "r".into()),
        (AnnotateCtxKey::Lane, "l".into()),
        (AnnotateCtxKey::Candidate, "c".into()),
        (AnnotateCtxKey::Txn, "t".into()),
        (AnnotateCtxKey::Session, "s".into()),
    ]);
    begin.attrs = BTreeMap::from([("host".into(), "v".into())]);

    let reply = backend
        .annotate(ConnId(4), &emitter(), &begin)
        .expect("begin");
    assert_eq!((reply.t_mono_ns, reply.span), (now, Some(1)));

    let events = stored_events(&temp);
    assert_eq!(events.len(), 1);
    let event = &events[0];
    assert_eq!((event.kind, event.outcome, event.inferred), (1, 0, false));
    assert_eq!((event.span, event.t_mono_ns), (1, mono(now)));
    assert_eq!(
        (event.uid, event.peer_pid, event.peer_dev, event.peer_inode),
        (1000, 7, 3, 9)
    );
    assert_eq!(event.comm.as_deref(), Some("emitter"));
    assert_eq!((event.name.as_str(), event.key.as_str()), ("lane", "k"));
    let ctx: Vec<(u8, &str)> = event
        .ctx
        .iter()
        .map(|entry| (entry.key, entry.value.as_str()))
        .collect();
    assert_eq!(ctx, [(0, "r"), (1, "l"), (2, "c"), (3, "t"), (4, "s")]);
    let attrs: Vec<(&str, &str)> = event
        .attrs
        .iter()
        .map(|entry| (entry.key.as_str(), entry.value.as_str()))
        .collect();
    assert_eq!(attrs, [("host", "v")]);
}

#[test]
fn annotate_drops_peer_labels_that_event_1_cannot_carry() {
    let temp = TempDir::new("live-backend");
    let mut backend = annotating_backend(&temp, fixture_annotator());
    let unusable = [
        ("", 0, 9),
        ("sixteen-bytes-xx", 3, 0),
        ("two\nlines", 0, 0),
        ("del\u{7f}", 0, 9),
    ];
    for (index, (comm, dev, inode)) in (0u64..).zip(unusable) {
        backend.begin_turn(STARTED + 1_000 + index);
        let peer = Peer {
            uid: 1000,
            pid: Some(0),
            comm: Some(comm.into()),
            cgroup: Some(PeerCgroupKey { dev, inode }),
        };
        backend
            .annotate(ConnId(4), &peer, &annotation(AnnotateKind::Point, "p"))
            .expect("an unusable label never refuses the event");
    }

    let events = stored_events(&temp);
    assert_eq!(events.len(), 4);
    for event in events {
        assert_eq!(event.comm, None);
        assert_eq!(
            (event.peer_pid, event.peer_dev, event.peer_inode),
            (0, 0, 0)
        );
        assert_eq!((event.kind, event.span), (3, 0));
    }
}

#[test]
fn every_end_outcome_and_its_code_reach_the_stored_record() {
    let temp = TempDir::new("live-backend");
    let mut backend = annotating_backend(&temp, fixture_annotator());
    let outcomes = [
        AnnotateOutcome::Ok,
        AnnotateOutcome::Fail,
        AnnotateOutcome::Error,
        AnnotateOutcome::Skipped,
        AnnotateOutcome::Cancelled,
        AnnotateOutcome::Timeout,
        AnnotateOutcome::Abandoned,
    ];
    for (index, outcome) in (0u64..).zip(outcomes) {
        let key = format!("k{index}");
        // Two minutes apart, so each pair also seals the previous events segment.
        let now = STARTED + index * 120_000_000_000;
        backend.begin_turn(now);
        let begin = backend
            .annotate(ConnId(4), &emitter(), &annotation(AnnotateKind::Begin, &key))
            .expect("begin");
        backend.begin_turn(now + 1_000);
        let mut end = annotation(AnnotateKind::End, &key);
        end.outcome = Some(outcome);
        end.code = Some(format!("c{index}"));
        let reply = backend
            .annotate(ConnId(4), &emitter(), &end)
            .expect("end");
        assert_eq!(reply.span, begin.span);
    }

    let ends: Vec<(u8, bool, Option<String>)> = stored_events(&temp)
        .into_iter()
        .filter(|event| event.kind == 2)
        .map(|event| (event.outcome, event.inferred, event.code))
        .collect();
    let expected: Vec<(u8, bool, Option<String>)> = (1u8..=7)
        .map(|code| (code, false, Some(format!("c{}", code - 1))))
        .collect();
    assert_eq!(ends, expected);
}

#[test]
fn a_repeated_idempotent_point_replies_as_before_and_is_stored_once() {
    let temp = TempDir::new("live-backend");
    let mut backend = annotating_backend(&temp, fixture_annotator());
    let mut point = annotation(AnnotateKind::Point, "p");
    point.idempotency = Some((ID_BOOT.into(), 5));

    backend.begin_turn(STARTED + 1_000);
    let first = backend
        .annotate(ConnId(4), &emitter(), &point)
        .expect("first point");
    backend.begin_turn(STARTED + 2_000);
    let again = backend
        .annotate(ConnId(5), &emitter(), &point)
        .expect("repeated point");

    assert_eq!(again, first);
    assert_eq!(first.t_mono_ns, STARTED + 1_000);
    assert_eq!(stored_events(&temp).len(), 1);
}

#[test]
fn closing_a_connection_ends_only_its_own_open_spans() {
    let temp = TempDir::new("live-backend");
    let mut backend = annotating_backend(&temp, fixture_annotator());
    backend.begin_turn(STARTED + 1_000);
    for (conn, key) in [(ConnId(4), "a"), (ConnId(5), "b")] {
        backend
            .annotate(conn, &emitter(), &annotation(AnnotateKind::Begin, key))
            .expect("begin");
    }

    backend.begin_turn(STARTED + 2_000);
    backend.connection_closed(ConnId(4));

    let events = stored_events(&temp);
    assert_eq!(events.len(), 3);
    let end = &events[2];
    assert_eq!((end.kind, end.outcome, end.inferred), (2, 7, true));
    assert_eq!(
        (end.span, end.key.as_str(), end.t_mono_ns),
        (1, "a", mono(STARTED + 2_000))
    );
    assert_eq!(
        backend.annotate(ConnId(4), &emitter(), &ending("a")),
        Err(AnnotateRefusal::SpanUnknown)
    );
    let other = backend
        .annotate(ConnId(5), &emitter(), &ending("b"))
        .expect("the other connection keeps its span");
    assert_eq!(other.span, Some(2));
}

#[test]
fn a_turn_time_before_startup_refuses_appends_and_forgets_a_closed_connection() {
    let temp = TempDir::new("live-backend");
    let mut backend = annotating_backend(&temp, fixture_annotator());
    backend.begin_turn(STARTED + 1_000);
    backend
        .annotate(
            ConnId(4),
            &emitter(),
            &annotation(AnnotateKind::Begin, "a"),
        )
        .expect("begin");

    backend.begin_turn(STARTED - 1);
    assert_eq!(
        backend.annotate(ConnId(5), &emitter(), &annotation(AnnotateKind::Point, "p")),
        Err(AnnotateRefusal::Internal)
    );
    backend.connection_closed(ConnId(4));
    assert_eq!(stored_events(&temp).len(), 1, "no inferred end has a time");

    backend.begin_turn(STARTED + 2_000);
    assert_eq!(
        backend.annotate(ConnId(4), &emitter(), &ending("a")),
        Err(AnnotateRefusal::SpanUnknown)
    );
}

#[test]
fn rate_and_span_limits_reach_the_caller_as_typed_refusals() {
    let temp = TempDir::new("live-backend");
    let one = Annotator::new(1, 1).expect("one token per second");
    let mut backend = annotating_backend(&temp, one);
    backend.begin_turn(STARTED + 1_000);
    let point = annotation(AnnotateKind::Point, "p");
    backend
        .annotate(ConnId(4), &emitter(), &point)
        .expect("the one token");
    assert_eq!(
        backend.annotate(ConnId(4), &emitter(), &point),
        Err(AnnotateRefusal::RateLimited {
            retry_after_ms: 1_000
        })
    );

    let temp = TempDir::new("live-backend");
    let roomy = Annotator::new(1_000, 1_024).expect("largest limits");
    let mut backend = annotating_backend(&temp, roomy);
    backend.begin_turn(STARTED + 1_000);
    for index in 0..MAX_OPEN_SPANS_PER_ISSUER {
        let begin = annotation(AnnotateKind::Begin, &format!("k{index}"));
        backend
            .annotate(ConnId(4), &emitter(), &begin)
            .expect("under the span limit");
    }
    assert_eq!(
        backend.annotate(
            ConnId(4),
            &emitter(),
            &annotation(AnnotateKind::Begin, "over")
        ),
        Err(AnnotateRefusal::Budget)
    );
}
