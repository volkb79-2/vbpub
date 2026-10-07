# TEST fixture for cmru's installer `extensions` mechanism (not ciu's enroll).
# Registers one command, `hello`, whose handler reports the token it received.
import sys


def _hello(args, token):
    info(f"hello name={args.name} token={token}")
    print(f"exit-codes={EXIT_CONFIG},{EXIT_PREREQ}", file=sys.stdout)


def _register_hello(subparsers):
    parser = subparsers.add_parser("hello", help="Say hello (test fixture)")
    parser.add_argument("--name", default="world")
    return {"hello": _hello}


_EXTENSIONS.append(_register_hello)
