// Package doctor answers "would srdm work here, and if not, what exactly do
// I fix". Every failing check names the remedy; a check that can only say
// "something is wrong" is not worth an operator's night.
//
// P01 ships the offline subset: cgroup v2 and its controllers,
// memory_recursiveprot, the parent slice's protection budget, and store
// integrity. The mount-propagation and Wings checks arrive with P02, where
// there is a publication topology for them to be about.
package doctor

import (
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"

	"srdm/internal/config"
	"srdm/internal/profile"
	"srdm/internal/store"
)

// Status is a check's verdict.
type Status string

const (
	// StatusPass means the check holds.
	StatusPass Status = "pass"
	// StatusFail means srdm will not work correctly until it is fixed.
	StatusFail Status = "fail"
	// StatusWarn means srdm will work but something is worth knowing.
	StatusWarn Status = "warn"
	// StatusSkip means the check could not run and says why.
	StatusSkip Status = "skip"
)

// Check is one diagnostic result.
type Check struct {
	ID     string `json:"id"`
	Title  string `json:"title"`
	Status Status `json:"status"`
	Detail string `json:"detail,omitempty"`
	// Fix is the actionable remedy, present whenever Status is not pass.
	Fix string `json:"fix,omitempty"`
}

// Options injects the host surfaces doctor reads, so the whole offline
// subset is exercisable against a fixture tree.
type Options struct {
	// CgroupRoot defaults to /sys/fs/cgroup.
	CgroupRoot string
	// MountInfoPath defaults to /proc/self/mountinfo.
	MountInfoPath string
	// UnitProperty reads one systemd unit property. It defaults to
	// systemctl show, and returns an error when systemd is unreachable.
	UnitProperty func(unit, property string) (string, error)
}

const (
	defaultCgroupRoot    = "/sys/fs/cgroup"
	defaultMountInfoPath = "/proc/self/mountinfo"
)

func (o Options) cgroupRoot() string {
	if o.CgroupRoot != "" {
		return o.CgroupRoot
	}
	return defaultCgroupRoot
}

func (o Options) mountInfoPath() string {
	if o.MountInfoPath != "" {
		return o.MountInfoPath
	}
	return defaultMountInfoPath
}

func (o Options) unitProperty() func(string, string) (string, error) {
	if o.UnitProperty != nil {
		return o.UnitProperty
	}
	return systemctlShow
}

// RequiredControllers are the cgroup v2 controllers srdm's publication layer
// needs: memory for the class floors, io and cpu for the stage/publish
// workers' limits.
var RequiredControllers = []string{"cpu", "io", "memory"}

// Run executes the offline subset and returns every check, in a stable
// order.
//
// The profile may be nil, in which case the checks that depend on class
// floors are skipped rather than guessed at.
func Run(cfg config.Config, p *profile.Profile, opts Options) []Check {
	checks := []Check{
		checkCgroupV2(opts),
		checkControllers(opts),
		checkRecursiveProt(opts),
		checkParentSlice(cfg, p, opts),
	}
	checks = append(checks, checkStoreIntegrity(cfg)...)
	return checks
}

// Failed returns the checks that would stop srdm working.
func Failed(checks []Check) []Check {
	var out []Check
	for _, c := range checks {
		if c.Status == StatusFail {
			out = append(out, c)
		}
	}
	return out
}

func checkCgroupV2(opts Options) Check {
	c := Check{ID: "cgroup-v2", Title: "cgroup v2 unified hierarchy is mounted"}
	path := opts.cgroupRoot() + "/cgroup.controllers"
	if _, err := os.Stat(path); err != nil {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("%s is not readable: %v", path, err)
		c.Fix = "boot with systemd.unified_cgroup_hierarchy=1 (cgroup v1 and hybrid " +
			"modes have no memory.min, so every class floor srdm sets would be ignored)"
		return c
	}
	c.Status = StatusPass
	c.Detail = opts.cgroupRoot() + " is a cgroup v2 hierarchy"
	return c
}

func checkControllers(opts Options) Check {
	c := Check{ID: "cgroup-controllers", Title: "required cgroup controllers are available"}
	body, err := os.ReadFile(opts.cgroupRoot() + "/cgroup.controllers")
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read cgroup.controllers: %v", err)
		return c
	}
	have := make(map[string]bool)
	for _, f := range strings.Fields(string(body)) {
		have[f] = true
	}
	var missing []string
	for _, want := range RequiredControllers {
		if !have[want] {
			missing = append(missing, want)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		c.Status = StatusFail
		c.Detail = "missing controller(s): " + strings.Join(missing, ", ")
		c.Fix = "enable them on the root cgroup (echo '+" + missing[0] +
			"' > " + opts.cgroupRoot() + "/cgroup.subtree_control) or check the kernel config"
		return c
	}
	c.Status = StatusPass
	c.Detail = "have " + strings.Join(RequiredControllers, ", ")
	return c
}

// RecursiveProtOption is the cgroup2 mount option without which every
// MemoryMin on a parent slice stops protecting the pages below it.
const RecursiveProtOption = "memory_recursiveprot"

func checkRecursiveProt(opts Options) Check {
	c := Check{ID: "cgroup-recursiveprot", Title: "cgroup2 is mounted with " + RecursiveProtOption}
	body, err := os.ReadFile(opts.mountInfoPath())
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read %s: %v", opts.mountInfoPath(), err)
		return c
	}
	mnt, ok := findCgroup2Mount(string(body))
	if !ok {
		c.Status = StatusFail
		c.Detail = "no cgroup2 mount found in " + opts.mountInfoPath()
		c.Fix = "mount the unified hierarchy at " + opts.cgroupRoot()
		return c
	}
	for _, o := range mnt.superOptions {
		if o == RecursiveProtOption {
			c.Status = StatusPass
			c.Detail = mnt.mountPoint + " has " + RecursiveProtOption
			return c
		}
	}
	c.Status = StatusFail
	c.Detail = mnt.mountPoint + " super options are: " + strings.Join(mnt.superOptions, ",")
	c.Fix = "remount with it (mount -o remount," + RecursiveProtOption + " " + mnt.mountPoint +
		"). systemd sets it at boot, but a runtime remount from the init cgroup namespace " +
		"can strip it — and without it every class floor beneath " +
		"the parent slice is silently inert"
	return c
}

func checkParentSlice(cfg config.Config, p *profile.Profile, opts Options) Check {
	c := Check{ID: "parent-slice", Title: cfg.Slice + " backs the class floors"}

	loadState, err := opts.unitProperty()(cfg.Slice, "LoadState")
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot query %s: %v", cfg.Slice, err)
		return c
	}
	if loadState != "loaded" {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("%s LoadState is %q", cfg.Slice, loadState)
		// A missing slice name does not error at container-create time —
		// systemd silently creates an unlimited transient slice — so this
		// check has to fail closed or the whole protection budget is a
		// fiction nobody notices.
		c.Fix = "install the admin-owned " + cfg.Slice + " unit and run systemctl daemon-reload; " +
			"an absent slice name does not fail loudly, it fails OPEN as an unlimited transient slice"
		return c
	}

	if p == nil {
		c.Status = StatusSkip
		c.Detail = cfg.Slice + " is loaded; no profile given, so its floor was not checked"
		return c
	}
	want := p.ManagedFloorSum()

	raw, err := opts.unitProperty()(cfg.Slice, "MemoryMin")
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read %s MemoryMin: %v", cfg.Slice, err)
		return c
	}
	got, err := parseMemoryValue(raw)
	if err != nil {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("%s MemoryMin is %q, which is not a byte count", cfg.Slice, raw)
		c.Fix = "set MemoryMin to at least " + formatBytes(want) + " in the " + cfg.Slice + " unit"
		return c
	}
	if got < want {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("%s MemoryMin is %s, the profile's managed class floors sum to %s",
			cfg.Slice, formatBytes(got), formatBytes(want))
		// A child's floor is only effective while its parent's covers it;
		// below that the class floors are arithmetically dead.
		c.Fix = "raise MemoryMin to at least " + formatBytes(want) + " in the admin-owned " +
			cfg.Slice + " unit (srdm verifies this rather than writing it)"
		return c
	}
	c.Status = StatusPass
	c.Detail = fmt.Sprintf("%s MemoryMin is %s, covering the %s of managed class floors",
		cfg.Slice, formatBytes(got), formatBytes(want))
	return c
}

func checkStoreIntegrity(cfg config.Config) []Check {
	c := Check{ID: "store-integrity", Title: "every release verifies against its manifest"}

	ids, err := listReleases(cfg)
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read %s: %v", cfg.ReleasesDir(), err)
		return []Check{c}
	}
	if len(ids) == 0 {
		c.Status = StatusPass
		c.Detail = "the store holds no releases yet"
		return []Check{c}
	}

	out := make([]Check, 0, len(ids)+1)
	bad := 0
	for _, id := range ids {
		rc := Check{ID: "release/" + id, Title: "release " + id + " verifies"}
		rel, err := loadRelease(cfg, id)
		if err != nil {
			bad++
			rc.Status = StatusFail
			rc.Detail = err.Error()
			rc.Fix = "the release is not usable; quarantine it with `srdm recover` and re-stage it"
			out = append(out, rc)
			continue
		}
		if err := rel.Manifest.Verify(rel.RootDir()); err != nil {
			bad++
			rc.Status = StatusFail
			rc.Detail = err.Error()
			rc.Fix = "content drifted from the manifest; re-stage the release rather than repairing it in place"
			out = append(out, rc)
			continue
		}
		rc.Status = StatusPass
		rc.Detail = fmt.Sprintf("%d manifest entries", len(rel.Manifest.Entries))
		out = append(out, rc)
	}

	if bad > 0 {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("%d of %d releases do not verify", bad, len(ids))
		c.Fix = "see the per-release checks below"
	} else {
		c.Status = StatusPass
		c.Detail = fmt.Sprintf("all %d releases verify", len(ids))
	}
	return append([]Check{c}, out...)
}

// listReleases and loadRelease keep doctor's dependency on the store to a
// read-only surface: doctor never opens a store for writing, because it must
// stay usable against a store it does not own.
func listReleases(cfg config.Config) ([]string, error) {
	entries, err := os.ReadDir(cfg.ReleasesDir())
	if err != nil {
		return nil, err
	}
	var out []string
	for _, e := range entries {
		if e.IsDir() {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out, nil
}

func loadRelease(cfg config.Config, id string) (*store.Release, error) {
	return store.LoadReleaseDir(id, cfg.ReleaseDir(id))
}

func systemctlShow(unit, property string) (string, error) {
	out, err := exec.Command("systemctl", "show", unit, "--property="+property, "--value").Output()
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

// parseMemoryValue reads a systemd memory property. systemd renders an unset
// limit as "infinity" and "[not set]" for some properties.
func parseMemoryValue(raw string) (int64, error) {
	raw = strings.TrimSpace(raw)
	switch raw {
	case "infinity":
		// An infinite MemoryMin is not a thing systemd accepts, but if it
		// ever reports one, treating it as "covers everything" is right.
		return 1<<63 - 1, nil
	case "", "[not set]":
		return 0, nil
	}
	return strconv.ParseInt(raw, 10, 64)
}

func formatBytes(n int64) string {
	const unit = 1024
	if n < unit {
		return strconv.FormatInt(n, 10) + "B"
	}
	div, exp := int64(unit), 0
	for v := n / unit; v >= unit && exp < 3; v /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.4g%c", float64(n)/float64(div), "KMGT"[exp])
}
