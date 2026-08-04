// Package config holds srdm's on-disk layout and daemon configuration.
//
// Naming (master plan decision 9): every identifier is the single token
// "srdm". systemd's "-" is the slice hierarchy separator, so a hyphenated
// root would nest under auto-created ancestors with MemoryMin=0 and every
// class floor beneath it would be arithmetically dead.
package config

import (
	"fmt"
	"io/fs"
	"path/filepath"
	"strings"
)

// Default roots. Both are deliberately single-token "srdm" paths.
const (
	DefaultStateDir = "/var/lib/srdm"
	DefaultRunDir   = "/run/srdm"
	DefaultSlice    = "srdm.slice"
)

// Ownership is the normalization applied to release content before a
// transaction may be promoted.
//
// The master plan fixes the store at srdm:srdm, 0755/0644 world-readable:
// managed content carries no secrets, and every consumer reads it.
type Ownership struct {
	UID      int
	GID      int
	DirMode  fs.FileMode
	FileMode fs.FileMode
}

// DefaultOwnership returns the mode policy with an unset uid/gid. Callers
// that leave UID/GID at -1 get mode normalization only; see store.Promote.
func DefaultOwnership() Ownership {
	return Ownership{UID: -1, GID: -1, DirMode: 0o755, FileMode: 0o644}
}

// Config is the resolved daemon configuration.
type Config struct {
	// StateDir is the persistent root: the release store and the journal.
	StateDir string
	// RunDir is the volatile root: operation tmpfs mounts and sockets.
	// P01 only creates it; publication topology arrives with P02.
	RunDir string
	// Owner is the ownership normalization applied at promotion.
	Owner Ownership
	// Slice is the admin-owned parent slice whose MemoryMin backs the class
	// floors. srdm verifies it rather than writing it (decision D-003).
	Slice string
	// Retention is how many releases per profile `gc` keeps beyond the ones
	// it may not remove at all. The master plan's default is "≥ 3 retained,
	// immutable, hash-verified" (D-002).
	//
	// It is a floor on what is KEPT rather than a cap on what exists: the
	// assigned release, the rollback target and anything published are never
	// collected, so a profile can legitimately hold more than this and never
	// fewer.
	Retention int
	// Wings describes the deployment srdm exposes content into. Only the
	// host-bind driver reads it; provider mode (v2) needs none of it.
	Wings Wings
}

// Wings is what srdm needs to know about the Pterodactyl node it shares a
// host with.
//
// All of it is deployment shape rather than srdm's own state, and srdm reads
// it rather than writing it — the same relationship it has with srdm.slice
// (D-003). Getting any of it wrong is an outage, not a warning, which is why
// the host-bind driver refuses rather than proceeds when it cannot confirm
// what it needs.
type Wings struct {
	// BindRoot is the host path the Wings container binds. Its propagation
	// is the whole precondition: under Docker's default rprivate every mount
	// srdm makes is invisible to Wings and every unmount leaves a ghost —
	// the 2026-07-31 outage.
	BindRoot string
	// VolumeRoot is where server volumes live beneath BindRoot. host-bind
	// mounts managed content at the declared class paths under
	// VolumeRoot/<uuid>/.
	VolumeRoot string
	// ConfigPath is Wings' own config.yml, read for exactly one key:
	// system.check_permissions_on_boot.
	ConfigPath string
	// ChownSkipPatch asserts that the RUNNING Wings build carries F1, the
	// patch that skips the pre-boot chown walk over read-only mounts.
	//
	// It is an assertion by whoever installed that build, not something srdm
	// detects: a patch is not visible in a binary srdm can inspect. It
	// defaults to false, so a node that has not said otherwise is treated as
	// unpatched and `access: ro` is refused unless the node config disables
	// the walk instead. Claiming it falsely does not corrupt anything — it
	// produces the EROFS start failure this exists to prevent, with srdm's
	// refusal removed from in front of it.
	ChownSkipPatch bool
	// WriteOwner is vestigial as of P10 (D-029), kept for the same reason
	// Record.DirtyCapable is: an older srdm may still carry it in a config
	// document, and nothing should choke on the field being set.
	//
	// It was who a class tree was handed to when `access: rw` unsealed it IN
	// PLACE (D-022) — the uid:gid Wings runs its server containers as, with
	// `access: rw` refused when it was undeclared. P10 replaced in-place
	// unsealing with a per-server overlay upper layer that srdm itself
	// creates and owns, so there is no tree to hand over and nothing reads
	// this field anymore. Left set, it is simply ignored.
	WriteOwner *WriteOwner
}

// WriteOwner is the identity that may write through an `access: rw` exposure.
type WriteOwner struct {
	UID int `json:"uid"`
	GID int `json:"gid"`
}

func (o WriteOwner) String() string { return fmt.Sprintf("%d:%d", o.UID, o.GID) }

// Wings defaults, as a stock Pterodactyl node lays itself out.
const (
	DefaultWingsBindRoot   = "/var/lib/pterodactyl"
	DefaultWingsVolumeRoot = "/var/lib/pterodactyl/volumes"
	DefaultWingsConfig     = "/etc/pterodactyl/config.yml"
)

// DefaultWings returns the stock node layout.
func DefaultWings() Wings {
	return Wings{
		BindRoot:   DefaultWingsBindRoot,
		VolumeRoot: DefaultWingsVolumeRoot,
		ConfigPath: DefaultWingsConfig,
	}
}

// ServerVolume is one server's volume root.
func (w Wings) ServerVolume(serverID string) string {
	return filepath.Join(w.VolumeRoot, serverID)
}

// Validate reports whether the Wings settings are usable.
func (w Wings) Validate() error {
	for _, f := range []struct{ name, val string }{
		{"wings.bind_root", w.BindRoot},
		{"wings.volume_root", w.VolumeRoot},
	} {
		if f.val == "" {
			return fmt.Errorf("config: %s is empty", f.name)
		}
		if !filepath.IsAbs(f.val) {
			return fmt.Errorf("config: %s must be an absolute path, got %q", f.name, f.val)
		}
	}
	// The volume root has to be inside the bind root, or the propagation
	// precondition is checked against a mount that does not carry srdm's
	// exposures — which would pass while every mount stayed invisible to
	// Wings.
	if !withinPath(w.VolumeRoot, w.BindRoot) {
		return fmt.Errorf("config: wings.volume_root %q is not beneath wings.bind_root %q, "+
			"so the propagation srdm checks is not the propagation its mounts travel over",
			w.VolumeRoot, w.BindRoot)
	}
	// A negative id is how "unset" is spelled everywhere else in this config
	// (config.Ownership), so accepting one here would mean two different
	// meanings for the same value in the same document.
	if w.WriteOwner != nil && (w.WriteOwner.UID < 0 || w.WriteOwner.GID < 0) {
		return fmt.Errorf("config: wings.write_owner is %s; leave it unset rather than "+
			"negative — unset refuses access: rw, which is the safe answer", w.WriteOwner)
	}
	return nil
}

// withinPath reports whether path is at or beneath root, matching whole
// components so "/var/lib/pterodactyl-old" is not beneath
// "/var/lib/pterodactyl".
func withinPath(path, root string) bool {
	path = strings.TrimSuffix(path, "/")
	root = strings.TrimSuffix(root, "/")
	return path == root || strings.HasPrefix(path, root+"/")
}

// DefaultRetention is the master plan's "≥ 3 retained" (D-002).
const DefaultRetention = 3

// Default returns the shipped defaults.
func Default() Config {
	return Config{
		StateDir:  DefaultStateDir,
		RunDir:    DefaultRunDir,
		Owner:     DefaultOwnership(),
		Slice:     DefaultSlice,
		Retention: DefaultRetention,
		Wings:     DefaultWings(),
	}
}

// Validate reports whether the configuration is usable.
func (c Config) Validate() error {
	for _, f := range []struct {
		name, val string
	}{
		{"state_dir", c.StateDir},
		{"run_dir", c.RunDir},
	} {
		if f.val == "" {
			return fmt.Errorf("config: %s is empty", f.name)
		}
		if !filepath.IsAbs(f.val) {
			return fmt.Errorf("config: %s must be an absolute path, got %q", f.name, f.val)
		}
	}
	if c.Slice == "" {
		return fmt.Errorf("config: slice is empty")
	}
	if !strings.HasSuffix(c.Slice, ".slice") {
		return fmt.Errorf("config: slice %q must name a systemd slice unit", c.Slice)
	}
	// A hyphen before ".slice" makes systemd nest the unit under auto-created
	// ancestors that carry MemoryMin=0, which silently kills every class floor
	// below it. Refuse rather than ship a dead protection budget.
	if strings.Contains(strings.TrimSuffix(c.Slice, ".slice"), "-") {
		return fmt.Errorf("config: slice %q contains %q, which systemd reads as a hierarchy "+
			"separator; the auto-created ancestors carry MemoryMin=0 and would make every "+
			"class floor beneath it arithmetically dead", c.Slice, "-")
	}
	if c.Owner.DirMode&fs.ModeType != 0 || c.Owner.FileMode&fs.ModeType != 0 {
		return fmt.Errorf("config: ownership modes must be permission bits only")
	}
	// Zero would mean "keep nothing beyond what is pinned", which is a
	// coherent policy nobody wants by accident, and negative is meaningless.
	// Refusing both keeps `gc` from ever being destructive because a field
	// was left unset.
	if c.Retention < 1 {
		return fmt.Errorf("config: retention is %d; it must keep at least one release "+
			"per profile, and the shipped default is %d", c.Retention, DefaultRetention)
	}
	// Empty Wings settings are valid: everything upstream of the exposure
	// fork works without a Pterodactyl node at all, and provider mode (v2)
	// never needs them. The host-bind driver refuses on its own when they
	// are missing, where the error can say what the operator was trying to
	// do.
	if c.Wings != (Wings{}) {
		if err := c.Wings.Validate(); err != nil {
			return err
		}
	}
	return nil
}

// StoreDir is the release store root.
func (c Config) StoreDir() string { return filepath.Join(c.StateDir, "store") }

// ReleasesDir holds one directory per promoted, verified release.
func (c Config) ReleasesDir() string { return filepath.Join(c.StoreDir(), "releases") }

// ChannelsDir holds <profile>/<channel> symlinks into ReleasesDir.
func (c Config) ChannelsDir() string { return filepath.Join(c.StoreDir(), "channels") }

// TxDir holds in-flight transaction directories, one per operation.
func (c Config) TxDir() string { return filepath.Join(c.StoreDir(), "tx") }

// QuarantineDir holds transactions recovery could not adopt.
func (c Config) QuarantineDir() string { return filepath.Join(c.StoreDir(), "quarantine") }

// JournalDir holds durable per-operation records and the events stream.
func (c Config) JournalDir() string { return filepath.Join(c.StateDir, "journal") }

// ReleaseDir is the path of a promoted release.
func (c Config) ReleaseDir(releaseID string) string {
	return filepath.Join(c.ReleasesDir(), releaseID)
}

// ChannelLink is the path of a channel symlink.
func (c Config) ChannelLink(profileID, channel string) string {
	return filepath.Join(c.ChannelsDir(), profileID, channel)
}

// --- publication layout ----------------------------------------------------
//
// Two roots under RunDir, and the split is deliberate. Operation-private
// work happens under OpRoot, where a half-populated tree is nobody's
// business; a generation becomes visible only as a read-only bind under
// ExposeRoot, of a tree that has already been verified. Nothing is ever
// renamed into visibility, and nothing visible was ever writable.

// OpRootName is the dot-prefixed operation-private subtree of RunDir.
const OpRootName = ".op"

// OpRoot holds every in-flight publication's private mounts.
func (c Config) OpRoot() string { return filepath.Join(c.RunDir, OpRootName) }

// OpDir is one operation's private directory.
func (c Config) OpDir(opID string) string { return filepath.Join(c.OpRoot(), opID) }

// OpClassDir is where a class's op tmpfs is mounted.
func (c Config) OpClassDir(opID, class string) string {
	return filepath.Join(c.OpDir(opID), class)
}

// OpClassRoot is the content root inside a class's op tmpfs. Content lives
// one level down so the tmpfs mount point itself is never the thing bound,
// which keeps the bind source a plain directory.
func (c Config) OpClassRoot(opID, class string) string {
	return filepath.Join(c.OpClassDir(opID, class), "root")
}

// OpPlanFile is the durable statement of what an operation intends to
// publish, written before any mount or hold unit exists and removed once
// the published record makes it official.
//
// Under RunDir beside the mounts and units it describes, deliberately: a
// reboot wipes both together, so the plan can never outlive the state it
// would otherwise misdescribe. It exists for the process-dies-without-reboot
// case — the one D-013's "worker outlives the process that started it"
// leaves unaddressed for the DAEMON's own process, as opposed to the
// worker's (P08b, adoption and quarantine).
func (c Config) OpPlanFile(opID string) string {
	return filepath.Join(c.OpDir(opID), "plan.json")
}

// GenerationDir is a published generation's visible root.
func (c Config) GenerationDir(profileID, generation string) string {
	return filepath.Join(c.RunDir, profileID, generation)
}

// ExposeClassDir is the read-only bind a consumer eventually sees.
func (c Config) ExposeClassDir(profileID, generation, class string) string {
	return filepath.Join(c.GenerationDir(profileID, generation), class)
}

// PublishedDir holds the durable published-state records.
//
// Under StateDir, not RunDir: a record has to survive the reboot whose
// republish it drives.
func (c Config) PublishedDir() string { return filepath.Join(c.StateDir, "published") }

// PublishedRecord is one generation's durable published-state record.
func (c Config) PublishedRecord(profileID, generation string) string {
	return filepath.Join(c.PublishedDir(), profileID, generation+".json")
}

// AssignmentsDir holds one declared-intent document per profile.
//
// Under StateDir beside PublishedDir, and the pair is the point: a published
// record is what srdm DID, an assignment is what it was ASKED to do. Both
// have to survive the reboot at which the difference between them becomes
// the boot path's whole job.
func (c Config) AssignmentsDir() string { return filepath.Join(c.StateDir, "assignments") }

// ProfilesDir holds a durable copy of every profile document srdm has been
// given, keyed by id.
//
// D-026: a profile document is otherwise only ever a path an operator passes
// on the command line, loaded fresh and never kept. Boot restore has no
// operator to ask — it has an assignment naming a profile ID, and nothing
// else — so the last profile document that successfully drove a
// state-changing operation is kept here, written at the same moment and by
// the same rule as everything else durable: as a side effect of the
// operation that made it true, not as a separate administrative step an
// operator can forget.
func (c Config) ProfilesDir() string { return filepath.Join(c.StateDir, "profiles") }

// ProfileDocument is one profile's durable copy.
func (c Config) ProfileDocument(profileID string) string {
	return filepath.Join(c.ProfilesDir(), profileID+".json")
}

// LockPath is the mutual exclusion between operations that change state.
//
// Under RunDir, not StateDir: a lock that survived a reboot would be a lock
// held by a process that no longer exists, and the first boot after a crash
// is exactly when srdm most needs to be able to act.
func (c Config) LockPath() string { return filepath.Join(c.RunDir, "srdm.lock") }

// --- access: rw overlay layers ----------------------------------------------
//
// D-027 adopted an overlay for `access: rw`: the sealed generation as
// `lowerdir`, a per-server `upperdir`/`workdir` above it. Under StateDir, not
// RunDir — deliberately (P10's "what to build" §1): an in-place update that
// survived a process crash but not a host reboot would be worse than one that
// never started, so the writable layer has to live on the filesystem that
// survives a reboot, keyed so re-exposing the SAME generation to the SAME
// server finds its own writes still there.

// OverlayDir is the root of every server's writable overlay layers.
func (c Config) OverlayDir() string { return filepath.Join(c.StateDir, "overlay") }

// OverlayServerDir is one server's overlay state for one declared class path
// of one generation — the unit two simultaneously-mounted overlays can never
// share, because overlayfs owns `upperdir`/`workdir` exclusively for as long
// as a mount using them is live.
func (c Config) OverlayServerDir(profileID, generation, class, path, serverID string) string {
	return filepath.Join(c.OverlayDir(), profileID, generation, class,
		filepath.FromSlash(path), serverID)
}

// OverlayUpperDir is the writable layer an rw exposure's writes actually
// land in.
func (c Config) OverlayUpperDir(profileID, generation, class, path, serverID string) string {
	return filepath.Join(c.OverlayServerDir(profileID, generation, class, path, serverID), "upper")
}

// OverlayWorkDir is overlayfs's own scratch directory, required to be on the
// same filesystem as OverlayUpperDir and touched by nothing but the kernel.
func (c Config) OverlayWorkDir(profileID, generation, class, path, serverID string) string {
	return filepath.Join(c.OverlayServerDir(profileID, generation, class, path, serverID), "work")
}
