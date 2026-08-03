from __future__ import annotations

import ftplib
import re
import socket
import ssl
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from bambu_utils.config import PrinterConfig

_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9._-]+$")


def normalize_remote_path(path: str, *, allow_root: bool = False) -> str:
    """Return a safe path relative to the printer's FTPS root."""

    normalized = path.lstrip("/")
    if not normalized:
        if allow_root:
            return "."
        raise ValueError("remote path must not be empty")

    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} or not _SAFE_PATH_PART.fullmatch(part) for part in parts):
        raise ValueError(
            "remote paths may contain only letters, numbers, '.', '_', '-', and '/'"
        )
    return "/".join(parts)


def default_remote_path(local_path: Path) -> str:
    """Choose a cache path with a printer-safe ASCII filename."""

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", local_path.name).strip("._")
    if not safe_name:
        raise ValueError("local filename has no usable characters")
    return f"cache/{safe_name}"


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    """Implicit FTPS with the TLS session reuse required by Bambu printers."""

    def connect(
        self,
        host: str = "",
        port: int = 0,
        timeout: float = -999.0,
        source_address: tuple[str, int] | None = None,
    ) -> str:
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999.0:
            self.timeout = timeout
        if self.timeout == 0:
            raise ValueError("Non-blocking socket (timeout=0) is not supported")
        if source_address is not None:
            self.source_address = source_address

        raw_socket = socket.create_connection(
            (self.host, self.port), self.timeout, source_address=self.source_address
        )
        self.af = raw_socket.family
        self.sock = self.context.wrap_socket(raw_socket, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(
        self, cmd: str, rest: int | str | None = None
    ) -> tuple[socket.socket, int | None]:
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        control_socket = self.sock
        if not isinstance(control_socket, ssl.SSLSocket):
            raise ConnectionError("FTPS control connection is not open")
        data_socket = self.context.wrap_socket(
            conn,
            server_hostname=self.host,
            session=control_socket.session,
        )
        return data_socket, size


class FileTransferClient:
    """Read and write files on a Bambu printer's SD-card FTPS server."""

    def __init__(self, config: PrinterConfig) -> None:
        self._config = config

    @contextmanager
    def _session(self) -> Generator[ImplicitFTP_TLS]:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        ftp = ImplicitFTP_TLS(context=context, timeout=self._config.timeout)
        try:
            ftp.connect(self._config.host, self._config.ftps_port)
            ftp.login("bblp", self._config.access_code)
            ftp.prot_p()
            yield ftp
        finally:
            try:
                ftp.quit()
            except (OSError, EOFError, ftplib.Error):
                ftp.close()

    def upload(
        self,
        local_path: Path,
        remote_path: str,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        remote = normalize_remote_path(remote_path)
        with local_path.open("rb") as source, self._session() as ftp:
            self._store(ftp, cast(BinaryIO, source), remote, progress)

    @staticmethod
    def _store(
        ftp: ImplicitFTP_TLS,
        source: BinaryIO,
        remote_path: str,
        progress: Callable[[int], None] | None,
    ) -> None:
        ftp.voidcmd("TYPE I")
        with ftp.transfercmd(f"STOR {remote_path}") as connection:
            while block := source.read(128 * 1024):
                connection.sendall(block)
                if progress:
                    progress(len(block))

            # Bambu's server does not reliably complete TLS close-notify on its
            # data channel. Closing the data socket after the final byte avoids
            # the resulting timeout while the control channel receives 226.
            if isinstance(connection, ssl.SSLSocket):
                connection.shutdown(socket.SHUT_RDWR)
        ftp.voidresp()

    def download(self, remote_path: str, local_path: Path) -> None:
        remote = normalize_remote_path(remote_path)
        with local_path.open("wb") as destination, self._session() as ftp:
            ftp.retrbinary(f"RETR {remote}", destination.write)

    def list(self, remote_path: str = "/") -> list[str]:
        remote = normalize_remote_path(remote_path, allow_root=True)
        with self._session() as ftp:
            return sorted(ftp.nlst(remote))

    def delete(self, remote_path: str) -> None:
        remote = normalize_remote_path(remote_path)
        with self._session() as ftp:
            ftp.delete(remote)
