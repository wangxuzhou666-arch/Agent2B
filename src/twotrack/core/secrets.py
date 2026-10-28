"""Deployment secrets: generated on first start, never shipped as a default.

``load_or_create_secret`` creates the file atomically with mode 0600 (O_CREAT | O_EXCL, so a
concurrent start cannot race it) and refuses to use an existing file that other users can read
or that is too short: a secret that may have leaked must be rotated, not silently reused.
"""

import os
import secrets
import stat
from pathlib import Path

MIN_LENGTH = 32


class SecretFileError(Exception):
    pass


def _check_existing(path: Path) -> str:
    if os.name != "nt" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SecretFileError(f"secret file '{path}' is accessible by other users; "
                              "run chmod 600 on it (and rotate it if it may have leaked)")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < MIN_LENGTH:
        raise SecretFileError(f"secret file '{path}' holds fewer than {MIN_LENGTH} characters")
    return value


def load_or_create_secret(path: str | Path) -> str:
    target = Path(path)
    if target.exists():
        return _check_existing(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = secrets.token_urlsafe(48)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another process created it first: use theirs
        return _check_existing(target)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    return value
