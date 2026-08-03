//go:build e2e

// The privileged half of harvest: the round trip the master plan's oracle 23
// names, and the measurement that closes the half of D-020 P06 left open.
//
// The unit tests assert what harvest refuses and what it assembles. Only this
// can say whether an unprivileged process — which is what a game container
// is — can actually write through an rw exposure, and whether what comes back
// out of a real tmpfs is byte for byte a release.
package harvest

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/expose"
	"srdm/internal/fsx"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

const (
	workerEnv = "SRDM_E2E_WORKER"
	// writeEnv makes the test binary write one file and report how it failed.
	// It exists so a write can be attempted as somebody other than root
	// without a shell, a fixture image or a uid that has to exist in
	// /etc/passwd.
	writeEnv = "SRDM_E2E_WRITE"
	// gameUID is an unprivileged identity standing in for the uid Wings runs
	// its server containers as. Nothing about it needs to exist in the passwd
	// database — the kernel checks numbers.
	gameUID = 65534
	gameGID = 65534
)

// Exit codes for the write helper, so the caller learns WHICH refusal it hit.
// EROFS and EACCES mean opposite things here: the first is the mount refusing
// (which is what ro is for), the second is the seal refusing (which is what
// unsealing exists to remove).
const (
	writeOK     = 0
	writeEROFS  = 10
	writeEACCES = 11
	writeOther  = 12
)

func TestMain(m *testing.M) {
	// The hold worker is srdm invoked on itself; here the running process is
	// a test binary, so the same trick is played on it.
	if os.Getenv(workerEnv) == "1" && len(os.Args) > 1 && os.Args[1] == hold.WorkerSubcommand {
		os.Exit(hold.RunWorker(os.Args[2:], os.Stderr))
	}
	if target := os.Getenv(writeEnv); target != "" {
		os.Exit(writeAsSelf(target))
	}
	os.Exit(m.Run())
}

func writeAsSelf(target string) int {
	err := os.WriteFile(target, []byte("written by the game's own updater"), 0o644)
	if err == nil {
		return writeOK
	}
	fmt.Fprintf(os.Stderr, "write helper (uid %d): %v\n", os.Getuid(), err)
	var errno syscall.Errno
	switch {
	case errors.As(err, &errno) && errno == syscall.EROFS:
		return writeEROFS
	case errors.As(err, &errno) && errno == syscall.EACCES:
		return writeEACCES
	default:
		return writeOther
	}
}

// writeAs attempts the write as uid:gid and returns one of the codes above.
func (n *node) writeAs(t *testing.T, target string, uid, gid int) int {
	t.Helper()
	cmd := exec.Command(n.writer(t))
	cmd.Env = append(os.Environ(), writeEnv+"="+target)
	cmd.Stderr = os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Credential: &syscall.Credential{Uid: uint32(uid), Gid: uint32(gid), NoSetGroups: true},
	}
	if err := cmd.Run(); err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return ee.ExitCode()
		}
		t.Fatalf("running the write helper: %v", err)
	}
	return writeOK
}

// writer is a world-executable copy of the test binary.
//
// Not the binary itself: `go test` builds it under a private 0700 directory
// in the build cache, so an unprivileged uid cannot even reach it, let alone
// exec it. Chmod'ing that directory would work and would mutate state shared
// with every other package's run — this suite runs as root in one container,
// and the rule about leaving no process-global state changed is exactly the
// kind that gets broken for a reason as good as this one.
func (n *node) writer(t *testing.T) string {
	t.Helper()
	if n.writerPath != "" {
		return n.writerPath
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(exe)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(n.dir, "writer")
	if err := os.WriteFile(path, body, 0o755); err != nil {
		t.Fatal(err)
	}
	n.writerPath = path
	return path
}

func requireRoot(t *testing.T) {
	t.Helper()
	if os.Getuid() != 0 {
		t.Fatalf("the e2e harness must run as root; got uid %d", os.Getuid())
	}
}

// --- the node -------------------------------------------------------------

const serverID = "7f1c2e3d-0000-4000-8000-abcdefabcdef"

type node struct {
	cfg        config.Config
	jnl        *journal.Journal
	prof       *profile.Profile
	rel        *store.Release
	st         *store.Store
	pub        *publish.Publisher
	rec        *publish.Record
	volume     string
	dir        string
	writerPath string
}

// managed is the content of the release's managed classes. Per-instance state
// is deliberately not in it: publication never carries any, so a harvest can
// never produce any, and a from-scratch stage of game content has none either
// until the game itself creates it.
var managed = map[string]string{
	"Engine/libengine.so":      "engine bytes",
	"WS/Binaries/server":       "server bytes",
	"WS/Content/Paks/game.pak": "pak bytes",
}

func newNode(t *testing.T) *node {
	t.Helper()
	requireRoot(t)

	dir := t.TempDir()
	// The unprivileged writer has to be able to TRAVERSE to the volume, and
	// TempDir hands back 0700 the whole way down. Without this the write
	// helper fails on a directory that has nothing to do with what is being
	// measured.
	for p := dir; p != "/" && p != "."; p = filepath.Dir(p) {
		if err := os.Chmod(p, 0o755); err != nil {
			break
		}
	}
	// Publication seals its trees read-only, and an rw exposure hands them to
	// somebody else; both have to be undone before TempDir's own cleanup runs.
	// Cleanups are last-registered-first.
	t.Cleanup(func() { reclaim(dir) })

	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	owner := config.WriteOwner{UID: gameUID, GID: gameGID}
	cfg.Wings = config.Wings{
		BindRoot:   filepath.Join(dir, "pterodactyl"),
		VolumeRoot: filepath.Join(dir, "pterodactyl", "volumes"),
		ConfigPath: filepath.Join(dir, "wings.yml"),
		WriteOwner: &owner,
	}

	fixed := time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)
	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixed }),
		journal.WithJournaldSocket(""))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = jnl.Close() })

	n := &node{cfg: cfg, jnl: jnl, prof: e2eProfile(t), dir: dir}
	n.st, err = store.Open(cfg, jnl)
	if err != nil {
		t.Fatal(err)
	}
	// The release id has to be unique per test: the generation id is derived
	// from it, the hold unit names are derived from the generation, and a
	// transient unit outlives the test that made it for as long as it takes
	// systemd to reap it. Two tests sharing a release id race for a unit name.
	n.rel = n.stage(t, "rel-"+strings.ReplaceAll(t.Name(), "/", "-"), managed)

	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	holder, err := hold.New(hold.WithExe(exe), hold.WithWorkerEnv(workerEnv+"=1"))
	if err != nil {
		t.Fatal(err)
	}
	n.pub, err = publish.New(cfg, jnl, publish.WithHolder(holder))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { n.cleanUp(t) })

	// The Wings bind root has to be a SHARED mount, which is what it is on a
	// systemd host. The gate container's /tmp is private, so the harness makes
	// it so explicitly — otherwise the propagation precondition would fail for
	// a reason production never has.
	if err := os.MkdirAll(cfg.Wings.BindRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Mount(cfg.Wings.BindRoot, cfg.Wings.BindRoot, "", syscall.MS_BIND, ""); err != nil {
		t.Fatalf("binding the Wings root onto itself: %v", err)
	}
	t.Cleanup(func() { _ = syscall.Unmount(cfg.Wings.BindRoot, syscall.MNT_DETACH) })
	if err := syscall.Mount("", cfg.Wings.BindRoot, "", syscall.MS_SHARED, ""); err != nil {
		t.Fatalf("making the Wings root shared: %v", err)
	}

	n.volume = cfg.Wings.ServerVolume(serverID)
	if err := os.MkdirAll(n.volume, 0o755); err != nil {
		t.Fatal(err)
	}
	return n
}

func e2eProfile(t *testing.T) *profile.Profile {
	t.Helper()
	p := &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "testgame",
		Classes: []profile.Class{
			{Name: "pak", Kind: profile.KindManaged,
				Paths: []string{"WS/Content/Paks"}, MemoryMin: 150 << 20, NoExec: true},
			{Name: "code", Kind: profile.KindManaged,
				Paths: []string{"Engine", "WS/Binaries"}, MemoryMin: 200 << 20},
			{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS/Saved"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

// stage promotes a tree exactly as `srdm store promote --from` does, which is
// what the round trip compares a harvest against.
func (n *node) stage(t *testing.T, releaseID string, content map[string]string) *store.Release {
	t.Helper()
	src := filepath.Join(t.TempDir(), "staged-"+releaseID)
	for rel, body := range content {
		full := filepath.Join(src, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	tx, err := n.st.Begin("op-stage-" + releaseID)
	if err != nil {
		t.Fatal(err)
	}
	if err := fsx.CopyTree(src, tx.Root); err != nil {
		t.Fatal(err)
	}
	rel, err := n.st.Promote(tx, n.prof, store.PromoteOpts{
		ReleaseID:  releaseID,
		Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		t.Fatal(err)
	}
	return rel
}

func (n *node) publishGeneration(t *testing.T, opID string, rel *store.Release) *publish.Record {
	t.Helper()
	rec, err := n.pub.Publish(context.Background(), opID, "testgame", rel, n.prof)
	if err != nil {
		t.Fatalf("Publish: %v", err)
	}
	n.rec = rec
	return rec
}

// driver returns a host-bind driver. The container inspector is injected
// because the gate has no Docker socket; its logic is covered by the unit
// tests, and what is under test here is everything downstream of it.
func (n *node) driver(t *testing.T) *expose.HostBind {
	t.Helper()
	cfg := n.cfg.Wings
	cfg.ChownSkipPatch = true // the node asserts F1; precondition 2 is unit-tested
	d, err := expose.NewHostBind(cfg, n.jnl,
		expose.WithMarker(n.pub), expose.WithInspector(fakeInspector{}))
	if err != nil {
		t.Fatal(err)
	}
	return d
}

type fakeInspector struct{}

func (fakeInspector) BindPropagation(context.Context, string) (string, string, error) {
	return "rslave", "wings", nil
}

func (n *node) harvester(t *testing.T) *Harvester {
	t.Helper()
	h, err := New(n.cfg, n.jnl, n.st)
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func request(access expose.Access) expose.Request {
	return expose.Request{ServerID: serverID, Access: access, OperationID: "op-expose"}
}

func (n *node) cleanUp(t *testing.T) {
	t.Helper()
	entries, err := mountinfo.Read("")
	if err != nil {
		return
	}
	var points []string
	for _, root := range []string{n.cfg.Wings.VolumeRoot, n.cfg.RunDir} {
		for _, e := range mountinfo.Under(entries, root) {
			points = append(points, e.MountPoint)
		}
	}
	// Deepest first.
	for i := range points {
		for j := i + 1; j < len(points); j++ {
			if points[j] > points[i] {
				points[i], points[j] = points[j], points[i]
			}
		}
	}
	for _, m := range points {
		_ = syscall.Unmount(m, syscall.MNT_DETACH)
	}
}

// reclaim undoes the seal and the hand-over, so the harness can remove what
// it made.
func reclaim(root string) {
	_ = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.Mode()&os.ModeSymlink != 0 {
			return nil
		}
		_ = os.Lchown(path, 0, 0)
		_ = os.Chmod(path, info.Mode().Perm()|0o700)
		return nil
	})
}

// spawnConsumer holds its own bind of path in its own mount namespace — what
// a game container is, as far as the superblock is concerned. The returned
// stop waits for the process to be reaped, which is a synchronization point
// rather than a delay: after it, the pid and its namespace are gone.
func spawnConsumer(t *testing.T, path string) (stop func()) {
	t.Helper()
	script := fmt.Sprintf("mkdir -p /tmp/held.$$ && mount --bind %q /tmp/held.$$ && "+
		"echo ready && exec sleep 600", path)
	cmd := exec.Command("unshare", "--mount", "--propagation", "slave", "/bin/sh", "-c", script)
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the consumer namespace: %v", err)
	}
	stopped := false
	stop = func() {
		if stopped {
			return
		}
		stopped = true
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}
	t.Cleanup(stop)

	sc := bufio.NewScanner(out)
	if !sc.Scan() || strings.TrimSpace(sc.Text()) != "ready" {
		stop()
		t.Fatalf("the consumer never reported ready (%v)", sc.Err())
	}
	return stop
}

// --- D-022: who may write through an rw exposure --------------------------

// The measurement P06 could not make and deferred here, because harvest is
// the only reason to write through an exposure at all.
//
// P06 left rw permitting writes at the MOUNT level while publication's seal
// still refused them at the MODE level: root could write through, and the
// game container — which is the entire point — could not. This asserts the
// model that closes it, from the only vantage point that can tell the
// difference: an unprivileged uid.
func TestAnUnprivilegedWriterCanWriteThroughAnRWExposureAndNotThroughRO(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1", n.rel)
	d := n.driver(t)
	target := filepath.Join(n.volume, "WS/Content/Paks", "patch01.pak")

	// Read-only first, on the same generation: the mount refuses, and it
	// refuses with EROFS, which holds even for root.
	if err := d.Expose(context.Background(), rec, n.prof, request(expose.AccessRO)); err != nil {
		t.Fatalf("Expose ro: %v", err)
	}
	if code := n.writeAs(t, target, gameUID, gameGID); code != writeEROFS {
		t.Errorf("writing through a read-only exposure exited %d, want EROFS (%d)",
			code, writeEROFS)
	}
	if err := d.Unexpose(context.Background(), rec, n.prof, request(expose.AccessRO)); err != nil {
		t.Fatal(err)
	}

	// Now rw. This is the claim: not "root can write", which was already true
	// and useless, but that the uid the game runs as can.
	if err := d.Expose(context.Background(), rec, n.prof, request(expose.AccessRW)); err != nil {
		t.Fatalf("Expose rw: %v", err)
	}
	if code := n.writeAs(t, target, gameUID, gameGID); code != writeOK {
		t.Fatalf("an unprivileged write through an rw exposure exited %d (%s). rw exists so "+
			"the game's own updater keeps working unchanged; if only root can write through "+
			"it, it buys nothing", code, describeWrite(code))
	}
	// It landed in the generation's own tmpfs, not on the volume's disk
	// underneath the mount.
	body, err := os.ReadFile(filepath.Join(rec.Classes[pakIndex(rec)].ContentRoot(),
		"WS/Content/Paks/patch01.pak"))
	if err != nil {
		t.Fatalf("the write did not reach the generation's tmpfs: %v", err)
	}
	if !strings.Contains(string(body), "updater") {
		t.Errorf("the tmpfs holds %q", body)
	}
}

func describeWrite(code int) string {
	switch code {
	case writeEROFS:
		return "EROFS — the mount refused, so rw bound the read-only side"
	case writeEACCES:
		return "EACCES — the modes refused, so the tree was never unsealed"
	default:
		return "an unexpected failure"
	}
}

func pakIndex(rec *publish.Record) int {
	for i, c := range rec.Classes {
		if c.Name == "pak" {
			return i
		}
	}
	return 0
}

// --- oracle 23: the harvest round trip ------------------------------------

// The master plan's oracle 23, end to end: update in place through rw ->
// harvest -> the resulting release's manifest matches a from-scratch stage of
// the same content, byte for byte, and carries harvested-from provenance.
//
// "Byte for byte" is asserted as one string rather than by walking two trees:
// the manifest's content digest covers every entry's type, path, mode, class
// and payload hash, so two equal digests ARE two identical manifests, and any
// single changed byte anywhere changes it.
func TestHarvestRoundTripsToTheSameManifestAsAFromScratchStage(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1", n.rel)
	d := n.driver(t)

	if err := d.Expose(context.Background(), rec, n.prof, request(expose.AccessRW)); err != nil {
		t.Fatalf("Expose rw: %v", err)
	}

	// The update in place: a new pak, and a rewritten binary. Both as the game
	// container's uid, because that is who does it.
	added := filepath.Join(n.volume, "WS/Content/Paks", "patch01.pak")
	if code := n.writeAs(t, added, gameUID, gameGID); code != writeOK {
		t.Fatalf("the update could not write: %d (%s)", code, describeWrite(code))
	}
	// A replace rather than a rewrite, which is what an updater does to a
	// file it does not own the mode of.
	rewritten := filepath.Join(n.volume, "Engine", "libengine.so")
	if err := os.Remove(rewritten); err != nil {
		t.Fatal(err)
	}
	if code := n.writeAs(t, rewritten, gameUID, gameGID); code != writeOK {
		t.Fatalf("the update could not replace a file: %d (%s)", code, describeWrite(code))
	}

	// Quiesce: the exposure itself is a hold, so the procedure is stop the
	// server, unexpose, then harvest. Harvest reads the generation's own tmpfs
	// and needs no exposure to do it.
	if err := d.Unexpose(context.Background(), rec, n.prof, request(expose.AccessRW)); err != nil {
		t.Fatal(err)
	}

	live, err := n.pub.LoadRecord("testgame", rec.Generation)
	if err != nil {
		t.Fatal(err)
	}
	if !live.DirtyCapable {
		t.Fatal("the generation was written through but is not marked dirty-capable")
	}

	harvested, err := n.harvester(t).Harvest(context.Background(), live, n.prof,
		Opts{ReleaseID: "rel-harvested"})
	if err != nil {
		t.Fatalf("Harvest: %v", err)
	}

	// The same content, staged from scratch as an operator would.
	updated := map[string]string{}
	for k, v := range managed {
		updated[k] = v
	}
	updated["WS/Content/Paks/patch01.pak"] = "written by the game's own updater"
	updated["Engine/libengine.so"] = "written by the game's own updater"
	fromScratch := n.stage(t, "rel-scratch", updated)

	if harvested.Manifest.ContentDigest != fromScratch.Manifest.ContentDigest {
		t.Errorf("a harvested release and a from-scratch stage of the same content differ:\n"+
			"  harvested   %s\n  from scratch %s\nharvest exists to make the game's own "+
			"updater a trustworthy acquisition source, which it is only if its output is "+
			"indistinguishable from a stage",
			harvested.Manifest.ContentDigest, fromScratch.Manifest.ContentDigest)
		diffManifests(t, harvested.Manifest, fromScratch.Manifest)
	}
	if harvested.Complete.Provenance.Kind != store.ProvenanceHarvested ||
		harvested.Complete.Provenance.Source != n.rel.ID {
		t.Errorf("provenance is %+v, want harvested from %q",
			harvested.Complete.Provenance, n.rel.ID)
	}
	if err := n.st.Verify(harvested.ID); err != nil {
		t.Errorf("the harvested release does not verify: %v", err)
	}

	// And the point of all of it: republishing from the harvest keeps the
	// update, where republishing from the source release would discard it.
	// This is oracle 22's ephemerality with the remedy attached.
	if err := n.pub.Teardown(context.Background(), "op-down", "testgame", rec.Generation); err != nil {
		t.Fatalf("Teardown: %v", err)
	}
	fresh := n.publishGeneration(t, "op-2", harvested)
	if fresh.DirtyCapable {
		t.Error("a generation published from a harvested release is already dirty-capable")
	}
	if err := d.Expose(context.Background(), fresh, n.prof, request(expose.AccessRO)); err != nil {
		t.Fatalf("re-expose: %v", err)
	}
	body, err := os.ReadFile(added)
	if err != nil || !strings.Contains(string(body), "updater") {
		t.Errorf("the update did not survive a republish from the harvested release: %q, %v",
			body, err)
	}
}

func diffManifests(t *testing.T, got, want *store.Manifest) {
	t.Helper()
	index := func(m *store.Manifest) map[string]store.Entry {
		out := make(map[string]store.Entry, len(m.Entries))
		for _, e := range m.Entries {
			out[e.Path] = e
		}
		return out
	}
	g, w := index(got), index(want)
	for path, we := range w {
		ge, ok := g[path]
		if !ok {
			t.Errorf("  missing from the harvest: %s", path)
			continue
		}
		if ge != we {
			t.Errorf("  %s: harvested %+v, staged %+v", path, ge, we)
		}
	}
	for path := range g {
		if _, ok := w[path]; !ok {
			t.Errorf("  only in the harvest: %s", path)
		}
	}
}

// Oracle 23's last clause: harvest on a running consumer is refused. Against
// a real second mount namespace holding a real bind, because that is what a
// game container is and it is the only thing that can be writing.
func TestHarvestIsRefusedWhileARealConsumerHoldsTheGeneration(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1", n.rel)

	stop := spawnConsumer(t, rec.Classes[pakIndex(rec)].ExposePath)

	_, err := n.harvester(t).Harvest(context.Background(), rec, n.prof,
		Opts{ReleaseID: "rel-harvested"})
	if !IsRefusal(err) {
		t.Fatalf("want a refusal while a consumer holds the generation, got %v", err)
	}
	if ids, _ := n.st.List(); contains(ids, "rel-harvested") {
		t.Error("a refused harvest promoted a release anyway")
	}

	// After a clean stop it proceeds. The refusal is about writes being
	// quiesced, so it has to lift when they are — a check that never lets go
	// is a check nobody can satisfy.
	stop()
	if _, err := n.harvester(t).Harvest(context.Background(), rec, n.prof,
		Opts{ReleaseID: "rel-harvested"}); err != nil {
		t.Fatalf("harvest was refused after the consumer stopped: %v", err)
	}
}

func contains(all []string, want string) bool {
	for _, s := range all {
		if s == want {
			return true
		}
	}
	return false
}

// A generation that was torn down leaves its mount POINT behind as an
// ordinary empty directory. Against a real mount table rather than a
// synthetic one: this is the check that stops harvest promoting an empty
// release over a good one, and it is only worth anything if it reads the
// kernel's answer.
func TestHarvestIsRefusedOnceTheGenerationHasBeenTornDown(t *testing.T) {
	n := newNode(t)
	rec := n.publishGeneration(t, "op-1", n.rel)

	if err := n.pub.Teardown(context.Background(), "op-down", "testgame", rec.Generation); err != nil {
		t.Fatalf("Teardown: %v", err)
	}

	_, err := n.harvester(t).Harvest(context.Background(), rec, n.prof,
		Opts{ReleaseID: "rel-harvested"})
	var np *NotPublishedError
	if !errors.As(err, &np) {
		t.Fatalf("want a *NotPublishedError once the generation is gone, got %v", err)
	}
	if ids, _ := n.st.List(); contains(ids, "rel-harvested") {
		t.Error("an unmounted generation was promoted")
	}
}
