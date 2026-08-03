from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Self, cast

import pytest

from bambu_utils.ftps import (
    FileTransferClient,
    ImplicitFTP_TLS,
    default_remote_path,
    normalize_remote_path,
)


class _DataConnection:
    def __init__(self) -> None:
        self.blocks: list[bytes] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    def sendall(self, block: bytes) -> None:
        self.blocks.append(block)


class _FakeFTP:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.connection = _DataConnection()
        self.completed = False

    def voidcmd(self, command: str) -> None:
        self.commands.append(command)

    def transfercmd(self, command: str) -> _DataConnection:
        self.commands.append(command)
        return self.connection

    def voidresp(self) -> None:
        self.completed = True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("cache/part.gcode.3mf", "cache/part.gcode.3mf"),
        ("/cache/part.gcode", "cache/part.gcode"),
        ("/", "."),
    ],
)
def test_normalize_remote_path(value: str, expected: str) -> None:
    assert normalize_remote_path(value, allow_root=value == "/") == expected


@pytest.mark.parametrize(
    "value",
    ["../secret", "cache/a file.3mf", "cache/a\nDELE other", "cache/../other"],
)
def test_normalize_remote_path_rejects_unsafe_input(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_remote_path(value)


def test_default_remote_path_sanitizes_filename() -> None:
    assert default_remote_path(Path("my plate #1.gcode.3mf")) == (
        "cache/my_plate_1.gcode.3mf"
    )


def test_store_streams_the_file_and_waits_for_completion() -> None:
    ftp = _FakeFTP()
    progress: list[int] = []

    FileTransferClient._store(  # pyright: ignore[reportPrivateUsage]
        cast(ImplicitFTP_TLS, ftp),
        BytesIO(b"slice"),
        "cache/slice.gcode.3mf",
        progress.append,
    )

    assert ftp.commands == ["TYPE I", "STOR cache/slice.gcode.3mf"]
    assert ftp.connection.blocks == [b"slice"]
    assert progress == [5]
    assert ftp.completed is True
