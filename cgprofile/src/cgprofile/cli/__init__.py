"""Command-line entry points.

``cgprofilectl`` and ``cgprofiled`` are the console scripts of the cgprofile wheel
(CGPCTL-SPEC.md). ``cgprofilectl`` is the cli-extended client; ``cgprofiled`` is still a
thin stub over ``legacy serve`` (collector child, daemon entry and report
subprocess) until package 3 replaces it and deletes ``legacy``.
"""
