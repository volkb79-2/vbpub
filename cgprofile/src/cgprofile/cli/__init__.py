"""Command-line entry points.

``cgpctl`` and ``cgpd`` are the console scripts of the cgprofile wheel
(CGPCTL-SPEC.md). ``cgpctl`` is the cli-extended client; ``cgpd`` is still a
thin stub over ``legacy serve`` (collector child, daemon entry and report
subprocess) until package 3 replaces it and deletes ``legacy``.
"""
