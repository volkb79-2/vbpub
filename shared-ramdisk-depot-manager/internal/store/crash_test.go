package store

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"testing"
	"time"

	"srdm/internal/fsx"
	"srdm/internal/journal"
)

// --- O1: kill at every phase ----------------------------------------------
//
// This oracle does not simulate a crash with an injected error. It starts a
// real child process, lets it reach a real phase boundary, and SIGKILLs it
// there — the one signal a process cannot handle, defer around or flush
// after. Anything weaker would test srdm's error handling rather than its
// crash safety, and the difference between those two is the entire point of
// writing COMPLETE last.
//
// The synchronization is a pipe, not a clock: the child announces that it
// has reached the boundary and then blocks forever, and the parent kills it
// only after reading that announcement. There is no sleep, no deadline and
// no timing assumption anywhere in the verdict, so a slow machine cannot
// change the result.

const (
	crashPhaseEnv = "SRDM_CRASH_PHASE"
	crashStateEnv = "SRDM_CRASH_STATE"
	crashSrcEnv   = "SRDM_CRASH_SRC"
	crashReady    = "SRDM-CRASH-READY"

	// crashPhaseCopy is the boundary before Promote is even entered: the
	// transaction is populated and nothing else has happened yet.
	crashPhaseCopy = "copy"

	crashReleaseID = "rel-new"
	crashOpID      = "op-new"
	crashChannel   = "stable"
	crashPrevID    = "rel-prev"
)

func TestMain(m *testing.M) {
	if os.Getenv(crashPhaseEnv) != "" {
		crashChild()
		return // unreachable; crashChild never returns
	}
	os.Exit(m.Run())
}

// crashChild performs a promotion inside a subprocess and parks at the
// configured boundary, waiting to be killed.
func crashChild() {
	fail := func(format string, args ...any) {
		fmt.Fprintf(os.Stderr, "crash child: "+format+"\n", args...)
		os.Exit(2)
	}

	phase := os.Getenv(crashPhaseEnv)
	cfg := testConfig(os.Getenv(crashStateEnv))

	jnl, err := journal.New(cfg.JournalDir(),
		journal.WithClock(func() time.Time { return fixedTime }),
		journal.WithJournaldSocket(""))
	if err != nil {
		fail("journal: %v", err)
	}
	st, err := Open(cfg, jnl)
	if err != nil {
		fail("open: %v", err)
	}

	park := func() {
		fmt.Println(crashReady)
		_ = os.Stdout.Sync()
		// Block forever. The parent SIGKILLs us from here.
		select {}
	}

	tx, err := st.Begin(crashOpID)
	if err != nil {
		fail("begin: %v", err)
	}
	if err := fsx.CopyTree(os.Getenv(crashSrcEnv), tx.Root); err != nil {
		fail("copy: %v", err)
	}
	if phase == crashPhaseCopy {
		park()
	}

	st.hook = func(ph Phase) error {
		if string(ph) == phase {
			park()
		}
		return nil
	}

	p := crashProfile
	if err := p.Validate(); err != nil {
		fail("profile: %v", err)
	}
	if _, err := st.Promote(tx, p, PromoteOpts{
		ReleaseID:  crashReleaseID,
		Channel:    crashChannel,
		Provenance: Provenance{Kind: ProvenanceStaged, Source: "crash-test"},
	}); err != nil {
		fail("promote: %v", err)
	}
	// Reaching here means the requested boundary was never hit.
	fail("phase %q was never reached", phase)
}

func TestKillAtEveryPhase(t *testing.T) {
	cases := []struct {
		phase string
		// wantNewRelease is whether rel-new exists in the store afterwards.
		wantNewRelease bool
		// wantChannel is what the channel must resolve to. A crash never
		// leaves it anywhere else.
		wantChannel string
		// wantQuarantined is whether the transaction was moved aside.
		wantQuarantined bool
	}{
		// Before COMPLETE, the transaction is worthless: quarantine it and
		// leave the previous release live.
		{crashPhaseCopy, false, crashPrevID, true},
		{string(PhaseClassify), false, crashPrevID, true},
		{string(PhaseManifest), false, crashPrevID, true},
		{string(PhaseProbes), false, crashPrevID, true},
		{string(PhaseOwnership), false, crashPrevID, true},
		{string(PhasePersist), false, crashPrevID, true},
		// COMPLETE is written and fsync'd: the transaction is whole, so
		// recovery adopts it. The channel has not been told yet.
		{string(PhaseComplete), true, crashPrevID, false},
		// Renamed into the store, channel still untouched.
		{string(PhasePublish), true, crashPrevID, false},
		// The flip is done and durable.
		{string(PhaseFlip), true, crashReleaseID, false},
	}

	for _, tc := range cases {
		t.Run(tc.phase, func(t *testing.T) {
			dir := t.TempDir()

			// A previous release, so "the store contains the previous one"
			// is a claim with something to be about.
			st := newTestStore(t, dir)
			p := crashProfile
			if err := p.Validate(); err != nil {
				t.Fatal(err)
			}
			promoteContent(t, st, p, "op-prev", crashPrevID, crashChannel, contentA)

			src := filepath.Join(dir, "src")
			writeTree(t, src, contentA)
			// One byte different from the previous release, so an adopted
			// release is distinguishable from the old one.
			if err := os.WriteFile(filepath.Join(src, "Engine", "libengine.so"),
				[]byte("engine bytes v2"), 0o644); err != nil {
				t.Fatal(err)
			}
			normalizeModes(t, src)

			killChildAtPhase(t, dir, src, tc.phase)

			// Recovery runs in a fresh process image, exactly as it would at
			// boot.
			st2 := newTestStore(t, dir)
			report, err := st2.Recover("op-recover")
			if err != nil {
				t.Fatalf("Recover: %v", err)
			}

			// The invariant that holds at every single boundary: whatever is
			// in the store is whole.
			ids, err := st2.List()
			if err != nil {
				t.Fatal(err)
			}
			for _, id := range ids {
				if err := st2.Verify(id); err != nil {
					t.Fatalf("release %q survived the crash but does not verify: %v", id, err)
				}
			}

			hasNew := slices.Contains(ids, crashReleaseID)
			if hasNew != tc.wantNewRelease {
				t.Errorf("rel-new present = %v, want %v (releases: %v)", hasNew, tc.wantNewRelease, ids)
			}
			if !slices.Contains(ids, crashPrevID) {
				t.Errorf("the previous release vanished (releases: %v)", ids)
			}

			got, err := st2.Resolve("testgame", crashChannel)
			if err != nil {
				t.Fatalf("the channel does not resolve after recovery: %v", err)
			}
			if got != tc.wantChannel {
				t.Errorf("channel resolves to %q, want %q", got, tc.wantChannel)
			}

			// Half-promoted is never an option: the channel points at
			// something that verifies.
			if err := st2.Verify(got); err != nil {
				t.Errorf("the channel points at a release that does not verify: %v", err)
			}

			quarantined := len(report.Quarantined) > 0
			if quarantined != tc.wantQuarantined {
				t.Errorf("quarantined = %v (%v), want %v",
					quarantined, report.Quarantined, tc.wantQuarantined)
			}
			// Nothing is left in flight.
			if entries, err := os.ReadDir(st2.cfg.TxDir()); err != nil {
				t.Fatal(err)
			} else if len(entries) != 0 {
				t.Errorf("recovery left %d transaction(s) behind", len(entries))
			}

			// "and the journal names which": recovery records the channel's
			// resolved release, durably, where an operator will look.
			if report.Channels["testgame/"+crashChannel] != got {
				t.Errorf("the recovery report does not name the channel's release: %v", report.Channels)
			}
			assertRecoveryJournalNames(t, st2, "testgame/"+crashChannel, got)
		})
	}
}

// killChildAtPhase runs the promotion in a child process and SIGKILLs it the
// moment it reports having reached the boundary.
func killChildAtPhase(t *testing.T, stateDir, srcDir, phase string) {
	t.Helper()

	cmd := exec.Command(os.Args[0])
	cmd.Env = append(os.Environ(),
		crashPhaseEnv+"="+phase,
		crashStateEnv+"="+stateDir,
		crashSrcEnv+"="+srcDir,
	)
	cmd.Stderr = os.Stderr
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the crash child: %v", err)
	}

	// Waiting on the child's announcement is a real synchronization point.
	// If the child dies instead of reaching the boundary the pipe closes and
	// Scan returns false, which fails the test with a clear reason rather
	// than hanging.
	reached := false
	scanner := bufio.NewScanner(stdout)
	for scanner.Scan() {
		if strings.TrimSpace(scanner.Text()) == crashReady {
			reached = true
			break
		}
	}
	if !reached {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		t.Fatalf("the child never reached phase %q", phase)
	}

	if err := cmd.Process.Signal(syscall.SIGKILL); err != nil {
		t.Fatalf("SIGKILL: %v", err)
	}
	// The child was killed, so a non-nil error here is expected and carries
	// no information worth asserting on.
	_ = cmd.Wait()
}

func assertRecoveryJournalNames(t *testing.T, st *Store, channel, releaseID string) {
	t.Helper()
	op, err := journal.LoadOperation(st.cfg.JournalDir(), "op-recover")
	if err != nil {
		t.Fatalf("recovery left no durable record: %v", err)
	}
	for _, ev := range op.Events {
		if ev.Fields["channel"] == channel && ev.Fields["release_id"] == releaseID {
			return
		}
	}
	t.Fatalf("the recovery record does not name %s -> %s; events: %+v", channel, releaseID, op.Events)
}

// crashProfile is shared by the parent and the child, so both sides agree on
// classification without passing a profile document between processes.
var crashProfile = newTestProfile()
