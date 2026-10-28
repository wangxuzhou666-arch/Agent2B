import os
import stat

import pytest

from twotrack.core.secrets import SecretFileError, load_or_create_secret


def test_secret_is_created_once_with_private_mode(tmp_path):
    path = tmp_path / "state" / "jwt.secret"
    first = load_or_create_secret(path)
    assert len(first) >= 32 and load_or_create_secret(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_shared_or_short_secret_file_is_refused(tmp_path):
    path = tmp_path / "jwt.secret"
    path.write_text("x" * 64)
    os.chmod(path, 0o644)
    with pytest.raises(SecretFileError, match="chmod 600"):
        load_or_create_secret(path)
    os.chmod(path, 0o600)
    path.write_text("short")
    with pytest.raises(SecretFileError, match="fewer than"):
        load_or_create_secret(path)
