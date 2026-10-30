# The ONE definition of the RUNTIME-OPTIONAL host-setup.env keys.
#
# Read by BOTH the strict Python validator (mdt-host-setup-wizard.py, run first by
# install.sh --validate-config) and the shell (scripts/mdt-container-io-caps.lib.sh,
# _mdt_missing_declared_keys). Do not copy this expression anywhere: MDT-HS-02 found
# the shell exemption never ran because the Python validator, with its own idea of
# "missing", refused first.
#
# An absent line for one of these keys means "not set" (a policy default), never
# "silently unbounded": the 20 operator IO pins (dev.slice and per tier) and the
# rotational-flag override are read at runtime by the reconcile. When PRESENT they are
# still validated. The four IO_WRITEBACK_* / IO_ROOT_QUEUE_* keys (MDT-HS-05) are here too: absent
# means `kernel` (the kernel's own value is left untouched), never an unbounded cap. IO_ROOT_SCHEDULER, IO_COST_MODEL and IO_COST_QOS (MDT-HS-07) are here too: absent means NOT MANAGED (the legacy BFQ rule is kept; no io.cost line is written), never a silent change of the scheduler or of io.cost. Render-time keys are NOT here: an absent line drops a rendered
# directive and can leave a slice unbounded, so those still refuse.
#
# One extended regular expression (POSIX ERE / Python re), anchored, on one line.
^(DEV_IO_(R|W)(BPS|IOPS)_MAX|DEV_(INTERACTIVE|BACKGROUND|GATES|BUILDKITD)_IO_(R|W)(BPS|IOPS)_MAX|IO_BASELINE_ROTATIONAL_FLAG|IO_WRITEBACK_DIRTY_BYTES|IO_WRITEBACK_DIRTY_BACKGROUND_BYTES|IO_ROOT_QUEUE_NR_REQUESTS|IO_ROOT_QUEUE_MAX_SECTORS_KB|IO_ROOT_SCHEDULER|IO_COST_MODEL|IO_COST_QOS)$
