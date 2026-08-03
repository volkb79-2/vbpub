package hold

import (
	"bytes"
	"io/fs"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// --- harness ---------------------------------------------------------------

func testConfig(t *testing.T) config.Config {
	t.Helper()
	dir := t.TempDir()
	// The worker seals its tree read-only, which is the point — and which
	// leaves TempDir unable to remove it. Cleanups run last-registered
	// first, so this one restores write access before TempDir's own runs.
	t.Cleanup(func() { restoreWritable(dir) })

	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")
	cfg.Owner = config.Ownership{UID: -1, GID: -1, DirMode: 0o755, FileMode: 0o644}
	return cfg
}

func restoreWritable(root string) {
	_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil // best effort: this is cleanup, not an assertion
		}
		if d.Type()&fs.ModeSymlink != 0 {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		_ = os.Chmod(path, info.Mode().Perm()|0o200)
		return nil
	})
}

func testProfile(t *testing.T) *profile.Profile {
	t.Helper()
	p := &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "testgame",
		Classes: []profile.Class{
			{Name: "pak", Kind: profile.KindManaged, Paths: []string{"WS/Content/Paks"},
				MemoryMin: 150 << 20, NoExec: true},
			{Name: "code", Kind: profile.KindManaged, Paths: []string{"Engine", "WS/Binaries"},
				MemoryMin: 200 << 20},
			{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS/Saved"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

var content = map[string]string{
	"Engine/libengine.so":      "engine bytes",
	"WS/Binaries/server":       "server bytes",
	"WS/Content/Paks/game.pak": "pak bytes",
	"WS/Content/Paks/game.sig": "sig",
	"WS/Saved/world.db":        "never published",
}

// stagedRelease promotes a real release, so the worker is exercised against
// the actual store format rather than a hand-built stand-in.
func stagedRelease(t *testing.T, cfg config.Config) *store.Release {
	t.Helper()
	fixed := time.Date(2026, 8, 3, 12, 0, 0, 0, time.UTC)
	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixed }),
		journal.WithJournaldSocket(""))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = jnl.Close() })

	st, err := store.Open(cfg, jnl)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := st.Begin("op-stage")
	if err != nil {
		t.Fatal(err)
	}
	for rel, body := range content {
		full := filepath.Join(tx.Root, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	release, err := st.Promote(tx, testProfile(t), store.PromoteOpts{
		ReleaseID:  "rel-1",
		Provenance: store.Provenance{Kind: store.ProvenanceStaged},
	})
	if err != nil {
		t.Fatal(err)
	}
	return release
}

// runPrepare runs the worker's work through the ARGV the daemon would build,
// which is the point: the argv construction and the flag parsing are one
// contract, and testing them separately is how they drift.
func runPrepare(t *testing.T, args WorkerArgs) (int, string) {
	t.Helper()
	var stderr bytes.Buffer
	code := prepare(args.Argv()[1:], &stderr)
	return code, stderr.String()
}

// --- what the worker produces ---------------------------------------------

func TestWorkerPopulatesVerifiesAndSealsItsOwnClass(t *testing.T) {
	cfg := testConfig(t)
	rel := stagedRelease(t, cfg)
	target := filepath.Join(cfg.RunDir, ".op", "op-1", "pak", "root")

	code, msg := runPrepare(t, WorkerArgs{
		ReleaseDir: rel.Dir, ReleaseID: rel.ID, Class: "pak", Target: target})
	if code != ExitOK {
		t.Fatalf("worker exited %d: %s", code, msg)
	}

	body, err := os.ReadFile(filepath.Join(target, "WS", "Content", "Paks", "game.pak"))
	if err != nil {
		t.Fatalf("pak content is missing: %v", err)
	}
	if string(body) != "pak bytes" {
		t.Errorf("pak content is %q", body)
	}
	// The structural ancestors exist, or the class path could not.
	for _, dir := range []string{"WS", "WS/Content", "WS/Content/Paks"} {
		if st, err := os.Stat(filepath.Join(target, filepath.FromSlash(dir))); err != nil || !st.IsDir() {
			t.Errorf("structural directory %q is missing: %v", dir, err)
		}
	}
	// And nothing from another class, nor any per-instance state: the
	// absolute state rule is that WS/Saved is never shared at all.
	for _, foreign := range []string{"Engine", "WS/Binaries", "WS/Saved"} {
		if _, err := os.Stat(filepath.Join(target, filepath.FromSlash(foreign))); !os.IsNotExist(err) {
			t.Errorf("the pak class was populated with %q: %v", foreign, err)
		}
	}

	// Sealed: this is what makes "nothing visible was ever writable" true
	// even of the source the exposure binds.
	err = filepath.WalkDir(target, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if info.Mode()&fs.ModeSymlink != 0 {
			return nil
		}
		if info.Mode().Perm()&0o222 != 0 {
			t.Errorf("%s is still writable (%v) after sealing", path, info.Mode().Perm())
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

// Verification happens against the populated tree and BEFORE anything binds
// it. A class that does not match its manifest never becomes an exposure.
func TestWorkerRefusesContentThatDoesNotMatchTheManifest(t *testing.T) {
	cfg := testConfig(t)
	rel := stagedRelease(t, cfg)

	// Corrupt the store copy after promotion, so what the worker copies is
	// not what the manifest promised.
	pak := filepath.Join(rel.RootDir(), "WS", "Content", "Paks", "game.pak")
	if err := os.Chmod(pak, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(pak, []byte("tampered"), 0o644); err != nil {
		t.Fatal(err)
	}

	code, msg := runPrepare(t, WorkerArgs{
		ReleaseDir: rel.Dir, ReleaseID: rel.ID, Class: "pak",
		Target: filepath.Join(cfg.RunDir, ".op", "op-1", "pak", "root")})
	if code != ExitVerify {
		t.Fatalf("worker exited %d, want %d for content that does not verify: %s",
			code, ExitVerify, msg)
	}
	if !strings.Contains(msg, "manifest") {
		t.Errorf("the worker did not say why it refused: %s", msg)
	}
}

// A class the manifest knows nothing about is a fault, not an empty success:
// publishing it would produce an exposure with no content behind it.
func TestWorkerRefusesAClassTheManifestDoesNotName(t *testing.T) {
	cfg := testConfig(t)
	rel := stagedRelease(t, cfg)

	code, _ := runPrepare(t, WorkerArgs{
		ReleaseDir: rel.Dir, ReleaseID: rel.ID, Class: "nosuchclass",
		Target: filepath.Join(cfg.RunDir, ".op", "op-1", "x", "root")})
	if code != ExitFault {
		t.Fatalf("worker exited %d for an unknown class, want %d", code, ExitFault)
	}
}

func TestWorkerRefusesAMalformedInvocation(t *testing.T) {
	cfg := testConfig(t)
	rel := stagedRelease(t, cfg)
	full := WorkerArgs{ReleaseDir: rel.Dir, ReleaseID: rel.ID, Class: "pak",
		Target: filepath.Join(cfg.RunDir, "t")}

	cases := map[string][]string{
		"no target":       {"--release-dir", full.ReleaseDir, "--release-id", full.ReleaseID, "--class", "pak"},
		"no class":        {"--release-dir", full.ReleaseDir, "--release-id", full.ReleaseID, "--target", full.Target},
		"relative target": {"--release-dir", full.ReleaseDir, "--release-id", full.ReleaseID, "--class", "pak", "--target", "run/t"},
		"unknown flag":    {"--release-dir", full.ReleaseDir, "--nope", "x"},
		"no arguments":    {},
	}
	for name, args := range cases {
		var stderr bytes.Buffer
		if code := prepare(args, &stderr); code != ExitUsage {
			t.Errorf("%s: worker exited %d, want %d", name, code, ExitUsage)
		}
	}
}

// The release directory the daemon points at has to BE the release it named.
func TestWorkerRefusesAReleaseItCannotLoad(t *testing.T) {
	cfg := testConfig(t)
	rel := stagedRelease(t, cfg)

	code, msg := runPrepare(t, WorkerArgs{
		ReleaseDir: filepath.Join(rel.Dir, "nonexistent"), ReleaseID: rel.ID,
		Class: "pak", Target: filepath.Join(cfg.RunDir, "t")})
	if code != ExitFault {
		t.Fatalf("worker exited %d for an unloadable release, want %d: %s", code, ExitFault, msg)
	}
}

// A target the worker cannot write into is a FAULT, not a refusal. Only
// running out of space quarantines a generation; everything else has to stay
// distinguishable from it, or a broken host looks like content that does not
// fit.
//
// The target is made a regular file rather than an unwritable directory:
// permissions do not stop root, and this suite runs as root under the
// privileged harness.
func TestWorkerReportsAnUnusableTargetAsAFault(t *testing.T) {
	cfg := testConfig(t)
	rel := stagedRelease(t, cfg)

	target := filepath.Join(t.TempDir(), "not-a-directory")
	if err := os.WriteFile(target, []byte("in the way"), 0o644); err != nil {
		t.Fatal(err)
	}

	code, msg := runPrepare(t, WorkerArgs{
		ReleaseDir: rel.Dir, ReleaseID: rel.ID, Class: "pak", Target: target})
	if code != ExitFault {
		t.Fatalf("worker exited %d for an unusable target, want %d: %s", code, ExitFault, msg)
	}
	if code == ExitNoSpace {
		t.Error("a target that could not be written was reported as running out of space, " +
			"which would quarantine the generation for a fault in the host")
	}
}

// --- sealing ---------------------------------------------------------------

// Sealing a tree that is not there has to fail. Returning nil would let
// publication proceed to bind a class nothing ever wrote.
func TestSealReportsAMissingTree(t *testing.T) {
	if err := Seal(filepath.Join(t.TempDir(), "nonexistent")); err == nil {
		t.Fatal("sealing a tree that does not exist reported success")
	}
}

// chmod follows symlinks. Sealing a tree that contains one must not reach
// through it and strip write access from whatever it points at — which could
// be anything, including a file outside the class entirely.
func TestSealDoesNotFollowSymlinks(t *testing.T) {
	dir := t.TempDir()
	t.Cleanup(func() { restoreWritable(dir) })

	outside := filepath.Join(dir, "outside")
	if err := os.WriteFile(outside, []byte("not ours"), 0o644); err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(dir, "root")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}

	if err := Seal(root); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(outside)
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm()&0o200 == 0 {
		t.Fatalf("sealing followed a symlink and made %s read-only (%v)", outside, st.Mode().Perm())
	}
}

// --- the readiness signal --------------------------------------------------

// sd_notify with no library: one datagram to $NOTIFY_SOCKET. This is what
// makes "active" mean "populated" rather than "exec'd" (D-013), so it is
// worth a test of its own rather than only appearing in the e2e path.
func TestNotifyReadySendsTheReadyDatagram(t *testing.T) {
	sock := filepath.Join(t.TempDir(), "notify")
	conn, err := net.ListenPacket("unixgram", sock)
	if err != nil {
		t.Fatalf("listening on %s: %v", sock, err)
	}
	defer conn.Close()
	t.Setenv("NOTIFY_SOCKET", sock)

	if err := notifyReady(); err != nil {
		t.Fatal(err)
	}
	// A budget, not an oracle: the datagram has already been written when
	// notifyReady returns, so expiry here means it never arrived at all.
	if err := conn.SetReadDeadline(time.Now().Add(10 * time.Second)); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 64)
	n, _, err := conn.ReadFrom(buf)
	if err != nil {
		t.Fatalf("no readiness datagram arrived: %v", err)
	}
	if got := strings.TrimSpace(string(buf[:n])); got != "READY=1" {
		t.Fatalf("the worker sent %q, want READY=1", got)
	}
}

// A worker that failed must never declare itself ready. "Active" means
// "populated" only because the signal comes after the work (D-013); a signal
// sent regardless would make it mean "started" again, and the daemon would
// bind a class that does not exist.
func TestAFailedWorkerNeverSignalsReady(t *testing.T) {
	sock := filepath.Join(t.TempDir(), "notify")
	conn, err := net.ListenPacket("unixgram", sock)
	if err != nil {
		t.Fatalf("listening on %s: %v", sock, err)
	}
	defer conn.Close()
	t.Setenv("NOTIFY_SOCKET", sock)

	var stderr bytes.Buffer
	if code := RunWorker([]string{"--class", "pak"}, &stderr); code != ExitUsage {
		t.Fatalf("RunWorker exited %d for a malformed invocation, want %d", code, ExitUsage)
	}

	// Checked with no waiting at all, which is the strict reading: the signal
	// is written synchronously before RunWorker returns, so if one were
	// coming it would already be here. Waiting could only help this pass.
	if err := conn.SetReadDeadline(time.Now()); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 64)
	if n, _, err := conn.ReadFrom(buf); err == nil {
		t.Fatalf("a worker that never populated anything signalled %q", buf[:n])
	}
}

// Without a socket the worker cannot be a Type=notify unit, and silently
// carrying on would make "active" mean "exec'd" again — the exact race D-013
// closed.
func TestNotifyReadyRefusesWithoutASocket(t *testing.T) {
	t.Setenv("NOTIFY_SOCKET", "")
	err := notifyReady()
	if err == nil {
		t.Fatal("the worker declared itself ready with nowhere to say so")
	}
	if !strings.Contains(err.Error(), "notify") {
		t.Errorf("the error does not explain what is missing: %v", err)
	}
}
