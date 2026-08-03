import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bambu_utils.cli import _parser  # pyright: ignore[reportPrivateUsage]


def test_dotenv_supplies_connection_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "BAMBU_HOST=file-host\n"
        "BAMBU_SERIAL=file-serial\n"
        "BAMBU_ACCESS_CODE=file-code\n"
        "BAMBU_TIMEOUT=20\n"
    )
    monkeypatch.chdir(tmp_path)

    with patch.dict(os.environ, {}, clear=True):
        args = _parser().parse_args(["status"])

    assert args.host == "file-host"
    assert args.serial == "file-serial"
    assert args.access_code == "file-code"
    assert args.timeout == 20.0


def test_flags_and_shell_environment_override_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "BAMBU_HOST=file-host\nBAMBU_SERIAL=file-serial\n"
    )
    monkeypatch.chdir(tmp_path)

    with patch.dict(os.environ, {"BAMBU_HOST": "shell-host"}, clear=True):
        args = _parser().parse_args(
            ["--host", "flag-host", "--serial", "flag-serial", "status"]
        )

    assert args.host == "flag-host"
    assert args.serial == "flag-serial"
