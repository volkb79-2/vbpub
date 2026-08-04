package main

// The operator surface. Everything below this file's verbs already existed as
// a library; what P08 adds is the ability to reach any of it without writing
// Go.
//
// Every verb takes `--profile <file>` because the profile document decides
// what a path IS — its class, its memory policy, whether it is shared at all
// — and nothing srdm stores can substitute for it. A verb that guessed the
// profile would be guessing the classification of content it is about to
// mount into a running server.

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"

	"srdm/internal/assign"
	"srdm/internal/config"
	"srdm/internal/expose"
	"srdm/internal/fsx"
	"srdm/internal/harvest"
	"srdm/internal/journal"
	"srdm/internal/opctl"
	"srdm/internal/power"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

// opEnv is everything a verb needs, assembled once.
type opEnv struct {
	cfg  config.Config
	prof *profile.Profile
	jnl  *journal.Journal
	ctl  *opctl.Controller
	ctx  context.Context
}

// opFlags registers the flags every operations verb shares.
func opFlags(fs *flag.FlagSet) (*config.Config, *string, *string) {
	cfg := commonFlags(fs)
	profilePath := fs.String("profile", "", "profile document (required)")
	opID := fs.String("op", "", "operation id (defaults to one derived from the verb)")
	fs.StringVar(&cfg.Wings.BindRoot, "wings-bind-root", cfg.Wings.BindRoot,
		"host path the Wings container binds")
	fs.StringVar(&cfg.Wings.VolumeRoot, "wings-volume-root", cfg.Wings.VolumeRoot,
		"where server volumes live beneath the bind root")
	fs.StringVar(&cfg.Wings.ConfigPath, "wings-config", cfg.Wings.ConfigPath,
		"Wings' own config.yml")
	fs.BoolVar(&cfg.Wings.ChownSkipPatch, "wings-chown-skip-patch", false,
		"assert the running Wings build carries F1 (the pre-boot chown-walk skip)")
	fs.IntVar(&cfg.Retention, "retention", cfg.Retention,
		"releases per profile gc keeps beyond the ones it may never remove")
	fs.StringVar(&cfg.Wings.APIURL, "wings-api-url", cfg.Wings.APIURL,
		"Wings' own node API base URL (not the Panel's) — only `update` needs it, "+
			"e.g. https://127.0.0.1:8080")
	fs.BoolVar(&cfg.Wings.APIInsecure, "wings-api-insecure", false,
		"skip TLS verification against --wings-api-url (only for a self-signed cert)")
	fs.DurationVar(&cfg.Wings.APIStopTimeout, "wings-api-stop-timeout", 0,
		"how long update waits for a server to settle offline (default 2m)")
	fs.DurationVar(&cfg.Wings.APIStartTimeout, "wings-api-start-timeout", 0,
		"how long update waits for a server to reach running (default 5m)")
	fs.DurationVar(&cfg.Wings.APIPollInterval, "wings-api-poll-interval", 0,
		"how often update polls Wings while waiting (default 2s)")
	return cfg, profilePath, opID
}

// writeOwnerFlag registers --write-owner, which access: rw requires.
func writeOwnerFlag(fs *flag.FlagSet) *string {
	return fs.String("write-owner", "",
		"uid:gid to hand an unsealed tree to for access: rw "+
			"(Wings' system.user.uid / system.user.gid)")
}

func applyWriteOwner(cfg *config.Config, spec string) error {
	if spec == "" {
		return nil
	}
	var uid, gid int
	if _, err := fmt.Sscanf(spec, "%d:%d", &uid, &gid); err != nil {
		return fmt.Errorf("--write-owner %q is not uid:gid", spec)
	}
	cfg.Wings.WriteOwner = &config.WriteOwner{UID: uid, GID: gid}
	return nil
}

// newOpEnv builds the controller a verb drives.
//
// needProfile is false only for `status`, and the exception is deliberate:
// the first command an operator runs on a node they are trying to understand
// should not require them to already know where the profile document is. Every
// verb that CHANGES anything needs it, because the profile decides what a
// path is.
func newOpEnv(cfg config.Config, profilePath string, needProfile bool) (*opEnv, error) {
	if profilePath == "" && needProfile {
		return nil, errors.New("--profile is required")
	}
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	var prof *profile.Profile
	var err error
	if profilePath != "" {
		if prof, err = profile.Load(profilePath); err != nil {
			return nil, err
		}
		if err := persistProfile(cfg, profilePath, prof); err != nil {
			return nil, err
		}
	}
	jnl, err := openJournal(cfg, prof)
	if err != nil {
		return nil, err
	}
	st, err := store.Open(cfg, jnl)
	if err != nil {
		jnl.Close()
		return nil, err
	}
	pub, err := publish.New(cfg, jnl)
	if err != nil {
		jnl.Close()
		return nil, err
	}
	drv, err := expose.NewHostBind(cfg.Wings, jnl, expose.WithMarker(pub))
	if err != nil {
		jnl.Close()
		return nil, err
	}
	hrv, err := harvest.New(cfg, jnl, st)
	if err != nil {
		jnl.Close()
		return nil, err
	}
	opts := []opctl.Option{opctl.WithHarvester(hrv)}
	// A power driver is wired in whenever Wings' node API is configured,
	// whatever verb is running — harmless, since only Update ever calls it.
	// Only `update` fails when it is not: every other verb has no use for
	// it, so an operator who has not set --wings-api-url yet is not blocked
	// from running them.
	if pwr, perr := power.NewWingsDriver(power.WingsConfig{
		APIURL: cfg.Wings.APIURL, APIInsecure: cfg.Wings.APIInsecure, ConfigPath: cfg.Wings.ConfigPath,
		StopTimeout:  cfg.Wings.EffectiveAPIStopTimeout(),
		StartTimeout: cfg.Wings.EffectiveAPIStartTimeout(),
		PollInterval: cfg.Wings.EffectiveAPIPollInterval(),
	}); perr == nil {
		// THE TOKEN IS A SECRET. Registered the moment it exists and before
		// this journal writes anything, exactly as a profile's own
		// Credentials are (D-032) — never logged, never put in an error,
		// never a journal field.
		jnl.AddSecrets(pwr.Bearers()...)
		opts = append(opts, opctl.WithPower(pwr))
		if prof != nil && prof.Readiness.Configured() {
			opts = append(opts, opctl.WithReadiness(power.NewWingsLogReadiness(pwr), power.Readiness{
				Kind: prof.Readiness.Kind, Pattern: prof.Readiness.Pattern,
				Timeout: prof.Readiness.EffectiveTimeout(),
			}))
		}
	} else if !power.IsRefusal(perr) {
		jnl.Close()
		return nil, perr
	}
	ctl, err := opctl.New(cfg, jnl, st, pub, drv, opts...)
	if err != nil {
		jnl.Close()
		return nil, err
	}
	return &opEnv{cfg: cfg, prof: prof, jnl: jnl, ctl: ctl, ctx: context.Background()}, nil
}

func (e *opEnv) close() {
	if e != nil && e.jnl != nil {
		_ = e.jnl.Close()
	}
}

// refused wraps an error so an operator can tell "srdm said no" from "srdm
// broke" without reading the journal. The distinction is the whole reason
// refusals are a type rather than a message.
func refused(err error) error {
	if err == nil {
		return nil
	}
	if opctl.IsRefusal(err) {
		return fmt.Errorf("refused: %w", err)
	}
	return err
}

// ---------------------------------------------------------------- activate --

func cmdActivate(args []string) error {
	fs := flag.NewFlagSet("activate", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	releaseID := fs.String("release", "", "release to activate (required)")
	owner := writeOwnerFlag(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *releaseID == "" {
		return errors.New("activate: --release is required")
	}
	if err := applyWriteOwner(cfg, *owner); err != nil {
		return err
	}
	if *opID == "" {
		*opID = "activate-" + *releaseID
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	rec, err := env.ctl.Activate(env.ctx, *opID, *releaseID, env.prof)
	if err != nil {
		return refused(err)
	}
	fmt.Printf("%s: release %s is live as generation %s\n", env.prof.ID, rec.ReleaseID, rec.Generation)
	return nil
}

// ------------------------------------------------------------------ update --

func cmdUpdate(args []string) error {
	fs := flag.NewFlagSet("update", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	releaseID := fs.String("release", "", "release to update to (required)")
	from := fs.String("from", "", "stage this directory into --release first, then update to it")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *releaseID == "" {
		return errors.New("update: --release is required")
	}
	if *opID == "" {
		*opID = "update-" + *releaseID
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	if *from != "" {
		if err := stageRelease(env, *from, *releaseID); err != nil {
			return err
		}
	}

	rec, err := env.ctl.Update(env.ctx, *opID, *releaseID, env.prof)
	if err != nil {
		return refused(err)
	}
	fmt.Printf("%s: release %s is live as generation %s (ordered cohort update)\n",
		env.prof.ID, rec.ReleaseID, rec.Generation)
	return nil
}

// stageRelease is --from's sugar: promote a staged directory into a release
// before update swaps the cohort onto it, so the practical case — "here is
// a directory, make the cluster run it" — is genuinely one command.
func stageRelease(env *opEnv, from, releaseID string) error {
	st, err := store.Open(env.cfg, env.jnl)
	if err != nil {
		return err
	}
	tx, err := st.Begin(releaseID + "-stage")
	if err != nil {
		return err
	}
	if err := fsx.CopyTree(from, tx.Root); err != nil {
		return err
	}
	_, err = st.Promote(tx, env.prof, store.PromoteOpts{
		ReleaseID:  releaseID,
		Provenance: store.Provenance{Kind: store.ProvenanceStaged, Source: from},
	})
	return err
}

func cmdRollback(args []string) error {
	fs := flag.NewFlagSet("rollback", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	owner := writeOwnerFlag(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := applyWriteOwner(cfg, *owner); err != nil {
		return err
	}
	if *opID == "" {
		*opID = "rollback-" + strings.TrimSuffix(*profilePath, ".json")
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	rec, err := env.ctl.Rollback(env.ctx, *opID, env.prof)
	if err != nil {
		return refused(err)
	}
	fmt.Printf("%s: rolled back to release %s (generation %s)\n",
		env.prof.ID, rec.ReleaseID, rec.Generation)
	return nil
}

// ------------------------------------------------------- attach and detach --

func cmdAttach(args []string) error {
	fs := flag.NewFlagSet("attach", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	serverID := fs.String("server", "", "Wings server uuid (required)")
	access := fs.String("access", string(expose.AccessRO), "ro or rw")
	role := fs.String("role", string(assign.RoleSlave),
		"main or slave — `update` orders a cohort cycle by this: every slave stops before "+
			"main, and no slave starts before main signals ready. Exactly one server per "+
			"profile may be main; re-attaching an already-attached server updates its role")
	owner := writeOwnerFlag(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *serverID == "" {
		return errors.New("attach: --server is required")
	}
	if err := applyWriteOwner(cfg, *owner); err != nil {
		return err
	}
	if *opID == "" {
		*opID = "attach-" + *serverID
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	if err := env.ctl.Attach(env.ctx, *opID, *serverID, *access, assign.Role(*role), env.prof); err != nil {
		return refused(err)
	}
	fmt.Printf("%s: server %s attached %s (role=%s)\n", env.prof.ID, *serverID, *access, *role)
	return nil
}

func cmdDetach(args []string) error {
	fs := flag.NewFlagSet("detach", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	serverID := fs.String("server", "", "Wings server uuid (required)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *serverID == "" {
		return errors.New("detach: --server is required")
	}
	if *opID == "" {
		*opID = "detach-" + *serverID
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	if err := env.ctl.Detach(env.ctx, *opID, *serverID, env.prof); err != nil {
		return refused(err)
	}
	fmt.Printf("%s: server %s detached\n", env.prof.ID, *serverID)
	return nil
}

// --------------------------------------------------------------- teardown --

func cmdTeardown(args []string) error {
	fs := flag.NewFlagSet("teardown", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *opID == "" {
		*opID = "teardown"
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	if err := env.ctl.ReleaseGeneration(env.ctx, *opID, env.prof); err != nil {
		return refused(err)
	}
	fmt.Printf("%s: the active generation is torn down; the assignment is unchanged\n", env.prof.ID)
	return nil
}

// ---------------------------------------------------------------- harvest --

func cmdHarvest(args []string) error {
	fs := flag.NewFlagSet("harvest", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	releaseID := fs.String("release", "", "release id the harvest becomes (required)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *releaseID == "" {
		return errors.New("harvest: --release is required — a harvested release is a " +
			"first-class release and its identity is yours to choose")
	}
	if *opID == "" {
		*opID = "harvest-" + *releaseID
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	rel, err := env.ctl.Harvest(env.ctx, *opID, *releaseID, env.prof)
	if err != nil {
		return refused(err)
	}
	fmt.Printf("harvested %s (%d entries, %s) from %s\n",
		rel.ID, len(rel.Manifest.Entries), rel.Manifest.ContentDigest,
		rel.Complete.Provenance.Source)
	fmt.Printf("it is not live until you activate it: srdm activate --profile %s --release %s\n",
		*profilePath, rel.ID)
	return nil
}

// --------------------------------------------------------------------- gc --

func cmdGC(args []string) error {
	fs := flag.NewFlagSet("gc", flag.ContinueOnError)
	cfg, profilePath, opID := opFlags(fs)
	dryRun := fs.Bool("dry-run", false, "report what would be removed, and remove nothing")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *opID == "" {
		*opID = "gc"
	}
	env, err := newOpEnv(*cfg, *profilePath, true)
	if err != nil {
		return err
	}
	defer env.close()

	res, err := env.ctl.GC(*opID, *dryRun)
	if err != nil {
		return refused(err)
	}
	for _, id := range sortedKeys(res.Kept) {
		fmt.Printf("keep   %-32s %s\n", id, res.Kept[id])
	}
	verb := "removed"
	if *dryRun {
		verb = "would remove"
	}
	for _, id := range res.Removed {
		fmt.Printf("%s %s\n", verb, id)
	}
	if len(res.Removed) == 0 {
		fmt.Printf("nothing to collect (retention %d)\n", env.cfg.Retention)
	}
	return nil
}

// ----------------------------------------------------------------- status --

func cmdStatus(args []string) error {
	fs := flag.NewFlagSet("status", flag.ContinueOnError)
	cfg, profilePath, _ := opFlags(fs)
	asJSON := fs.Bool("json", false, "emit the status as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	env, err := newOpEnv(*cfg, *profilePath, false)
	if err != nil {
		return err
	}
	defer env.close()

	statuses, err := env.ctl.Status(env.ctx)
	if err != nil {
		return err
	}
	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(statuses)
	}
	if len(statuses) == 0 {
		fmt.Println("no profile has an assignment yet")
	}
	for _, s := range statuses {
		state := "not published"
		if s.Published {
			state = "published"
		}
		fmt.Printf("%-16s %-32s %s\n", s.Profile, s.Release, state)
		if s.Generation != "" {
			fmt.Printf("  generation %s%s\n", s.Generation, dirtyNote(s.DirtyCapable))
		}
		if s.Previous != "" {
			fmt.Printf("  rollback   %s\n", s.Previous)
		}
		for _, srv := range s.Servers {
			fmt.Printf("  server     %s\n", srv)
		}
		for _, h := range s.Holders {
			fmt.Printf("  holder     %s\n", h)
		}
		for _, d := range s.Degraded {
			fmt.Printf("  DEGRADED   %s\n", d)
		}
	}
	releases, err := env.ctl.Releases()
	if err != nil {
		return err
	}
	if len(releases) > 0 {
		fmt.Printf("releases: %s\n", strings.Join(releases, " "))
	}
	return nil
}

func dirtyNote(dirty bool) string {
	if !dirty {
		return ""
	}
	return " (dirty-capable: exposed writable, so its content no longer provably " +
		"matches the release; republish or harvest)"
}

func sortedKeys(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// persistProfile keeps a durable copy of the profile document an operation
// just used, keyed by its id (D-026).
//
// The boot path has an assignment naming a profile by id and nobody to hand
// it a --profile flag; this is what lets it find the document again. Written
// as a side effect of every state-changing verb succeeding at loading one,
// which is the same rule internal/assign and the published record already
// follow: durable the moment the thing it records becomes true, not as a
// separate step an operator has to remember.
func persistProfile(cfg config.Config, path string, prof *profile.Profile) error {
	if prof == nil {
		return nil
	}
	body, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(cfg.ProfilesDir(), 0o755); err != nil {
		return err
	}
	return fsx.WriteFileSync(cfg.ProfileDocument(prof.ID), body, 0o644)
}

// ------------------------------------------------------------- reconcile --

func cmdReconcile(args []string) error {
	fs := flag.NewFlagSet("reconcile", flag.ContinueOnError)
	cfg, _, opID := opFlags(fs)
	asJSON := fs.Bool("json", false, "emit the report as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *opID == "" {
		*opID = "reconcile"
	}
	env, err := newOpEnv(*cfg, "", false)
	if err != nil {
		return err
	}
	defer env.close()

	rep, err := env.ctl.Reconcile(env.ctx, *opID)
	if err != nil {
		return refused(err)
	}
	return printRestoreReport(rep, *asJSON)
}

// ---------------------------------------------------------------- restore --

func cmdRestore(args []string) error {
	fs := flag.NewFlagSet("restore", flag.ContinueOnError)
	cfg, _, opID := opFlags(fs)
	asJSON := fs.Bool("json", false, "emit the report as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *opID == "" {
		*opID = "restore"
	}
	// No --profile: restore's whole point is having nobody to give it one
	// (srdm-restore.service, After=local-fs.target). Every profile it
	// touches comes from an assignment plus the durable profile copy D-026
	// keeps.
	env, err := newOpEnv(*cfg, "", false)
	if err != nil {
		return err
	}
	defer env.close()

	rep, err := env.ctl.Restore(env.ctx, *opID)
	if err != nil {
		return refused(err)
	}
	if err := printRestoreReport(rep, *asJSON); err != nil {
		return err
	}
	if len(rep.Failed) > 0 {
		return fmt.Errorf("restore: %d profile(s) could not be brought back", len(rep.Failed))
	}
	return nil
}

func printRestoreReport(rep *opctl.RestoreReport, asJSON bool) error {
	if asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(rep)
	}
	printList := func(label string, items []string) {
		for _, item := range items {
			fmt.Printf("%-14s %s\n", label, item)
		}
	}
	printList("adopted", rep.Adopted)
	printList("quarantined", rep.Quarantined)
	printList("ro-fixed", rep.ReadOnlyFixed)
	printList("orphan-gone", rep.OrphansRemoved)
	printList("cleared", rep.Cleared)
	printList("needs-activate", rep.NeedsActivate)
	printList("restored", rep.Restored)
	printList("BLOCKED", rep.Blocked)
	for _, name := range sortedKeys(rep.Failed) {
		fmt.Printf("%-14s %s: %s\n", "FAILED", name, rep.Failed[name])
	}
	total := len(rep.Adopted) + len(rep.Quarantined) + len(rep.ReadOnlyFixed) +
		len(rep.OrphansRemoved) + len(rep.Cleared) + len(rep.NeedsActivate) +
		len(rep.Restored) + len(rep.Blocked) + len(rep.Failed)
	if total == 0 {
		fmt.Println("nothing to reconcile")
	}
	return nil
}
