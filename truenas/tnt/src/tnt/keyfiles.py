"""Explicit, private, non-overwriting storage for a newly created one-time API key."""
from __future__ import annotations

import os
from pathlib import Path

from .errors import Refused


def write_api_key_file(path: str, api_key: str) -> str:
    """Write one API key to a new absolute file with mode 0600; never overwrite an existing path."""
    if not isinstance(path, str) or not path.strip():
        raise Refused("api-key-file", "provide an absolute path for the API key file")
    target = Path(path.strip()).expanduser()
    if not target.is_absolute():
        raise Refused("api-key-file", "the API key file path must be absolute")
    if not isinstance(api_key, str) or not api_key:
        raise Refused("api-key-file", "the API key value is empty")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        raise Refused("api-key-file", f"refusing to overwrite existing API key file {target}") from None
    except OSError as exc:
        raise Refused("api-key-file", f"cannot create API key file {target}: {exc.strerror or type(exc).__name__}") from None
    try:
        os.fchmod(fd, 0o600)
        data = (api_key + "\n").encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    except OSError as exc:
        os.close(fd)
        try:
            target.unlink()
        except OSError:
            pass
        raise Refused("api-key-file", f"cannot finish writing API key file {target}: {exc.strerror or type(exc).__name__}") from None
    else:
        os.close(fd)
    return str(target)
