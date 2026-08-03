package doctor

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"srdm/internal/config"
	"srdm/internal/publish"
	"srdm/internal/store"
	"srdm/internal/wings"
)

// checkWings runs the two host-bind preconditions doctor can answer without
// being asked to expose anything.
//
// They are here rather than only in the driver because an operator wants
// them BEFORE the migration window, not during it. `srdm doctor` on a node
// that has not set up propagation should say so on a quiet afternoon; the
// driver's refusal is the same fact arriving at the worst possible moment.
func checkWings(cfg config.Config, opts Options) []Check {
	if cfg.Wings == (config.Wings{}) {
		// No Wings settings means srdm is not exposing into a node — the
		// store and publication halves work perfectly well alone, and
		// inventing a default node to check against would report failures
		// about a deployment that does not exist.
		return nil
	}
	return []Check{
		checkWingsPropagation(cfg, opts),
		checkWingsChownWalk(cfg, opts),
		checkDirtyGenerations(cfg),
	}
}

// checkDirtyGenerations re-hashes every generation that has been exposed
// writable, and reports what drifted.
//
// The flag records that writing became POSSIBLE; only this says whether
// anything was actually written. Both are needed. The flag is what makes
// srdm refuse to share the generation, and it is set conservatively — but an
// operator deciding whether to republish wants to know if the content
// actually moved, and the only way to know is to hash it.
//
// Clean generations are not re-hashed. On a 2.4 GB payload that is minutes
// of I/O to confirm something nothing has written to, and doctor is a thing
// operators must be willing to run.
func checkDirtyGenerations(cfg config.Config) Check {
	c := Check{ID: "generation-drift", Title: "generations exposed writable still match their release"}

	records, err := publish.ListRecordsAt(cfg)
	if err != nil {
		c.Status = StatusFail
		c.Detail = "cannot read the published-state records: " + err.Error()
		c.Fix = "check " + cfg.PublishedDir() + " is readable and its records parse"
		return c
	}

	var dirty, drifted []string
	for _, rec := range records {
		if !rec.DirtyCapable {
			continue
		}
		name := rec.Profile + "/" + rec.Generation
		dirty = append(dirty, name)
		rel, err := store.LoadReleaseDir(rec.ReleaseID, cfg.ReleaseDir(rec.ReleaseID))
		if err != nil {
			drifted = append(drifted, fmt.Sprintf("%s (its release %s is unreadable: %v)",
				name, rec.ReleaseID, err))
			continue
		}
		for _, class := range rec.Classes {
			if err := rel.Manifest.VerifyClass(class.ExposePath, class.Name); err != nil {
				drifted = append(drifted, fmt.Sprintf("%s class %q: %v", name, class.Name, err))
			}
		}
	}

	switch {
	case len(dirty) == 0:
		c.Status = StatusPass
		c.Detail = "no generation has been exposed writable"
	case len(drifted) == 0:
		// Still not a pass: the generation cannot be shared or used as a
		// source while it is marked, whatever the hashes say.
		c.Status = StatusWarn
		c.Detail = fmt.Sprintf("%s has been exposed writable but still matches its release; "+
			"it remains unshareable until republished", strings.Join(dirty, ", "))
		c.Fix = "republish it to clear the mark, or leave it if the single consumer is " +
			"still using it"
	default:
		c.Status = StatusFail
		c.Detail = "content written through an rw exposure no longer matches its release: " +
			strings.Join(drifted, "; ")
		c.Fix = "republish the generation from the store, or harvest the change into a new " +
			"release if it was deliberate"
	}
	sort.Strings(drifted)
	return c
}

// checkWingsPropagation is host-bind precondition 1.
//
// Both halves have to hold and they fail differently. Without a shared host
// peer group there is nothing for the container's rslave to be a slave of;
// without rslave on the container the host side propagates to nobody. Under
// Docker's default rprivate every mount srdm makes is invisible to Wings and
// every unmount leaves a ghost Wings still traverses — which is the
// 2026-07-31 outage, and the reason this refuses rather than warns.
func checkWingsPropagation(cfg config.Config, opts Options) Check {
	c := Check{ID: "wings-propagation",
		Title: "host-side mounts reach the Wings container"}

	prop, err := wings.ReadPropagation(context.Background(), cfg.Wings,
		opts.mountInfoPath(), opts.inspector())
	if err != nil {
		c.Status = StatusFail
		c.Detail = err.Error()
		c.Fix = "check that " + cfg.Wings.BindRoot + " exists and is on a mounted filesystem"
		return c
	}

	if !prop.HostShared() {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("the host mount carrying %s (%s) is %s, not shared",
			cfg.Wings.BindRoot, prop.HostMountPoint, prop.HostPeerGroup)
		c.Fix = "mount --make-rshared " + prop.HostMountPoint +
			" (on a systemd host / is shared by default, so something made this subtree private)"
		return c
	}
	if prop.Degraded != "" {
		// Not a failure: the host half is right and srdm simply could not
		// ask about the container. Saying "pass" would be a lie and saying
		// "fail" would send an operator hunting for a problem that may not
		// exist.
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("the host side is %s, but srdm could not read the Wings "+
			"container's propagation: %s", prop.HostPeerGroup, prop.Degraded)
		c.Fix = "make the Docker socket readable by srdm so this can be checked before a " +
			"migration window rather than during one"
		return c
	}
	if !prop.ContainerReceives() {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("the Wings container %q binds %s with propagation %q",
			prop.ContainerName, cfg.Wings.BindRoot, prop.Container)
		c.Fix = fmt.Sprintf("set that bind to rslave (compose: `- %s:%s:rslave`) and recreate "+
			"the container; under rprivate every mount srdm makes is invisible to Wings and "+
			"every unmount leaves a ghost", cfg.Wings.BindRoot, cfg.Wings.BindRoot)
		return c
	}

	c.Status = StatusPass
	c.Detail = fmt.Sprintf("%s is %s on the host and %q binds it %s",
		prop.HostMountPoint, prop.HostPeerGroup, prop.ContainerName, prop.Container)
	return c
}

// checkWingsChownWalk is host-bind precondition 2.
//
// Wings' pre-boot walk chowns every entry under a server's volume
// unconditionally, and chown on a read-only mount returns EROFS even when
// the owner already matches. So a read-only bind anywhere under the volume
// makes that server unstartable — which an operator meets as an unexplained
// start failure unless something says so first.
func checkWingsChownWalk(cfg config.Config, opts Options) Check {
	c := Check{ID: "wings-chown-walk",
		Title: "a read-only exposure will not break server starts"}

	if cfg.Wings.ChownSkipPatch {
		c.Status = StatusPass
		c.Detail = "this node asserts the running Wings build carries F1, which skips the " +
			"pre-boot chown walk over read-only mounts"
		return c
	}

	walk, err := opts.chownWalk(cfg.Wings)()
	if err != nil {
		c.Status = StatusFail
		c.Detail = err.Error()
		c.Fix = "set system.check_permissions_on_boot: false in " + cfg.Wings.ConfigPath +
			", or run a Wings build carrying F1 and set wings.chown_skip_patch"
		return c
	}
	if walk.Enabled {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("Wings will run its pre-boot chown walk (%s) and this node "+
			"does not assert F1; every read-only exposure would make its server unstartable "+
			"with EROFS", walk.Source)
		c.Fix = "set system.check_permissions_on_boot: false in " + cfg.Wings.ConfigPath +
			", or run a Wings build carrying F1 and set wings.chown_skip_patch"
		return c
	}

	c.Status = StatusPass
	c.Detail = "system.check_permissions_on_boot is false, so the pre-boot walk will not " +
		"chown its way into a read-only mount"
	return c
}
