from __future__ import annotations

import logging
import socket
import ssl
import struct
import threading
from collections.abc import Iterator

from bambu_utils.config import PrinterConfig

_LOGGER = logging.getLogger(__name__)
_CAMERA_PORT = 6000
_HEADER_SIZE = 16
_MAX_FRAME_SIZE = 20 * 1024 * 1024


class CameraStream:
    """Fan out one A1/P1 TLS JPEG stream to multiple HTTP viewers."""

    def __init__(self, config: PrinterConfig) -> None:
        self._config = config
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._shutdown = False
        self._viewers = 0
        self._sequence = 0
        self._frame: bytes | None = None
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        with self._condition:
            return self._error

    def iter_mjpeg(
        self, shutdown_event: threading.Event | None = None
    ) -> Iterator[bytes]:
        external_stop = shutdown_event or threading.Event()
        with self._condition:
            if self._shutdown or external_stop.is_set():
                return
            self._viewers += 1
            self._start_thread_locked()
            sequence = self._sequence
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: (
                            self._sequence != sequence
                            or self._shutdown
                            or external_stop.is_set()
                        ),
                        timeout=1,
                    )
                    if self._shutdown or external_stop.is_set():
                        return
                    if self._sequence == sequence or self._frame is None:
                        continue
                    sequence = self._sequence
                    frame = self._frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    + frame
                    + b"\r\n"
                )
        finally:
            with self._condition:
                self._viewers -= 1
                if self._viewers == 0:
                    self._stop.set()

    def close(self) -> None:
        with self._condition:
            self._shutdown = True
            self._stop.set()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=5)

    def _start_thread_locked(self) -> None:
        if self._thread is not None or self._shutdown:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="bambu-camera",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._stream()
                except (ConnectionError, OSError, RuntimeError, ssl.SSLError) as error:
                    if self._stop.is_set():
                        break
                    _LOGGER.warning("camera stream failed: %s", error)
                    with self._condition:
                        self._error = str(error)
                        self._condition.notify_all()
                    self._stop.wait(2)
        finally:
            with self._condition:
                self._thread = None
                if self._viewers > 0 and not self._shutdown:
                    self._start_thread_locked()

    def _stream(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(
            (self._config.host, _CAMERA_PORT),
            timeout=min(self._config.timeout, 5),
        ) as raw:
            with context.wrap_socket(raw, server_hostname=self._config.host) as camera:
                camera.settimeout(3)
                camera.sendall(camera_auth_packet(self._config.access_code))
                while not self._stop.is_set():
                    header = _receive_exact(camera, _HEADER_SIZE)
                    payload_size, _track, _flags, _reserved = struct.unpack(
                        "<IIII", header
                    )
                    if payload_size <= 0 or payload_size > _MAX_FRAME_SIZE:
                        raise RuntimeError(
                            f"camera reported invalid frame size {payload_size}"
                        )
                    frame = _receive_exact(camera, payload_size)
                    if not frame.startswith(b"\xff\xd8") or not frame.endswith(
                        b"\xff\xd9"
                    ):
                        raise RuntimeError("camera returned a malformed JPEG frame")
                    with self._condition:
                        self._frame = frame
                        self._sequence += 1
                        self._error = None
                        self._condition.notify_all()


def camera_auth_packet(access_code: str) -> bytes:
    username = b"bblp".ljust(32, b"\0")
    password = access_code.encode("ascii")[:32].ljust(32, b"\0")
    return struct.pack("<IIII", 0x40, 0x3000, 0, 0) + username + password


def _receive_exact(connection: ssl.SSLSocket, count: int) -> bytes:
    received = bytearray()
    while len(received) < count:
        chunk = connection.recv(count - len(received))
        if not chunk:
            raise ConnectionError("camera stream closed unexpectedly")
        received.extend(chunk)
    return bytes(received)
