# TEST fixture for cmru's installer `extensions` mechanism (second fragment).
# Registers `second`; shadows the template name `err` with a parameter, which is
# legal (a fragment's own scope) and must not trip the EXTENSION_API check.
def _second(args, token):
    def render(err):
        return _c("BLD", f"second:{err}")
    print(render("local-err"))


def _register_second(subparsers):
    subparsers.add_parser("second", help="Second fixture command")
    return {"second": _second}


_EXTENSIONS.append(_register_second)
