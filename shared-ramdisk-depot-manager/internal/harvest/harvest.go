// Package harvest adopts an in-place-updated generation as a release.
//
// It is today's manual procedure — ROLE=main updates on disk, then repopulate
// — automated, verified and rollback-able, and it is what makes the game's
// own updater a legitimate acquisition source (master-plan decision 12). The
// containerized SteamCMD driver leaves the MVP critical path because of it.
//
// The order is the master plan's:
//
//	refuse while anything can still write -> re-walk and re-hash the tmpfs ->
//	classify (an unclassified new path blocks promotion) -> probes ->
//	transaction into the store, COMPLETE fsync'd last -> journal with
//	harvested-from provenance
//
// Only the first two steps are this package's own. Everything from
// classification down is store.Promote, unchanged and shared with the staged
// path — which is the point: a harvested release has to be indistinguishable
// from a staged one, and the cheapest way to guarantee that is for it to be
// made by the same code.
//
// What this package adds is the two things staging never has to think about.
// The content is on a tmpfs somebody may still be writing to, so harvest has
// to establish that nobody is. And it is on N tmpfs mounts rather than one
// tree, because publication gives every class its own — so the release tree
// is ASSEMBLED, and assembly is where a path can arrive from the wrong class.
package harvest

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strings"

	"srdm/internal/config"
	"srdm/internal/consumer"
	"srdm/internal/fsx"
	"srdm/internal/journal"
	"srdm/internal/mountinfo"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

// KindHarvest names the operation in the journal.
const KindHarvest = "harvest"

// Phases, journaled before they execute and after they settle.
//
// Promotion's own phases follow under the same operation id, emitted by
// store.Promote, so one `srdm journal show --op <id>` is the whole account.
const (
	// PhaseQuiesce is the refusal: the generation must still be published,
	// and nothing may be able to write to it.
	PhaseQuiesce = "quiesce"
	// PhaseAssemble copies the class trees into one transaction tree.
	PhaseAssemble = "assemble"
	// PhaseSettle asks the quiesce question a second time, after the copy.
	PhaseSettle = "settle"
)

// Guard resolves who is holding a generation. *consumer.Resolver satisfies
// it; it is the same surface teardown and exposure use.
type Guard interface {
	Resolve(ctx context.Context, paths []string) (*consumer.Report, error)
}

// Harvester turns published generations back into releases.
type Harvester struct {
	cfg           config.Config
	jnl           *journal.Journal
	st            *store.Store
	guard         Guard
	mountInfoPath string
}

// Option configures a Harvester.
type Option func(*Harvester)

// WithGuard injects consumer resolution.
func WithGuard(g Guard) Option { return func(h *Harvester) { h.guard = g } }

// WithMountInfoPath points the published-state check at a specific mount
// table.
func WithMountInfoPath(p string) Option { return func(h *Harvester) { h.mountInfoPath = p } }

// New returns a Harvester writing into st.
func New(cfg config.Config, jnl *journal.Journal, st *store.Store, opts ...Option) (*Harvester, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if jnl == nil {
		return nil, errors.New("harvest: a journal is required")
	}
	if st == nil {
		return nil, errors.New("harvest: a store is required")
	}
	h := &Harvester{cfg: cfg, jnl: jnl, st: st}
	for _, o := range opts {
		o(h)
	}
	if h.guard == nil {
		h.guard = consumer.New()
	}
	return h, nil
}

// Opts parameterizes a harvest.
type Opts struct {
	// OperationID names the operation in the journal and the transaction
	// directory. Defaults to "harvest-<generation>".
	OperationID string
	// ReleaseID names the release the harvest produces. Required, and
	// supplied rather than derived: a harvested release is a first-class
	// release and its identity is the operator's to choose, exactly as on the
	// staged path.
	ReleaseID string
	// Channel, when set, is flipped to the new release.
	Channel string
}

// QuiesceError is harvest declining because something can still write to the
// generation it was asked to read.
//
// A refusal rather than a fault: nothing is broken, and what has to happen
// next is something srdm cannot do for the operator. The remedy is in the
// message, because a refusal with no remedy is an outage with extra steps.
type QuiesceError struct {
	Generation string
	// When is "before the copy" or "during the copy". The second is the
	// interesting one — it means something started while harvest was reading,
	// so the tree it copied may be half of one state and half of another.
	When     string
	Holders  []consumer.Holder
	Degraded []string
}

func (e *QuiesceError) Error() string {
	var who []string
	for _, h := range e.Holders {
		who = append(who, h.String())
	}
	return fmt.Sprintf("harvest: generation %s is still held %s by %d consumer(s): %s; "+
		"a harvest of a tree somebody is writing to would promote a state that never "+
		"existed — half the old content and half the new — and it would hash and verify "+
		"perfectly. Stop the consumers and unexpose the generation; harvest reads the "+
		"generation's own tmpfs and needs no exposure to do it",
		e.Generation, e.When, len(e.Holders), strings.Join(who, "; "))
}

// NotPublishedError is harvest declining because a class's tmpfs is not
// mounted.
//
// This is the check that matters most and looks least necessary. A torn-down
// generation leaves its mount POINT behind as an ordinary empty directory, so
// without it harvest would walk that directory, find nothing, and promote an
// empty release over the top of a perfectly good one — passing every hash
// check it has, because an empty tree hashes consistently.
type NotPublishedError struct {
	Generation string
	Class      string
	Mount      string
}

func (e *NotPublishedError) Error() string {
	return fmt.Sprintf("harvest: generation %s has no tmpfs mounted at %s for class %q, "+
		"so there is nothing published to harvest; harvesting the empty mount point that "+
		"a teardown leaves behind would promote an empty release that verifies clean",
		e.Generation, e.Mount, e.Class)
}

// CollisionError is two class trees claiming the same path.
//
// Disjoint class prefixes make this impossible through an exposure, so it
// means one of the tmpfs mounts holds content that did not come from its own
// class. Letting the second copy win would silently move content between
// memory classes; letting the first win would silently drop a file.
type CollisionError struct {
	Path          string
	First, Second string
}

func (e *CollisionError) Error() string {
	return fmt.Sprintf("harvest: %q is present in both the %q and %q class trees, so one of "+
		"them holds content that is not its own; harvesting it would pick a winner and "+
		"lose the other silently", e.Path, e.First, e.Second)
}

// ClassMovedError is a path whose class in the profile is no longer the class
// whose tmpfs it was found on.
//
// Under host-bind this cannot be caused by a write: an exposure binds only
// the declared class paths, so every write that reaches a class tmpfs lands
// inside that class. What causes it is the PROFILE changing under a live
// generation — a class path narrowed, a new excluded rule added. Promoting
// anyway would republish the content into a different class with a different
// memory floor and a different tmpfs, which is a reclassification nobody
// asked for and nothing would report.
type ClassMovedError struct {
	Path      string
	Published string
	Now       string
	Profile   string
}

func (e *ClassMovedError) Error() string {
	return fmt.Sprintf("harvest: %q was published as class %q but profile %q now classifies "+
		"it as %q; the profile changed under a live generation, and harvesting would move "+
		"the content to a different class, tmpfs and memory floor without saying so",
		e.Path, e.Published, e.Profile, e.Now)
}

// IsRefusal reports whether err is srdm declining by contract rather than
// failing.
//
// It includes store.IsRefusal because promotion's own refusals — an
// unclassified path, a failed probe — reach a harvest caller through here and
// are refusals for exactly the same reasons they are on the staged path.
func IsRefusal(err error) bool {
	var (
		quiesce   *QuiesceError
		published *NotPublishedError
		collision *CollisionError
		moved     *ClassMovedError
	)
	return errors.As(err, &quiesce) || errors.As(err, &published) ||
		errors.As(err, &collision) || errors.As(err, &moved) ||
		store.IsRefusal(err)
}

// Harvest promotes a published generation's live content into a new release.
//
// The generation itself is untouched and stays dirty-capable if it was: what
// comes back is a release, and the way a written-through generation is
// repaired is still to republish it from the store — now from the release
// this produced. Nothing is re-sealed and nothing is unmounted.
func (h *Harvester) Harvest(ctx context.Context, rec *publish.Record, prof *profile.Profile,
	o Opts) (*store.Release, error) {

	if rec == nil {
		return nil, errors.New("harvest: a published record is required")
	}
	if prof == nil {
		return nil, errors.New("harvest: a profile is required")
	}
	if o.OperationID == "" {
		o.OperationID = "harvest-" + rec.Generation
	}
	for kind, val := range map[string]string{
		"operation id": o.OperationID, "release id": o.ReleaseID,
	} {
		if err := config.ValidName(kind, val); err != nil {
			return nil, fmt.Errorf("harvest: %w", err)
		}
	}
	// The profile decides what every path in the tree IS. Harvesting under a
	// different one would classify the generation's content against rules it
	// was never published by, and the first thing to go wrong would be a
	// class name that happens to match.
	if rec.Profile != prof.ID {
		return nil, fmt.Errorf("harvest: generation %s was published under profile %q but "+
			"was handed profile %q", rec.Generation, rec.Profile, prof.ID)
	}
	if len(rec.Classes) == 0 {
		return nil, fmt.Errorf("harvest: generation %s records no classes", rec.Generation)
	}

	fields := map[string]string{
		"generation":     rec.Generation,
		"profile":        rec.Profile,
		"source_release": rec.ReleaseID,
		"release_id":     o.ReleaseID,
		"dirty_capable":  fmt.Sprintf("%t", rec.DirtyCapable),
	}

	if err := h.phase(o.OperationID, PhaseQuiesce, fields, func() error {
		if err := h.requirePublished(rec); err != nil {
			return err
		}
		return h.refuseIfHeld(ctx, rec, "before the copy", fields)
	}); err != nil {
		return nil, err
	}

	tx, err := h.st.Begin(o.OperationID)
	if err != nil {
		return nil, err
	}

	if err := h.phase(o.OperationID, PhaseAssemble, fields, func() error {
		return h.assemble(rec, prof, tx)
	}); err != nil {
		// Discarded rather than left for `store recover`, and this is the one
		// place harvest departs from the staged path deliberately. A failed
		// stage leaves the only copy of freshly acquired content, so it is
		// evidence. A failed harvest leaves a partial copy of a tmpfs that is
		// still mounted and still readable — no evidence at all, and possibly
		// gigabytes of it.
		_ = h.st.Discard(tx)
		return nil, err
	}

	// Asked a second time, after the copy. The first check says nothing could
	// write when harvest started; only this one says nothing did while it
	// read. Without it a container starting mid-harvest produces a release
	// assembled from two different states of the tree — which hashes cleanly,
	// verifies cleanly, and is a build that never existed.
	if err := h.phase(o.OperationID, PhaseSettle, fields, func() error {
		return h.refuseIfHeld(ctx, rec, "during the copy", fields)
	}); err != nil {
		_ = h.st.Discard(tx)
		return nil, err
	}

	// Everything from here is the staged path, unchanged: classify, manifest,
	// probes, ownership, COMPLETE fsync'd last. Only the provenance differs.
	return h.st.Promote(tx, prof, store.PromoteOpts{
		ReleaseID: o.ReleaseID,
		Channel:   o.Channel,
		Provenance: store.Provenance{
			Kind: store.ProvenanceHarvested,
			// The source RELEASE, not the generation. A generation id is
			// eight hex derived from a release id, so recording the release
			// loses nothing and is the only one of the two that identifies
			// what was updated in place without a lookup.
			Source: rec.ReleaseID,
		},
	})
}

// requirePublished checks every class's tmpfs is actually mounted.
func (h *Harvester) requirePublished(rec *publish.Record) error {
	entries, err := mountinfo.Read(h.mountInfoPath)
	if err != nil {
		return fmt.Errorf("harvest: reading the mount table: %w", err)
	}
	for _, c := range rec.Classes {
		if _, ok := mountinfo.At(entries, c.OpMount); !ok {
			return &NotPublishedError{Generation: rec.Generation, Class: c.Name, Mount: c.OpMount}
		}
	}
	return nil
}

// refuseIfHeld refuses while anything holds the generation's superblock.
//
// A degraded report does NOT refuse, and the asymmetry is deliberate. The
// mount table is authoritative about the question harvest is actually asking:
// a process that can write to this content must have the mount, so a hold
// srdm can see is a writer it must wait for. Docker adds names, and sees a
// container configured but not running — which holds no mount and therefore
// cannot write. So a Docker that cannot be reached costs harvest a name in
// the refusal, not the refusal itself; it is recorded either way, because
// "nobody is holding this" and "I could not ask" are different answers.
func (h *Harvester) refuseIfHeld(ctx context.Context, rec *publish.Record, when string,
	fields map[string]string) error {

	rep, err := h.guard.Resolve(ctx, recordPaths(rec))
	if err != nil {
		return fmt.Errorf("harvest: resolving consumers of %s/%s: %w",
			rec.Profile, rec.Generation, err)
	}
	fields["holders"] = fmt.Sprintf("%d", len(rep.Holders))
	if len(rep.Degraded) > 0 {
		fields["degraded"] = strings.Join(rep.Degraded, "; ")
	}
	if rep.Held() {
		return &QuiesceError{
			Generation: rec.Profile + "/" + rec.Generation,
			When:       when,
			Holders:    rep.Holders,
			Degraded:   rep.Degraded,
		}
	}
	return nil
}

// assemble copies every class tree into one transaction tree.
//
// Publication gives each class its own tmpfs, so what a release is made of is
// spread across N mounts that overlap only in the structural directories on
// the way to their declared paths. Assembly is the inverse, and it is the one
// step of a harvest that has decisions in it: every path is re-classified
// against the profile before it is copied, and a path that two class trees
// both claim stops the harvest rather than picking a winner.
//
// Classification here is NOT a duplicate of promotion's. Promotion asks "does
// any rule match this path"; assembly asks "does the rule that matches it
// still name the tmpfs it came off". The first catches new content, the
// second catches a profile that moved.
func (h *Harvester) assemble(rec *publish.Record, prof *profile.Profile, tx *store.Tx) error {
	classes := append([]publish.ClassRecord(nil), rec.Classes...)
	sort.Slice(classes, func(i, j int) bool { return classes[i].Name < classes[j].Name })

	// What supplied each path, so a second class claiming it is caught rather
	// than silently overwriting or being overwritten.
	from := make(map[string]string)
	isDir := make(map[string]bool)

	for _, c := range classes {
		root := c.ContentRoot()
		err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			rel, err := filepath.Rel(root, p)
			if err != nil {
				return err
			}
			if rel == "." {
				return nil
			}
			rel = filepath.ToSlash(rel)

			class, err := prof.Classify(rel)
			if err != nil {
				// *profile.UnclassifiedError, and store.IsRefusal already
				// knows it: an unclassified new path blocks a harvest exactly
				// as it blocks a stage.
				return err
			}
			// A structural directory belongs to no class and appears in every
			// tree that has to reach through it, which is why it is exempt
			// rather than a collision.
			if class.Kind != profile.KindStructure && class.Name != c.Name {
				return &ClassMovedError{
					Path: rel, Published: c.Name, Now: class.Name, Profile: prof.ID,
				}
			}

			if prev, dup := from[rel]; dup {
				if isDir[rel] && d.IsDir() {
					return nil
				}
				return &CollisionError{Path: rel, First: prev, Second: c.Name}
			}
			from[rel] = c.Name
			isDir[rel] = d.IsDir()

			info, err := d.Info()
			if err != nil {
				return err
			}
			return fsx.CopyEntry(p, filepath.Join(tx.Root, filepath.FromSlash(rel)), info)
		})
		if err != nil {
			return err
		}
	}

	if len(from) == 0 {
		return fmt.Errorf("harvest: generation %s is mounted but every class tree is empty; "+
			"promoting that would replace a good release with nothing", rec.Generation)
	}
	return nil
}

// recordPaths is every mount point a published record names.
func recordPaths(rec *publish.Record) []string {
	var paths []string
	for _, c := range rec.Classes {
		paths = append(paths, c.OpMount, c.ExposePath)
	}
	return paths
}

// phase journals a step before and after it runs.
func (h *Harvester) phase(opID, ph string, fields map[string]string, fn func() error) error {
	if err := h.jnl.Emit(journal.Record{
		OperationID: opID, Kind: KindHarvest, Phase: ph,
		Outcome: journal.OutcomeStarted, Fields: fields,
	}); err != nil {
		return err
	}
	if err := fn(); err != nil {
		outcome := journal.OutcomeFailed
		if IsRefusal(err) {
			outcome = journal.OutcomeRefused
		}
		_ = h.jnl.Emit(journal.Record{
			OperationID: opID, Kind: KindHarvest, Phase: ph,
			Outcome: outcome, Fields: fields, Error: err.Error(),
		})
		return err
	}
	return h.jnl.Emit(journal.Record{
		OperationID: opID, Kind: KindHarvest, Phase: ph,
		Outcome: journal.OutcomeOK, Fields: fields,
	})
}
