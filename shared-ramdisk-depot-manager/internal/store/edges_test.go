package store

import (
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/config"
	"srdm/internal/profile"
)

// --- transaction lifecycle -------------------------------------------------

func TestBeginRefusesADuplicateTransaction(t *testing.T) {
	st := newTestStore(t, t.TempDir())
	if _, err := st.Begin("op-1"); err != nil {
		t.Fatal(err)
	}
	// Reusing an operation id would let a second run write into a directory
	// the first is still populating.
	if _, err := st.Begin("op-1"); err == nil {
		t.Fatal("a second transaction opened under the same operation id")
	}
}

func TestBeginRefusesUnsafeOperationIds(t *testing.T) {
	st := newTestStore(t, t.TempDir())
	for _, id := range []string{"", ".", "..", "../escape", "a/b"} {
		if _, err := st.Begin(id); err == nil {
			t.Errorf("Begin(%q) was accepted; ids become path components", id)
		}
	}
}

func TestDiscardRemovesTheTransaction(t *testing.T) {
	st := newTestStore(t, t.TempDir())
	tx, err := st.Begin("op-1")
	if err != nil {
		t.Fatal(err)
	}
	writeTree(t, tx.Root, contentA)

	if err := st.Discard(tx); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(tx.Dir); !os.IsNotExist(err) {
		t.Fatalf("the transaction directory survived Discard: %v", err)
	}
}

// --- promotion refusals ----------------------------------------------------

func TestPromoteRefusesAnExistingReleaseId(t *testing.T) {
	st := newTestStore(t, t.TempDir())
	p := testProfile(t)
	promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)

	tx, err := st.Begin("op-2")
	if err != nil {
		t.Fatal(err)
	}
	writeTree(t, tx.Root, contentA)
	// Releases are immutable. Overwriting one would change content under a
	// channel that already points at it.
	if _, err := st.Promote(tx, p, PromoteOpts{ReleaseID: "rel-1"}); err == nil {
		t.Fatal("an existing release id was overwritten")
	} else if !strings.Contains(err.Error(), "already exists") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestPromoteRefusesUnsafeNames(t *testing.T) {
	st := newTestStore(t, t.TempDir())
	p := testProfile(t)

	tx, err := st.Begin("op-1")
	if err != nil {
		t.Fatal(err)
	}
	writeTree(t, tx.Root, contentA)

	if _, err := st.Promote(tx, p, PromoteOpts{ReleaseID: "../escape"}); err == nil {
		t.Error("a traversing release id was accepted")
	}
	if _, err := st.Promote(tx, p, PromoteOpts{ReleaseID: "rel-1", Channel: "../escape"}); err == nil {
		t.Error("a traversing channel was accepted")
	}

	bad := newTestProfile()
	bad.ID = "../escape"
	if _, err := st.Promote(tx, bad, PromoteOpts{ReleaseID: "rel-1"}); err == nil {
		t.Error("a traversing profile id was accepted")
	}
}

func TestActivateRefusesUnsafeNames(t *testing.T) {
	st := newTestStore(t, t.TempDir())
	if err := st.Activate("op-1", "../escape", "stable", "rel-1"); err == nil {
		t.Error("a traversing profile id was accepted")
	}
	if err := st.Activate("op-1", "testgame", "..", "rel-1"); err == nil {
		t.Error("a traversing channel was accepted")
	}
}

// --- the ownership walk is actually wired ---------------------------------

func TestWithChownRoutesTheOwnershipWalk(t *testing.T) {
	dir := t.TempDir()
	cfg := testConfig(dir)
	// A configured owner is what makes the walk do anything at all.
	cfg.Owner = config.Ownership{UID: 4242, GID: 4243, DirMode: 0o755, FileMode: 0o644}

	var chowned []string
	st, err := Open(cfg, newTestJournal(t, cfg), WithChown(func(name string, uid, gid int) error {
		if uid != 4242 || gid != 4243 {
			t.Errorf("chown(%q) got %d:%d, want 4242:4243", name, uid, gid)
		}
		chowned = append(chowned, name)
		return nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	if got := st.Config().Owner.UID; got != 4242 {
		t.Errorf("Config().Owner.UID = %d", got)
	}

	promoteContent(t, st, testProfile(t), "op-1", "rel-1", "stable", contentA)

	if len(chowned) == 0 {
		t.Fatal("promotion normalized no ownership despite a configured owner")
	}
	for _, path := range chowned {
		if !strings.Contains(path, "root") {
			t.Errorf("chowned something outside the content root: %q", path)
		}
	}
}

// --- probes ----------------------------------------------------------------

func TestProbeKinds(t *testing.T) {
	dir := t.TempDir()
	writeTree(t, dir, contentA)

	p := newTestProfile()
	cases := []struct {
		name      string
		probe     profile.Probe
		wantFail  bool
		wantMatch string
	}{
		{"exists holds", profile.Probe{Name: "a", Kind: profile.ProbeExists, Path: "Engine/libengine.so"}, false, ""},
		{"exists fails", profile.Probe{Name: "b", Kind: profile.ProbeExists, Path: "Engine/absent.so"}, true, "missing"},
		{"absent holds", profile.Probe{Name: "c", Kind: profile.ProbeAbsent, Path: "Engine/absent.so"}, false, ""},
		{"absent fails", profile.Probe{Name: "d", Kind: profile.ProbeAbsent, Path: "Engine/libengine.so"}, true, "present"},
		{"non-empty holds", profile.Probe{Name: "e", Kind: profile.ProbeNonEmpty, Path: "Engine/libengine.so"}, false, ""},
		{"non-empty fails on a directory", profile.Probe{Name: "f", Kind: profile.ProbeNonEmpty, Path: "Engine"}, true, "not a regular file"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p.Probes = []profile.Probe{tc.probe}
			err := runProbes(dir, p)
			if tc.wantFail {
				if err == nil {
					t.Fatal("the probe passed when it should have failed")
				}
				if !strings.Contains(err.Error(), tc.wantMatch) {
					t.Fatalf("error %q does not explain the failure (%q)", err, tc.wantMatch)
				}
				if !IsRefusal(err) {
					t.Error("a failing probe must be a refusal, not a fault")
				}
				return
			}
			if err != nil {
				t.Fatalf("the probe failed when it should have passed: %v", err)
			}
		})
	}
}

func TestEmptyFileFailsNonEmptyProbe(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "empty"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	p := newTestProfile()
	p.Probes = []profile.Probe{{Name: "e", Kind: profile.ProbeNonEmpty, Path: "empty"}}

	err := runProbes(dir, p)
	if err == nil || !strings.Contains(err.Error(), "empty") {
		t.Fatalf("an empty file passed a non-empty probe: %v", err)
	}
}

func TestUnknownProbeKindIsAFaultNotARefusal(t *testing.T) {
	p := newTestProfile()
	p.Probes = []profile.Probe{{Name: "x", Kind: "vibes", Path: "."}}

	err := runProbes(t.TempDir(), p)
	if err == nil {
		t.Fatal("an unknown probe kind was accepted")
	}
	// A profile srdm cannot understand is a fault: the operator's intent is
	// unknown, so "refused by contract" would misdescribe it.
	if IsRefusal(err) {
		t.Error("an unknown probe kind was reported as a refusal")
	}
}

// --- recovery edges --------------------------------------------------------

func TestRecoverQuarantinesAReleaseMissingComplete(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)
	rel := promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)
	promoteContent(t, st, p, "op-2", "rel-2", "", contentA)

	// Simulate a release directory that lost its COMPLETE.
	if err := os.Remove(filepath.Join(st.cfg.ReleaseDir("rel-2"), CompleteFile)); err != nil {
		t.Fatal(err)
	}

	report, err := st.Recover("op-recover")
	if err != nil {
		t.Fatal(err)
	}
	if len(report.Quarantined) != 1 || report.Quarantined[0] != "rel-2" {
		t.Fatalf("Quarantined = %v, want [rel-2]", report.Quarantined)
	}
	if _, err := os.Stat(st.cfg.ReleaseDir("rel-2")); !os.IsNotExist(err) {
		t.Error("the broken release is still in releases/")
	}
	if _, err := os.Stat(filepath.Join(st.cfg.QuarantineDir(), "rel-2")); err != nil {
		t.Errorf("the broken release is not in quarantine: %v", err)
	}
	// The good release and its channel are untouched.
	if got := report.Channels["testgame/stable"]; got != "rel-1" {
		t.Errorf("channel resolves to %q, want rel-1", got)
	}
	if err := st.Verify(rel.ID); err != nil {
		t.Errorf("the healthy release was disturbed: %v", err)
	}
}

func TestRecoverReportsADanglingChannel(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)
	promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)

	// Remove the release the channel points at, leaving the link behind.
	if err := os.RemoveAll(st.cfg.ReleaseDir("rel-1")); err != nil {
		t.Fatal(err)
	}

	report, err := st.Recover("op-recover")
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := report.DanglingChannels["testgame/stable"]; !ok {
		t.Fatalf("a channel pointing at nothing was not reported: %+v", report)
	}
	if _, ok := report.Channels["testgame/stable"]; ok {
		t.Error("a dangling channel was also reported as resolving")
	}
	// Recovery never repoints a channel: it decides what exists, an
	// operator decides what is active.
	if _, err := os.Lstat(st.cfg.ChannelLink("testgame", "stable")); err != nil {
		t.Errorf("recovery removed the channel link: %v", err)
	}
}

func TestQuarantineNamesDoNotCollide(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)

	for i := range 3 {
		tx, err := st.Begin("op-dup")
		if err != nil {
			t.Fatalf("round %d: %v", i, err)
		}
		writeTree(t, tx.Root, contentA)
		if _, err := st.Recover("op-recover"); err != nil {
			t.Fatalf("round %d: %v", i, err)
		}
	}

	for _, want := range []string{"op-dup", "op-dup.1", "op-dup.2"} {
		if _, err := os.Stat(filepath.Join(st.cfg.QuarantineDir(), want)); err != nil {
			// Deterministic counter suffixes, not timestamps: the same
			// recovery over the same store always produces the same layout.
			t.Errorf("quarantine is missing %q: %v", want, err)
		}
	}
}

// --- what a release may contain -------------------------------------------

func TestManifestRefusesASocket(t *testing.T) {
	dir := t.TempDir()
	writeTree(t, dir, contentA)

	sock := filepath.Join(dir, "Engine", "s.sock")
	l, err := net.Listen("unix", sock)
	if err != nil {
		t.Skipf("cannot create a unix socket here: %v", err)
	}
	defer l.Close()

	_, err = BuildManifest(dir, testProfile(t))
	if err == nil {
		t.Fatal("a unix socket was accepted into a release manifest")
	}
	if !strings.Contains(err.Error(), "socket") {
		t.Fatalf("the error does not say what it found: %v", err)
	}
}
