from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Annotated, Literal

import uvicorn
from dotenv import dotenv_values
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bambu_utils.camera import CameraStream
from bambu_utils.client import BambuPrinter
from bambu_utils.config import PrinterConfig
from bambu_utils.history import RunHistory
from bambu_utils.monitor import PrinterMonitor


@dataclass(frozen=True, slots=True)
class DashboardSettings:
    printer: PrinterConfig
    listen_host: str = "127.0.0.1"
    listen_port: int = 8000
    data_dir: Path = Path("~/.local/share/bambu-utils")
    static_dir: Path = Path(__file__).with_name("static")

    @classmethod
    def from_environment(cls) -> DashboardSettings:
        file_values = dotenv_values(Path.cwd() / ".env")

        def setting(name: str, fallback: str | None = None) -> str | None:
            return os.environ.get(name) or file_values.get(name) or fallback

        host = _required_setting(setting("BAMBU_HOST"), "BAMBU_HOST")
        serial = _required_setting(setting("BAMBU_SERIAL"), "BAMBU_SERIAL")
        access_code = _required_setting(
            setting("BAMBU_ACCESS_CODE"), "BAMBU_ACCESS_CODE"
        )
        timeout = float(setting("BAMBU_TIMEOUT", "15") or "15")
        listen_port = int(setting("BAMBU_UI_PORT", "8000") or "8000")
        if not 1 <= listen_port <= 65535:
            raise ValueError("BAMBU_UI_PORT must be between 1 and 65535")
        return cls(
            printer=PrinterConfig(
                host=host,
                serial=serial,
                access_code=access_code,
                printer_model=setting("BAMBU_PRINTER_MODEL"),
                timeout=timeout,
            ),
            listen_host=setting("BAMBU_UI_HOST", "127.0.0.1") or "127.0.0.1",
            listen_port=listen_port,
            data_dir=Path(
                setting("BAMBU_DATA_DIR", "~/.local/share/bambu-utils")
                or "~/.local/share/bambu-utils"
            ).expanduser(),
        )


class ControlResult(BaseModel):
    command: str
    accepted: bool


class HealthResult(BaseModel):
    ok: bool
    printer_connected: bool
    camera_error: str | None


def create_app(
    settings: DashboardSettings,
    *,
    monitor: PrinterMonitor | None = None,
    camera: CameraStream | None = None,
    history: RunHistory | None = None,
    printer: BambuPrinter | None = None,
    shutdown_event: threading.Event | None = None,
    start_services: bool = True,
) -> FastAPI:
    stopping = shutdown_event or threading.Event()
    status_monitor = monitor or PrinterMonitor(settings.printer)
    camera_stream = camera or CameraStream(settings.printer)
    run_history = history or RunHistory(settings.data_dir / "runs.sqlite3")
    printer_client = printer or BambuPrinter(settings.printer)
    status_monitor.add_observer(run_history.observe)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        del app
        if start_services:
            status_monitor.start()
        try:
            yield
        finally:
            stopping.set()
            if start_services:
                status_monitor.stop()
            camera_stream.close()

    app = FastAPI(
        title="bambu-utils dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/api/status")
    async def _status() -> object:  # pyright: ignore[reportUnusedFunction]
        return status_monitor.snapshot()[1]

    @app.get("/api/events")
    async def _events(  # pyright: ignore[reportUnusedFunction]
        request: Request,
    ) -> StreamingResponse:
        async def stream() -> AsyncGenerator[str]:
            version = -1
            while not stopping.is_set() and not await request.is_disconnected():
                next_version, snapshot = await asyncio.to_thread(
                    status_monitor.wait_for_update, version, 1
                )
                if stopping.is_set():
                    return
                if next_version == version:
                    yield ": keep-alive\n\n"
                    continue
                version = next_version
                yield f"event: status\ndata: {snapshot.model_dump_json()}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/runs")
    async def _runs(  # pyright: ignore[reportUnusedFunction]
        limit: int = 20,
    ) -> object:
        return await asyncio.to_thread(run_history.recent, limit)

    @app.post("/api/control/{command}")
    async def _control(  # pyright: ignore[reportUnusedFunction]
        command: Literal["pause", "resume", "stop"],
        control_header: Annotated[
            str | None, Header(alias="X-Bambu-Control")
        ] = None,
    ) -> ControlResult:
        if control_header != "1":
            raise HTTPException(status_code=403, detail="control header is required")
        operation = getattr(printer_client, command)
        try:
            await asyncio.to_thread(operation)
        except (ConnectionError, OSError, RuntimeError, TimeoutError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return ControlResult(command=command, accepted=True)

    @app.get("/api/camera.mjpeg")
    async def _camera_feed() -> StreamingResponse:  # pyright: ignore[reportUnusedFunction]
        model = (settings.printer.printer_model or "").strip().upper()
        if model not in {"A1", "A1 MINI", "P1S", "P1P"}:
            raise HTTPException(
                status_code=501,
                detail="local camera streaming is currently implemented for A1/P1 printers",
            )
        return StreamingResponse(
            camera_stream.iter_mjpeg(stopping),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/health")
    async def _health() -> HealthResult:  # pyright: ignore[reportUnusedFunction]
        snapshot = status_monitor.snapshot()[1]
        return HealthResult(
            ok=True,
            printer_connected=snapshot.connected,
            camera_error=camera_stream.error,
        )

    assets = settings.static_dir / "assets"
    index = settings.static_dir / "index.html"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], response_model=None)
    async def _frontend(  # pyright: ignore[reportUnusedFunction]
        full_path: str,
    ) -> FileResponse | HTMLResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>bambu-utils dashboard</h1>"
            "<p>Frontend assets are missing. Run <code>npm --prefix web run build</code>.</p>",
            status_code=503,
        )

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = DashboardSettings.from_environment()
    stopping = threading.Event()
    config = uvicorn.Config(
        create_app(settings, shutdown_event=stopping),
        host=settings.listen_host,
        port=settings.listen_port,
        workers=1,
        access_log=False,
        timeout_graceful_shutdown=3,
    )
    try:
        _DashboardServer(config, stopping).run()
    except KeyboardInterrupt:
        pass


class _DashboardServer(uvicorn.Server):
    def __init__(
        self, config: uvicorn.Config, shutdown_event: threading.Event
    ) -> None:
        super().__init__(config)
        self._shutdown_event = shutdown_event

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._shutdown_event.set()
        super().handle_exit(sig, frame)


def _required_setting(value: str | None, name: str) -> str:
    if value:
        return value
    raise ValueError(f"{name} is required in the environment or .env")
