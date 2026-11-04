"""Command-line entry points.

``cgpctl`` and ``cgpd`` are the console scripts of the cgprofile wheel
(CGPCTL-SPEC.md). In package 1 they are thin stubs over ``legacy`` (the 1.x
``cgprofile.py``); packages 2 and 3 replace them with the cli-extended
clients and delete ``legacy``.
"""
