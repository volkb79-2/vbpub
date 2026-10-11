# RG-55 P5 live placement-barrier probe

This fixture is for the controller's bounded live acceptance probe of Run-Gate
rev 63 against the CIU-managed `cgprofile-host-daemon`. It exercises a real
daemon request for both persistent-runner (`exec`) and bare-host lanes:

- the success lanes request 64 MiB/128 MiB and print a body marker only after
  Run-Gate receives the daemon's applied-placement readback;
- the refusal lanes request 64 GiB/64 GiB, above the gates-slice cap, and must
  exit as an infrastructure refusal without printing their body marker.

The probe runner must be a unique, disposable `tester-unified:local` container
under the configured `$CGROUP_PARENT_DEV_GATES` parent, with `--init`, no host
namespace flags, and a verified 3-CPU cap. The daemon is not started, stopped,
or replaced by this fixture. Run one lane at a time and remove only the exact
probe-runner container created for this probe. `damon = false` keeps these
short placement checks independent of host-wide DAMON_STAT mode; cgroup
placement/readback remains enabled.

Because the fixture lives below the repository, invoke the candidate script
with `sys.argv[0]` set to this directory's `run-gate.py` path (the launcher
uses that path to discover `run-gate.toml`). The controller records the
container identity, outcome, placement record, body-marker result, and daemon
session cleanup in the RG-55 P5 report.
