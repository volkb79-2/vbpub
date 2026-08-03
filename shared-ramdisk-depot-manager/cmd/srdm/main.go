// Command srdm is the shared-ramdisk-depot-manager: one binary carrying the
// daemon and every CLI subcommand.
//
// P01 ships the store, the journal and the offline half of doctor.
// Subcommands whose machinery arrives in a later phase say so by name
// rather than being quietly absent.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"srdm/internal/config"
	"srdm/internal/doctor"
	"srdm/internal/fsx"
	"srdm/internal/hold"
	"srdm/internal/journal"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// Version is the binary's version, set at build time.
var Version = "0.1.0-P01"

func main() {
	// The hold worker is this same binary in a different role: it is what a
	// srdm-hold-<g8>-<class>.service runs, so that the pages of a class are
	// faulted inside the cgroup carrying that class's memory policy.
	//
	// It is dispatched here, ahead of run(), because its EXIT STATUS is the
	// contract — the daemon reads it back from systemd to tell a refusal
	// (out of space) from a fault (D-017), and run()'s error path would
	// collapse every one of them to 1.
	if len(os.Args) > 1 && os.Args[1] == hold.WorkerSubcommand {
		os.Exit(hold.RunWorker(os.Args[2:], os.Stderr))
	}
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "srdm: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		usage()
		return errors.New("no subcommand given")
	}
	switch args[0] {
	case "version":
		fmt.Println(Version)
		return nil
	case "doctor":
		return cmdDoctor(args[1:])
	case "store":
		return cmdStore(args[1:])
	case "journal":
		return cmdJournal(args[1:])
	case "help", "-h", "--help":
		usage()
		return nil
	case "daemon", "stage", "harvest", "status", "activate", "rollback", "gc", "operation":
		return fmt.Errorf("%q has no CLI verb yet: the store, the journal and offline doctor "+
			"ship today. The whole v1 pipeline exists as libraries with no operator entry "+
			"point — publication and hold units (internal/publish, internal/hold), the "+
			"host-bind exposure driver (internal/expose) and harvest (internal/harvest). "+
			"The verbs that drive them, the daemon, gc and boot restore arrive with P08",
			args[0])
	default:
		usage()
		return fmt.Errorf("unknown subcommand %q", args[0])
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `srdm — shared-ramdisk-depot-manager

  srdm doctor  [--offline] [--profile <file>] [--json]
  srdm store   promote  --profile <file> --release <id> --from <dir> [--channel <c>] [--op <id>]
  srdm store   activate --profile <id> --channel <c> --release <id> [--op <id>]
  srdm store   verify   [--release <id>]
  srdm store   list
  srdm store   recover  [--op <id>]
  srdm journal list
  srdm journal show --op <id>
  srdm version

Not for operators — srdm runs this on itself, as a hold unit's ExecStart:
  srdm hold-worker --release-dir <dir> --release-id <id> --class <c> --target <dir>

Common flags — accepted by every subcommand, written AFTER it:
  --state-dir <path>   persistent root (default `+config.DefaultStateDir+`)
  --run-dir   <path>   volatile root  (default `+config.DefaultRunDir+`)
  --slice     <unit>   parent slice   (default `+config.DefaultSlice+`)

  e.g.  srdm store list --state-dir /srv/srdm
`)
}

// commonFlags registers the flags every subcommand shares.
func commonFlags(fs *flag.FlagSet) *config.Config {
	cfg := config.Default()
	fs.StringVar(&cfg.StateDir, "state-dir", cfg.StateDir, "persistent state root")
	fs.StringVar(&cfg.RunDir, "run-dir", cfg.RunDir, "volatile runtime root")
	fs.StringVar(&cfg.Slice, "slice", cfg.Slice, "admin-owned parent slice unit")
	return &cfg
}

func openJournal(cfg config.Config, p *profile.Profile) (*journal.Journal, error) {
	var opts []journal.Option
	if p != nil {
		opts = append(opts, journal.WithSecrets(p.Secrets()...))
	}
	return journal.New(cfg.JournalDir(), opts...)
}

func loadProfile(path string) (*profile.Profile, error) {
	if path == "" {
		return nil, nil
	}
	return profile.Load(path)
}

// ---------------------------------------------------------------- doctor --

func cmdDoctor(args []string) error {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	cfg := commonFlags(fs)
	profilePath := fs.String("profile", "", "profile document, for the class-floor check")
	asJSON := fs.Bool("json", false, "emit checks as JSON")
	// P01 only has the offline subset; the flag exists so the eventual
	// online checks do not change the invocation operators learn now.
	fs.Bool("offline", true, "run only checks that need no daemon (P01: all of them)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := cfg.Validate(); err != nil {
		return err
	}
	p, err := loadProfile(*profilePath)
	if err != nil {
		return err
	}

	checks := doctor.Run(*cfg, p, doctor.Options{})

	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(checks); err != nil {
			return err
		}
	} else {
		for _, c := range checks {
			fmt.Printf("%-6s %-22s %s\n", strings.ToUpper(string(c.Status)), c.ID, c.Title)
			if c.Detail != "" {
				fmt.Printf("       %s\n", c.Detail)
			}
			if c.Fix != "" {
				fmt.Printf("       fix: %s\n", c.Fix)
			}
		}
	}
	if failed := doctor.Failed(checks); len(failed) > 0 {
		return fmt.Errorf("%d check(s) failed", len(failed))
	}
	return nil
}

// ----------------------------------------------------------------- store --

func cmdStore(args []string) error {
	if len(args) == 0 {
		return errors.New("store: expected promote, activate, verify, list or recover")
	}
	switch args[0] {
	case "promote":
		return cmdStorePromote(args[1:])
	case "activate":
		return cmdStoreActivate(args[1:])
	case "verify":
		return cmdStoreVerify(args[1:])
	case "list":
		return cmdStoreList(args[1:])
	case "recover":
		return cmdStoreRecover(args[1:])
	default:
		return fmt.Errorf("store: unknown subcommand %q", args[0])
	}
}

func cmdStorePromote(args []string) error {
	fs := flag.NewFlagSet("store promote", flag.ContinueOnError)
	cfg := commonFlags(fs)
	profilePath := fs.String("profile", "", "profile document (required)")
	releaseID := fs.String("release", "", "release id (required)")
	from := fs.String("from", "", "directory holding the content (required)")
	channel := fs.String("channel", "", "flip this channel to the new release")
	opID := fs.String("op", "", "operation id (defaults to the release id)")
	source := fs.String("source", "", "provenance source, recorded in COMPLETE")
	if err := fs.Parse(args); err != nil {
		return err
	}
	for name, val := range map[string]string{"--profile": *profilePath, "--release": *releaseID, "--from": *from} {
		if val == "" {
			return fmt.Errorf("store promote: %s is required", name)
		}
	}
	if *opID == "" {
		*opID = *releaseID
	}
	if err := cfg.Validate(); err != nil {
		return err
	}

	p, err := loadProfile(*profilePath)
	if err != nil {
		return err
	}
	jnl, err := openJournal(*cfg, p)
	if err != nil {
		return err
	}
	defer jnl.Close()

	st, err := store.Open(*cfg, jnl)
	if err != nil {
		return err
	}
	tx, err := st.Begin(*opID)
	if err != nil {
		return err
	}
	if err := fsx.CopyTree(*from, tx.Root); err != nil {
		return err
	}

	rel, err := st.Promote(tx, p, store.PromoteOpts{
		ReleaseID:  *releaseID,
		Channel:    *channel,
		Provenance: store.Provenance{Kind: store.ProvenanceStaged, Source: *source},
	})
	if err != nil {
		// The transaction is deliberately left in place rather than deleted:
		// it is the evidence of what went wrong. Say where it is, or an
		// operator finds it later as unexplained disk use.
		fmt.Fprintf(os.Stderr,
			"srdm: transaction %q is left at %s; run `srdm store recover` to quarantine it\n",
			tx.ID, tx.Dir)
		if store.IsRefusal(err) {
			// A refusal is srdm declining by contract, not a fault. Say so,
			// so an operator does not go hunting for a bug.
			return fmt.Errorf("refused: %w", err)
		}
		return err
	}
	fmt.Printf("promoted %s (%d entries, %s)\n",
		rel.ID, len(rel.Manifest.Entries), rel.Manifest.ContentDigest)
	if *channel != "" {
		fmt.Printf("channel %s/%s -> %s\n", p.ID, *channel, rel.ID)
	}
	return nil
}

func cmdStoreActivate(args []string) error {
	fs := flag.NewFlagSet("store activate", flag.ContinueOnError)
	cfg := commonFlags(fs)
	profileID := fs.String("profile", "", "profile id (required)")
	channel := fs.String("channel", "", "channel (required)")
	releaseID := fs.String("release", "", "release id (required)")
	opID := fs.String("op", "", "operation id")
	if err := fs.Parse(args); err != nil {
		return err
	}
	for name, val := range map[string]string{"--profile": *profileID, "--channel": *channel, "--release": *releaseID} {
		if val == "" {
			return fmt.Errorf("store activate: %s is required", name)
		}
	}
	if *opID == "" {
		*opID = "activate-" + *profileID + "-" + *channel
	}
	if err := cfg.Validate(); err != nil {
		return err
	}
	jnl, err := openJournal(*cfg, nil)
	if err != nil {
		return err
	}
	defer jnl.Close()
	st, err := store.Open(*cfg, jnl)
	if err != nil {
		return err
	}
	if err := st.Activate(*opID, *profileID, *channel, *releaseID); err != nil {
		return err
	}
	fmt.Printf("channel %s/%s -> %s\n", *profileID, *channel, *releaseID)
	return nil
}

func cmdStoreVerify(args []string) error {
	fs := flag.NewFlagSet("store verify", flag.ContinueOnError)
	cfg := commonFlags(fs)
	releaseID := fs.String("release", "", "release id (default: every release)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := cfg.Validate(); err != nil {
		return err
	}
	jnl, err := openJournal(*cfg, nil)
	if err != nil {
		return err
	}
	defer jnl.Close()
	st, err := store.Open(*cfg, jnl)
	if err != nil {
		return err
	}

	ids := []string{*releaseID}
	if *releaseID == "" {
		ids, err = st.List()
		if err != nil {
			return err
		}
	}
	var bad int
	for _, id := range ids {
		if err := st.Verify(id); err != nil {
			bad++
			fmt.Printf("FAIL %s: %v\n", id, err)
			continue
		}
		fmt.Printf("OK   %s\n", id)
	}
	if bad > 0 {
		return fmt.Errorf("%d of %d releases do not verify", bad, len(ids))
	}
	return nil
}

func cmdStoreList(args []string) error {
	fs := flag.NewFlagSet("store list", flag.ContinueOnError)
	cfg := commonFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := cfg.Validate(); err != nil {
		return err
	}
	jnl, err := openJournal(*cfg, nil)
	if err != nil {
		return err
	}
	defer jnl.Close()
	st, err := store.Open(*cfg, jnl)
	if err != nil {
		return err
	}
	ids, err := st.List()
	if err != nil {
		return err
	}
	for _, id := range ids {
		rel, err := st.Load(id)
		if err != nil {
			fmt.Printf("%-32s BROKEN %v\n", id, err)
			continue
		}
		fmt.Printf("%-32s %s %s %s\n", id, rel.Complete.Profile,
			rel.Complete.Provenance.Kind, rel.Manifest.ContentDigest)
	}

	// Channels are the operator-visible answer to "what is live".
	profiles, err := os.ReadDir(cfg.ChannelsDir())
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	for _, pe := range profiles {
		links, err := os.ReadDir(filepath.Join(cfg.ChannelsDir(), pe.Name()))
		if err != nil {
			return err
		}
		for _, le := range links {
			target, err := st.Resolve(pe.Name(), le.Name())
			if err != nil {
				continue
			}
			fmt.Printf("channel %s/%s -> %s\n", pe.Name(), le.Name(), target)
		}
	}
	return nil
}

func cmdStoreRecover(args []string) error {
	fs := flag.NewFlagSet("store recover", flag.ContinueOnError)
	cfg := commonFlags(fs)
	opID := fs.String("op", "recover", "operation id")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := cfg.Validate(); err != nil {
		return err
	}
	jnl, err := openJournal(*cfg, nil)
	if err != nil {
		return err
	}
	defer jnl.Close()
	st, err := store.Open(*cfg, jnl)
	if err != nil {
		return err
	}
	report, err := st.Recover(*opID)
	if err != nil {
		return err
	}
	for _, id := range report.Adopted {
		fmt.Printf("adopted     %s\n", id)
	}
	for _, id := range report.Quarantined {
		fmt.Printf("quarantined %s\n", id)
	}
	names := make([]string, 0, len(report.Channels))
	for name := range report.Channels {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		fmt.Printf("channel     %s -> %s\n", name, report.Channels[name])
	}
	for name, reason := range report.DanglingChannels {
		fmt.Printf("DANGLING    %s: %s\n", name, reason)
	}
	return nil
}

// --------------------------------------------------------------- journal --

func cmdJournal(args []string) error {
	if len(args) == 0 {
		return errors.New("journal: expected list or show")
	}
	fs := flag.NewFlagSet("journal "+args[0], flag.ContinueOnError)
	cfg := commonFlags(fs)
	opID := fs.String("op", "", "operation id")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	if err := cfg.Validate(); err != nil {
		return err
	}

	switch args[0] {
	case "list":
		ids, err := journal.ListOperations(cfg.JournalDir())
		if err != nil {
			return err
		}
		for _, id := range ids {
			fmt.Println(id)
		}
		return nil
	case "show":
		if *opID == "" {
			return errors.New("journal show: --op is required")
		}
		op, err := journal.LoadOperation(cfg.JournalDir(), *opID)
		if err != nil {
			return err
		}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(op)
	default:
		return fmt.Errorf("journal: unknown subcommand %q", args[0])
	}
}
