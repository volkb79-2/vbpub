package store

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/journal"
	"srdm/internal/profile"
)

// --- O2: COMPLETE is last and means it ------------------------------------

func TestPromotedReleaseVerifiesAndIsReachable(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)

	rel := promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)

	if _, err := os.Stat(filepath.Join(rel.Dir, CompleteFile)); err != nil {
		t.Fatalf("%s is missing from a promoted release: %v", CompleteFile, err)
	}
	if err := st.Verify("rel-1"); err != nil {
		t.Fatalf("a freshly promoted release does not verify: %v", err)
	}
	got, err := st.Resolve("testgame", "stable")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if got != "rel-1" {
		t.Fatalf("channel resolves to %q, want %q", got, "rel-1")
	}
	// The transaction became the release; nothing is left behind to recover.
	if entries, err := os.ReadDir(st.cfg.TxDir()); err != nil {
		t.Fatal(err)
	} else if len(entries) != 0 {
		t.Fatalf("transaction directory is not empty after promotion: %v", entries)
	}
}

// The negative half of O2: if COMPLETE could exist beside content that does
// not match the manifest, verification would be decoration. These two cases
// prove Verify actually discriminates.
func TestVerifyRejectsMismatchedAndMissingFiles(t *testing.T) {
	t.Run("mismatched file", func(t *testing.T) {
		dir := t.TempDir()
		st := newTestStore(t, dir)
		rel := promoteContent(t, st, testProfile(t), "op-1", "rel-1", "stable", contentA)

		target := filepath.Join(rel.RootDir(), "WS", "Content", "Paks", "game.pak")
		original, err := os.ReadFile(target)
		if err != nil {
			t.Fatal(err)
		}
		// Flip one byte, keeping the size identical.
		mutated := append([]byte(nil), original...)
		mutated[0] ^= 0xff
		if err := os.WriteFile(target, mutated, 0o644); err != nil {
			t.Fatal(err)
		}

		err = st.Verify("rel-1")
		if err == nil {
			t.Fatal("Verify passed a release whose content was rewritten")
		}
		if !strings.Contains(err.Error(), "game.pak") {
			t.Fatalf("the error does not name the offending path: %v", err)
		}
	})

	t.Run("missing file", func(t *testing.T) {
		dir := t.TempDir()
		st := newTestStore(t, dir)
		rel := promoteContent(t, st, testProfile(t), "op-1", "rel-1", "stable", contentA)

		if err := os.Remove(filepath.Join(rel.RootDir(), "WS", "Content", "Paks", "game.sig")); err != nil {
			t.Fatal(err)
		}
		err := st.Verify("rel-1")
		if err == nil {
			t.Fatal("Verify passed a release with a file missing")
		}
		if !strings.Contains(err.Error(), "game.sig") {
			t.Fatalf("the error does not name the missing path: %v", err)
		}
	})

	t.Run("extra file", func(t *testing.T) {
		dir := t.TempDir()
		st := newTestStore(t, dir)
		rel := promoteContent(t, st, testProfile(t), "op-1", "rel-1", "stable", contentA)

		extra := filepath.Join(rel.RootDir(), "WS", "Content", "Paks", "smuggled.pak")
		if err := os.WriteFile(extra, []byte("not in the manifest"), 0o644); err != nil {
			t.Fatal(err)
		}
		err := st.Verify("rel-1")
		if err == nil {
			t.Fatal("Verify passed a release with content nobody hashed")
		}
		if !strings.Contains(err.Error(), "smuggled.pak") {
			t.Fatalf("the error does not name the extra path: %v", err)
		}
	})
}

// A release directory without COMPLETE is not a release, however complete it
// looks. This is what lets Recover treat the file as the only signal.
func TestReleaseWithoutCompleteIsNotUsable(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	rel := promoteContent(t, st, testProfile(t), "op-1", "rel-1", "stable", contentA)

	if err := os.Remove(filepath.Join(rel.Dir, CompleteFile)); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Load("rel-1"); err == nil {
		t.Fatal("a release with no COMPLETE loaded successfully")
	} else if !strings.Contains(err.Error(), CompleteFile) {
		t.Fatalf("the error does not mention %s: %v", CompleteFile, err)
	}
}

// COMPLETE pins the manifest by hash, so swapping the manifest for another
// valid one is caught rather than silently accepted.
func TestCompletePinsTheManifest(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	rel := promoteContent(t, st, testProfile(t), "op-1", "rel-1", "stable", contentA)

	body, err := os.ReadFile(filepath.Join(rel.Dir, ManifestFile))
	if err != nil {
		t.Fatal(err)
	}
	// A byte-level edit that keeps the document valid JSON but changes it.
	edited := strings.Replace(string(body), `"profile": "testgame"`, `"profile": "othergame"`, 1)
	if edited == string(body) {
		t.Fatal("test setup: the manifest did not contain the expected profile field")
	}
	if err := os.WriteFile(filepath.Join(rel.Dir, ManifestFile), []byte(edited), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Load("rel-1"); err == nil {
		t.Fatal("a release whose manifest was swapped loaded successfully")
	}
}

// --- O3: an unclassified path blocks promotion ----------------------------

func TestUnclassifiedPathBlocksPromotion(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)

	// Establish a previous release so we can prove the refusal leaves the
	// store exactly as it was.
	promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)

	files := map[string]string{}
	for k, v := range contentA {
		files[k] = v
	}
	files["WS/notes.txt"] = "a path no rule claims"

	tx, err := st.Begin("op-2")
	if err != nil {
		t.Fatal(err)
	}
	writeTree(t, tx.Root, files)

	_, err = st.Promote(tx, p, PromoteOpts{ReleaseID: "rel-2", Channel: "stable"})
	if err == nil {
		t.Fatal("an unclassified path was promoted")
	}

	var unclassified *profile.UnclassifiedError
	if !errors.As(err, &unclassified) {
		t.Fatalf("want an UnclassifiedError, got %T: %v", err, err)
	}
	// The error must name both, or the operator cannot act on it.
	if unclassified.Path != "WS/notes.txt" {
		t.Errorf("error names path %q, want %q", unclassified.Path, "WS/notes.txt")
	}
	if unclassified.Profile != "testgame" {
		t.Errorf("error names profile %q, want %q", unclassified.Profile, "testgame")
	}
	if msg := err.Error(); !strings.Contains(msg, "WS/notes.txt") || !strings.Contains(msg, "testgame") {
		t.Errorf("the message does not carry both path and profile: %s", msg)
	}

	// The negative: it must not have landed in a default class.
	if ids, err := st.List(); err != nil {
		t.Fatal(err)
	} else if len(ids) != 1 || ids[0] != "rel-1" {
		t.Fatalf("the store gained a release from a refused promotion: %v", ids)
	}
	if got, err := st.Resolve("testgame", "stable"); err != nil {
		t.Fatal(err)
	} else if got != "rel-1" {
		t.Fatalf("the channel moved during a refused promotion: %q", got)
	}

	// A refusal is journaled as a refusal, not as a fault.
	assertJournalOutcome(t, st, "op-2", string(PhaseClassify), journal.OutcomeRefused)
}

func TestExcludedAndStructuralPathsDoNotBlockPromotion(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)

	files := map[string]string{}
	for k, v := range contentA {
		files[k] = v
	}
	// Per-instance state is classified (excluded), so it does not refuse —
	// and WS and WS/Content are structural directories nobody declares.
	files["WS/Saved/world.db"] = "never shared"

	rel := promoteContent(t, st, p, "op-1", "rel-1", "stable", files)

	classOf := map[string]string{}
	for _, e := range rel.Manifest.Entries {
		classOf[e.Path] = e.Class
	}
	for path, want := range map[string]string{
		"WS":                       "structure",
		"WS/Content":               "structure",
		"WS/Content/Paks/game.pak": "pak",
		"Engine/libengine.so":      "code",
		"WS/Saved/world.db":        "state",
	} {
		if got := classOf[path]; got != want {
			t.Errorf("%q classified as %q, want %q", path, got, want)
		}
	}
}

// The ancestor rule must not become a hole: a *file* sitting where a
// structural directory would be is still unclassified.
func TestFileAtAStructuralPathIsRefused(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)

	tx, err := st.Begin("op-1")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tx.Root, "WS"), []byte("not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Promote(tx, p, PromoteOpts{ReleaseID: "rel-1"}); err == nil {
		t.Fatal("a regular file at a structural path was promoted")
	} else if !strings.Contains(err.Error(), "not a directory") {
		t.Fatalf("unexpected error: %v", err)
	}
}

// --- probes ---------------------------------------------------------------

func TestProbeFailureRefusesPromotion(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)
	p.Probes = []profile.Probe{
		{Name: "pak-present", Kind: profile.ProbeNonEmpty, Path: "WS/Content/Paks/game.pak"},
		{Name: "no-staging-left", Kind: profile.ProbeAbsent, Path: "WS/Content/Paks/game.sig"},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}

	tx, err := st.Begin("op-1")
	if err != nil {
		t.Fatal(err)
	}
	writeTree(t, tx.Root, contentA)

	_, err = st.Promote(tx, p, PromoteOpts{ReleaseID: "rel-1"})
	if err == nil {
		t.Fatal("promotion succeeded despite a failing probe")
	}
	var pf *ProbeFailure
	if !errors.As(err, &pf) {
		t.Fatalf("want a ProbeFailure, got %T: %v", err, err)
	}
	if pf.Probe.Name != "no-staging-left" {
		t.Fatalf("the wrong probe failed: %q", pf.Probe.Name)
	}
	if !IsRefusal(err) {
		t.Error("a failing probe is a refusal, not a fault")
	}
	assertJournalOutcome(t, st, "op-1", string(PhaseProbes), journal.OutcomeRefused)
}

// --- activation -----------------------------------------------------------

func TestActivateRefusesAnUnverifiableRelease(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)
	promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)
	rel2 := promoteContent(t, st, p, "op-2", "rel-2", "", contentA)

	// Break rel-2 the way an interrupted write would.
	if err := os.Remove(filepath.Join(rel2.Dir, CompleteFile)); err != nil {
		t.Fatal(err)
	}
	if err := st.Activate("op-3", "testgame", "stable", "rel-2"); err == nil {
		t.Fatal("a channel was pointed at a release with no COMPLETE")
	}
	if got, err := st.Resolve("testgame", "stable"); err != nil {
		t.Fatal(err)
	} else if got != "rel-1" {
		t.Fatalf("the channel moved despite the refusal: %q", got)
	}
}

func TestActivateIsAtomicAndRelative(t *testing.T) {
	dir := t.TempDir()
	st := newTestStore(t, dir)
	p := testProfile(t)
	promoteContent(t, st, p, "op-1", "rel-1", "stable", contentA)
	promoteContent(t, st, p, "op-2", "rel-2", "", contentA)

	if err := st.Activate("op-3", "testgame", "stable", "rel-2"); err != nil {
		t.Fatalf("Activate: %v", err)
	}
	got, err := st.Resolve("testgame", "stable")
	if err != nil {
		t.Fatal(err)
	}
	if got != "rel-2" {
		t.Fatalf("channel resolves to %q, want rel-2", got)
	}
	// A relative link keeps the whole store relocatable.
	link, err := os.Readlink(st.cfg.ChannelLink("testgame", "stable"))
	if err != nil {
		t.Fatal(err)
	}
	if filepath.IsAbs(link) {
		t.Fatalf("channel symlink is absolute (%q); the store would not survive a move", link)
	}
	// No temporary link is left behind.
	entries, err := os.ReadDir(filepath.Dir(st.cfg.ChannelLink("testgame", "stable")))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".") {
			t.Fatalf("a temporary link survived the flip: %q", e.Name())
		}
	}
}

// --- helpers --------------------------------------------------------------

func assertJournalOutcome(t *testing.T, st *Store, opID, phase string, want journal.Outcome) {
	t.Helper()
	op, err := journal.LoadOperation(st.cfg.JournalDir(), opID)
	if err != nil {
		t.Fatalf("no durable record for operation %q: %v", opID, err)
	}
	for _, ev := range op.Events {
		if ev.Phase == phase && ev.Outcome == want {
			return
		}
	}
	t.Fatalf("operation %q has no %s record for phase %q; events: %+v", opID, want, phase, op.Events)
}
