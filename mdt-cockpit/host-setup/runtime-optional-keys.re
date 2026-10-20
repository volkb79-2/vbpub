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
# still validated. Render-time keys are NOT here: an absent line drops a rendered
# directive and can leave a slice unbounded, so those still refuse.
#
# One extended regular expression (POSIX ERE / Python re), anchored, on one line.
^(DEV_IO_(R|W)(BPS|IOPS)_MAX|DEV_(INTERACTIVE|BACKGROUND|GATES|BUILDKITD)_IO_(R|W)(BPS|IOPS)_MAX|IO_BASELINE_ROTATIONAL_FLAG)$
