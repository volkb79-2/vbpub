package harvest

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

func ctx() context.Context { return context.Background() }

// --- the fixture ----------------------------------------------------------
//
// A whole miniature node with no privilege in it: the class "tmpfs" mounts
// are ordinary directories, and the mount table they are supposed to appear
// in is a file. That is enough for everything harvest decides — what it
// refuses, what it classifies, and what the release ends up containing. What
// it cannot say is whether the pages are real, which is what the e2e suite
// is for.

const (
	sourceRelease = "rel-source"
	opID          = "op-live"
	genID         = "abcd1234"
)

type fixture struct {
	cfg  config.Config
	jnl  *journal.Journal
	st   *store.Store
	prof *profile.Profile
	rec  *publish.Record
	// table is the synthetic mount table; classes are mounted unless a test
	// asks otherwise.
	table string
	guard *fakeGuard
}

// classTrees is what each class's op tmpfs holds, as a published generation
// would have it: that class's own paths plus the structural directories on
// the way to them.
var classTrees = map[string]map[string]string{
	"code": {
		"Engine/libengine.so": "engine bytes",
		"WS/Binaries/server":  "server bytes",
	},
	"pak": {
		"WS/Content/Paks/game.pak": "pak bytes",
	},
}

func newFixture(t *testing.T) *fixture {
	t.Helper()

	dir := t.TempDir()
	cfg := config.Default()
	cfg.StateDir = filepath.Join(dir, "state")
	cfg.RunDir = filepath.Join(dir, "run")

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

	f := &fixture{cfg: cfg, jnl: jnl, st: st, prof: testProfile(t),
		guard: &fakeGuard{txDir: cfg.TxDir()}}
	f.rec = f.publishedRecord(t)
	f.table = f.mountTable(t, "code", "pak")
	return f
}

func testProfile(t *testing.T) *profile.Profile {
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

// publishedRecord materializes the class trees and the record that names
// them, which is everything harvest reads about a live generation.
func (f *fixture) publishedRecord(t *testing.T) *publish.Record {
	t.Helper()
	rec := &publish.Record{
		SchemaVersion: publish.RecordSchemaVersion,
		Generation:    genID,
		ReleaseID:     sourceRelease,
		Profile:       "testgame",
		OperationID:   opID,
		Slice:         "srdm-" + genID + ".slice",
		DirtyCapable:  true,
	}
	for _, class := range []string{"code", "pak"} {
		rec.Classes = append(rec.Classes, publish.ClassRecord{
			Name:       class,
			OpMount:    f.cfg.OpClassDir(opID, class),
			ExposePath: f.cfg.ExposeClassDir("testgame", genID, class),
			HoldUnit:   "srdm-hold-" + genID + "-" + class + ".service",
		})
		for rel, body := range classTrees[class] {
			f.write(t, class, rel, body)
		}
	}
	return rec
}

// write puts one path into a class's live tree, read-only exactly as
// publication's seal leaves it.
//
// The unlink first is what an updater does: publication seals a class tree
// a-w, so a file is REPLACED rather than rewritten in place. Writing over the
// old inode would need a mode the seal removed.
func (f *fixture) write(t *testing.T, class, rel, body string) {
	t.Helper()
	full := filepath.Join(f.cfg.OpClassRoot(opID, class), filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(full); err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	if err := os.WriteFile(full, []byte(body), 0o444); err != nil {
		t.Fatal(err)
	}
}

// sealDirs finishes the job publication's seal does, which the per-file
// writes above leave half done. Its own cleanup restores write access, or
// TempDir cannot remove the tree it made.
func (f *fixture) sealDirs(t *testing.T) {
	t.Helper()
	t.Cleanup(func() {
		_ = filepath.Walk(f.cfg.RunDir, func(p string, info os.FileInfo, err error) error {
			if err == nil && info.IsDir() {
				_ = os.Chmod(p, info.Mode().Perm()|0o700)
			}
			return nil
		})
	})
	var dirs []string
	err := filepath.Walk(f.cfg.RunDir, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			dirs = append(dirs, p)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	// Deepest first: no step depends on a permission an earlier one removed.
	for i := len(dirs) - 1; i >= 0; i-- {
		if err := os.Chmod(dirs[i], 0o555); err != nil {
			t.Fatal(err)
		}
	}
}

// mountTable renders a mount table in which the named classes are mounted.
func (f *fixture) mountTable(t *testing.T, mounted ...string) string {
	t.Helper()
	var lines []string
	for i, class := range mounted {
		lines = append(lines, fmt.Sprintf(
			"%d 30 0:%d / %s rw,nosuid,nodev - tmpfs tmpfs rw,size=67108864,mode=755",
			40+i, 140+i, f.cfg.OpClassDir(opID, class)))
	}
	path := filepath.Join(t.TempDir(), "mountinfo")
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func (f *fixture) harvester(t *testing.T) *Harvester {
	t.Helper()
	h, err := New(f.cfg, f.jnl, f.st, WithGuard(f.guard), WithMountInfoPath(f.table))
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func (f *fixture) harvest(t *testing.T) (*store.Release, error) {
	t.Helper()
	return f.harvester(t).Harvest(ctx(), f.rec, f.prof, Opts{ReleaseID: "rel-harvested"})
}

// fakeGuard answers the holder question from a script, so the two moments
// harvest asks it can be answered differently.
//
// It also records what had already happened at each ask. Without that, the
// two checks are indistinguishable when a consumer is present the whole time:
// both refuse, both discard, and a harvest that had dropped the first check
// would look identical to one that had not — while having copied the whole
// tree before saying no.
type fakeGuard struct {
	answers []*consumer.Report
	asked   int
	err     error
	// copiedAtAsk[i] is whether any transaction content existed when the
	// i-th question was asked.
	copiedAtAsk []bool
	txDir       string
}

func (g *fakeGuard) Resolve(context.Context, []string) (*consumer.Report, error) {
	if g.err != nil {
		return nil, g.err
	}
	g.copiedAtAsk = append(g.copiedAtAsk, g.anyTransactionContent())
	g.asked++
	if len(g.answers) == 0 {
		return &consumer.Report{}, nil
	}
	if g.asked <= len(g.answers) {
		return g.answers[g.asked-1], nil
	}
	return g.answers[len(g.answers)-1], nil
}

// anyTransactionContent reports whether the store holds a transaction with
// anything in its content root.
func (g *fakeGuard) anyTransactionContent() bool {
	txs, err := os.ReadDir(g.txDir)
	if err != nil {
		return false
	}
	for _, tx := range txs {
		entries, err := os.ReadDir(filepath.Join(g.txDir, tx.Name(), store.RootDir))
		if err == nil && len(entries) > 0 {
			return true
		}
	}
	return false
}

func held() *consumer.Report {
	return &consumer.Report{Holders: []consumer.Holder{{
		Kind: consumer.KindMount, MountPoint: "/var/lib/pterodactyl/volumes/uuid/Engine",
		Device: consumer.Device{Major: 0, Minor: 140}, PID: 4242,
		ContainerName: "soulmask-1",
	}}}
}

// txLeftBehind reports the transaction directories still on disk. A refusal
// must not leave a partial copy of a tmpfs that is still mounted and still
// readable.
func (f *fixture) txLeftBehind(t *testing.T) []string {
	t.Helper()
	entries, err := os.ReadDir(f.cfg.TxDir())
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		out = append(out, e.Name())
	}
	return out
}

// --- oracle 23: the harvest round trip ------------------------------------

// The core claim: what harvest promotes is the tree that is live, described
// exactly as a stage of the same bytes would describe it, and carrying
// provenance that says where it came from.
func TestHarvestPromotesTheLiveTreeWithHarvestedProvenance(t *testing.T) {
	f := newFixture(t)

	// An update in place: a new file, and one of the old ones rewritten.
	f.write(t, "pak", "WS/Content/Paks/patch01.pak", "patch bytes")
	f.write(t, "code", "Engine/libengine.so", "engine bytes v2")

	rel, err := f.harvest(t)
	if err != nil {
		t.Fatalf("Harvest: %v", err)
	}

	if rel.Complete.Provenance.Kind != store.ProvenanceHarvested {
		t.Errorf("provenance kind is %q, want %q",
			rel.Complete.Provenance.Kind, store.ProvenanceHarvested)
	}
	if rel.Complete.Provenance.Source != sourceRelease {
		t.Errorf("provenance source is %q, want the release that was updated in place (%q)",
			rel.Complete.Provenance.Source, sourceRelease)
	}

	// Every live path is in the release, with the live bytes.
	want := map[string]string{
		"Engine/libengine.so":         "engine bytes v2",
		"WS/Binaries/server":          "server bytes",
		"WS/Content/Paks/game.pak":    "pak bytes",
		"WS/Content/Paks/patch01.pak": "patch bytes",
	}
	for rel2, body := range want {
		got, err := os.ReadFile(filepath.Join(rel.RootDir(), filepath.FromSlash(rel2)))
		if err != nil {
			t.Errorf("%s is not in the harvested release: %v", rel2, err)
			continue
		}
		if string(got) != body {
			t.Errorf("%s is %q, want the live content %q", rel2, got, body)
		}
	}

	// And the release verifies against its own manifest — the harvested tree
	// is a first-class release, not a special case downstream.
	if err := f.st.Verify(rel.ID); err != nil {
		t.Errorf("the harvested release does not verify: %v", err)
	}

	// The classes are the ones the profile assigns, not the ones the trees
	// happened to be on.
	byClass := map[string]string{}
	for _, e := range rel.Manifest.Entries {
		byClass[e.Path] = e.Class
	}
	for path, class := range map[string]string{
		"Engine/libengine.so":         "code",
		"WS/Content/Paks/patch01.pak": "pak",
		"WS":                          "structure",
	} {
		if byClass[path] != class {
			t.Errorf("%s is class %q, want %q", path, byClass[path], class)
		}
	}
}

// Publication seals its trees read-only — every file AND every directory. A
// harvest of one must both succeed and come back with the store's own modes:
// a copy that carried 0555 across would fail on the second entry it wrote
// into that directory, and one that carried 0444 across would make every
// republished generation treat the previous publication's hardening as
// content.
func TestAHarvestedReleaseCarriesTheStoresModesNotTheSeal(t *testing.T) {
	f := newFixture(t)
	f.sealDirs(t)

	rel, err := f.harvest(t)
	if err != nil {
		t.Fatalf("Harvest: %v", err)
	}
	info, err := os.Lstat(filepath.Join(rel.RootDir(), "WS", "Content", "Paks", "game.pak"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o644 {
		t.Errorf("a harvested file has mode %v, want the store's 0644 — the published tree's "+
			"0444 seal is publication's hardening, not the release's content", info.Mode().Perm())
	}
	di, err := os.Lstat(filepath.Join(rel.RootDir(), "WS", "Content", "Paks"))
	if err != nil {
		t.Fatal(err)
	}
	if di.Mode().Perm() != 0o755 {
		t.Errorf("a harvested directory has mode %v, want the store's 0755", di.Mode().Perm())
	}
}

// Per-instance state is never published, so it can never be harvested. Which
// means a harvested release does not carry it — and that is the absolute
// state rule holding rather than a gap: WS/Saved is never shared, never used
// as content input, never modified by a transaction.
func TestPerInstanceStateCannotEnterAHarvest(t *testing.T) {
	f := newFixture(t)

	rel, err := f.harvest(t)
	if err != nil {
		t.Fatalf("Harvest: %v", err)
	}
	for _, e := range rel.Manifest.Entries {
		if strings.HasPrefix(e.Path, "WS/Saved") {
			t.Errorf("per-instance state %q reached a harvested release", e.Path)
		}
		if e.Class == "state" {
			t.Errorf("%q was harvested into the excluded class %q", e.Path, e.Class)
		}
	}
}

// --- the quiesce refusal --------------------------------------------------

// "Refuse if any consumer is running" — the first line of the master plan's
// harvest procedure, and the one that decides whether the release is a state
// the tree was ever actually in.
func TestHarvestIsRefusedWhileAConsumerCanStillWrite(t *testing.T) {
	f := newFixture(t)
	f.guard.answers = []*consumer.Report{held()}

	_, err := f.harvest(t)
	if !IsRefusal(err) {
		t.Fatalf("want a refusal while a consumer holds the generation, got %v", err)
	}
	var q *QuiesceError
	if !errors.As(err, &q) {
		t.Fatalf("want a *QuiesceError, got %T", err)
	}
	if !strings.Contains(err.Error(), "soulmask-1") {
		t.Errorf("the refusal does not name what is holding: %v", err)
	}
	if !strings.Contains(err.Error(), "unexpose") {
		t.Errorf("the refusal does not name the remedy: %v", err)
	}
	// Nothing was attempted: no release, and no transaction left on disk.
	if ids, _ := f.st.List(); len(ids) != 0 {
		t.Errorf("a refused harvest produced releases %v", ids)
	}
	if left := f.txLeftBehind(t); len(left) != 0 {
		t.Errorf("a refused harvest left transactions %v behind", left)
	}
	// And it was asked BEFORE the work, not after it. A harvest that only
	// checked once the copy was done would refuse identically — having first
	// read a multi-gigabyte tree that somebody was writing to, to build a
	// transaction it was always going to throw away.
	if len(f.guard.copiedAtAsk) == 0 || f.guard.copiedAtAsk[0] {
		t.Errorf("the first question was asked after content had already been copied "+
			"(%v); the refusal has to cost nothing", f.guard.copiedAtAsk)
	}
}

// The second ask, and the only one that can see a container that started
// while harvest was reading. Without it the release is assembled from two
// different states of the tree — and it hashes and verifies perfectly,
// because a torn copy is internally consistent.
func TestHarvestIsRefusedWhenAConsumerAppearsDuringTheCopy(t *testing.T) {
	f := newFixture(t)
	f.guard.answers = []*consumer.Report{{}, held()}

	_, err := f.harvest(t)
	if !IsRefusal(err) {
		t.Fatalf("want a refusal when a consumer appears mid-copy, got %v", err)
	}
	if !strings.Contains(err.Error(), "during the copy") {
		t.Errorf("the refusal does not say when the holder appeared: %v", err)
	}
	if f.guard.asked != 2 {
		t.Errorf("the guard was asked %d time(s); the question has to be asked again after "+
			"the copy, or a consumer that started mid-harvest is invisible", f.guard.asked)
	}
	// The second ask is the one that has seen the copy — that is what makes it
	// able to say the tree did not move under it.
	if len(f.guard.copiedAtAsk) != 2 || !f.guard.copiedAtAsk[1] {
		t.Errorf("the second question was asked before the copy (%v), so it cannot have "+
			"noticed anything that happened during it", f.guard.copiedAtAsk)
	}
	if ids, _ := f.st.List(); len(ids) != 0 {
		t.Errorf("a refused harvest produced releases %v", ids)
	}
	if left := f.txLeftBehind(t); len(left) != 0 {
		t.Errorf("a refused harvest left transactions %v behind", left)
	}
}

// A degraded resolution does not refuse. The mount table is authoritative
// about the question harvest asks — a process that can write must have the
// mount — and Docker only adds names.
func TestAnUnnameableHolderStillRefusesAndADegradedCheckDoesNot(t *testing.T) {
	f := newFixture(t)
	f.guard.answers = []*consumer.Report{{Degraded: []string{"docker: connection refused"}}}

	if _, err := f.harvest(t); err != nil {
		t.Fatalf("a harvest with nothing holding was refused because Docker was "+
			"unreachable: %v", err)
	}
}

// --- the generation has to be published -----------------------------------

// The check that looks unnecessary. A teardown leaves the mount POINT behind
// as an ordinary empty directory, so without this harvest walks it, finds
// nothing, and promotes an empty release that verifies clean.
func TestHarvestIsRefusedWhenAClassIsNotMounted(t *testing.T) {
	f := newFixture(t)
	f.table = f.mountTable(t, "code") // pak was torn down

	_, err := f.harvest(t)
	if !IsRefusal(err) {
		t.Fatalf("want a refusal when a class is not mounted, got %v", err)
	}
	var np *NotPublishedError
	if !errors.As(err, &np) {
		t.Fatalf("want a *NotPublishedError, got %T: %v", err, err)
	}
	if np.Class != "pak" {
		t.Errorf("the refusal names class %q, want %q", np.Class, "pak")
	}
	if ids, _ := f.st.List(); len(ids) != 0 {
		t.Errorf("a refused harvest produced releases %v", ids)
	}
}

// And the same failure one step further along: mounted, but empty. Promoting
// that would replace a good release with nothing, and every check downstream
// would pass.
func TestHarvestRefusesAGenerationWhoseTreesAreEmpty(t *testing.T) {
	f := newFixture(t)
	for _, class := range []string{"code", "pak"} {
		if err := os.RemoveAll(f.cfg.OpClassRoot(opID, class)); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(f.cfg.OpClassRoot(opID, class), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	if _, err := f.harvest(t); err == nil {
		t.Fatal("harvesting an empty generation produced a release")
	} else if !strings.Contains(err.Error(), "empty") {
		t.Errorf("the error does not say the generation is empty: %v", err)
	}
	if ids, _ := f.st.List(); len(ids) != 0 {
		t.Errorf("an empty generation was promoted as %v", ids)
	}
}

// --- assembly refuses rather than choosing --------------------------------

// Oracle 23's second clause: an unclassified new path blocks promotion
// exactly as it does on the staged path, and with the same error type.
func TestAnUnclassifiedPathBlocksAHarvestExactlyAsItBlocksAStage(t *testing.T) {
	f := newFixture(t)
	f.write(t, "pak", "WS/notes.txt", "a path no rule matches")

	_, err := f.harvest(t)
	if !IsRefusal(err) {
		t.Fatalf("want a refusal for an unclassified path, got %v", err)
	}
	var unclassified *profile.UnclassifiedError
	if !errors.As(err, &unclassified) {
		t.Fatalf("want a *profile.UnclassifiedError — the same refusal the staged path "+
			"produces — got %T: %v", err, err)
	}
	if unclassified.Path != "WS/notes.txt" {
		t.Errorf("the refusal names %q, want %q", unclassified.Path, "WS/notes.txt")
	}
}

// A profile edited while a generation is live. Nothing about the tree changed
// and every path still classifies, so only the comparison against the class
// tree it came off can see it — and republishing afterwards would silently
// move the content to a different tmpfs and a different memory floor.
func TestHarvestRefusesWhenTheProfileMovedAPathToAnotherClass(t *testing.T) {
	f := newFixture(t)

	// WS/Binaries is reassigned from `code` to `pak` after publication.
	moved := testProfile(t)
	for i := range moved.Classes {
		switch moved.Classes[i].Name {
		case "code":
			moved.Classes[i].Paths = []string{"Engine"}
		case "pak":
			moved.Classes[i].Paths = []string{"WS/Content/Paks", "WS/Binaries"}
		}
	}
	if err := moved.Validate(); err != nil {
		t.Fatal(err)
	}

	_, err := f.harvester(t).Harvest(ctx(), f.rec, moved, Opts{ReleaseID: "rel-harvested"})
	if !IsRefusal(err) {
		t.Fatalf("want a refusal when the profile moved a path, got %v", err)
	}
	var mv *ClassMovedError
	if !errors.As(err, &mv) {
		t.Fatalf("want a *ClassMovedError, got %T: %v", err, err)
	}
	if mv.Published != "code" || mv.Now != "pak" {
		t.Errorf("the refusal says %q -> %q, want %q -> %q", mv.Published, mv.Now, "code", "pak")
	}
	if left := f.txLeftBehind(t); len(left) != 0 {
		t.Errorf("a refused harvest left transactions %v behind", left)
	}
}

// Two class trees claiming one path. Disjoint prefixes make this unreachable
// for classified content — it would trip the class check first — so what
// reaches here is a STRUCTURAL path that is a directory in one tree and
// something else in another. Picking a winner would make the release depend
// on the order the classes were walked in.
func TestHarvestRefusesAPathTwoClassTreesBothClaim(t *testing.T) {
	f := newFixture(t)
	// `WS/Content` is structure — the directory on the way to WS/Content/Paks
	// — and here the code tree holds a FILE at that path.
	f.write(t, "code", "WS/Content", "not a directory")

	_, err := f.harvest(t)
	if !IsRefusal(err) {
		t.Fatalf("want a refusal for a path two class trees claim, got %v", err)
	}
	var col *CollisionError
	if !errors.As(err, &col) {
		t.Fatalf("want a *CollisionError, got %T: %v", err, err)
	}
	if col.Path != "WS/Content" {
		t.Errorf("the refusal names %q, want %q", col.Path, "WS/Content")
	}
}

// A structural directory legitimately appears in every class tree that has to
// reach through it, so it must NOT be a collision — otherwise no profile with
// two classes under a shared ancestor could ever be harvested.
func TestAStructuralDirectoryInEveryTreeIsNotACollision(t *testing.T) {
	f := newFixture(t)

	rel, err := f.harvest(t)
	if err != nil {
		t.Fatalf("Harvest: %v", err)
	}
	// WS is on the way to both WS/Binaries (code) and WS/Content/Paks (pak).
	info, err := os.Lstat(filepath.Join(rel.RootDir(), "WS"))
	if err != nil || !info.IsDir() {
		t.Fatalf("the shared structural directory did not survive assembly: %v", err)
	}
}

// --- the profile has to be the one the generation was published under -----

func TestHarvestRefusesAProfileTheGenerationWasNotPublishedUnder(t *testing.T) {
	f := newFixture(t)
	other := testProfile(t)
	other.ID = "othergame"
	if err := other.Validate(); err != nil {
		t.Fatal(err)
	}

	_, err := f.harvester(t).Harvest(ctx(), f.rec, other, Opts{ReleaseID: "rel-harvested"})
	if err == nil {
		t.Fatal("a generation was harvested under a profile it was not published with")
	}
	if !strings.Contains(err.Error(), "othergame") {
		t.Errorf("the error does not name the mismatch: %v", err)
	}
}

// --- the journal ----------------------------------------------------------

// A refusal is journaled as a refusal, not as a fault: "srdm said no" and
// "srdm broke" call for different things from an operator.
func TestARefusedHarvestIsJournaledAsARefusal(t *testing.T) {
	f := newFixture(t)
	f.guard.answers = []*consumer.Report{held()}

	if _, err := f.harvest(t); err == nil {
		t.Fatal("want a refusal")
	}
	op, err := journal.LoadOperation(f.cfg.JournalDir(), "harvest-"+genID)
	if err != nil {
		t.Fatal(err)
	}
	var sawRefused bool
	for _, r := range op.Events {
		if r.Kind == KindHarvest && r.Phase == PhaseQuiesce &&
			r.Outcome == journal.OutcomeRefused {
			sawRefused = true
		}
		if r.Outcome == journal.OutcomeFailed {
			t.Errorf("a refusal was journaled as a fault: %+v", r)
		}
	}
	if !sawRefused {
		t.Error("the refusal is not in the journal")
	}
}

// The whole operation shares one id, so promotion's phases and harvest's own
// are one account rather than two unrelated ones.
func TestAHarvestJournalsUnderOneOperationID(t *testing.T) {
	f := newFixture(t)
	if _, err := f.harvest(t); err != nil {
		t.Fatal(err)
	}
	op, err := journal.LoadOperation(f.cfg.JournalDir(), "harvest-"+genID)
	if err != nil {
		t.Fatal(err)
	}
	kinds := map[string]bool{}
	for _, r := range op.Events {
		kinds[r.Kind] = true
	}
	for _, want := range []string{KindHarvest, store.KindPromote} {
		if !kinds[want] {
			t.Errorf("operation %q has no %q records; kinds present: %v",
				"harvest-"+genID, want, kinds)
		}
	}
}
