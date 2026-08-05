from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from bambu_utils.status import PrinterStatus


class RunRecord(BaseModel):
    id: int
    name: str
    file: str
    started_at: datetime
    ended_at: datetime | None
    final_state: str | None
    progress: int
    layer: int
    total_layers: int


@dataclass(slots=True)
class _ActiveRun:
    id: int
    identifier: str
    name: str
    file: str
    started_at: datetime
    progress: int = 0
    layer: int = 0
    total_layers: int = 0


class RunHistory:
    """Transition-only SQLite run journal to minimize SD-card writes."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()
        self._active = self._load_active()

    def observe(self, status: PrinterStatus) -> None:
        if not status.connected or status.run.state == "UNKNOWN":
            return
        run = status.run
        identifier = run.file or run.name
        now = status.updated_at or datetime.now(UTC)
        with self._lock:
            if run.active and identifier:
                if self._active is None:
                    self._active = self._begin(
                        identifier,
                        run.name or identifier,
                        run.file,
                        now,
                    )
                elif self._active.identifier != identifier:
                    self._finish("REPLACED", now)
                    self._active = self._begin(
                        identifier,
                        run.name or identifier,
                        run.file,
                        now,
                    )
                self._active.progress = run.progress
                self._active.layer = run.layer
                self._active.total_layers = run.total_layers
            elif not run.active and self._active is not None:
                self._active.progress = run.progress
                self._active.layer = run.layer
                self._active.total_layers = run.total_layers
                self._finish(run.state, now)

    def recent(self, limit: int = 20) -> list[RunRecord]:
        bounded_limit = min(100, max(1, limit))
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT id, name, file, started_at, ended_at, final_state,
                       progress, layer, total_layers
                FROM runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [
            RunRecord(
                id=int(row["id"]),
                name=str(row["name"]),
                file=str(row["file"]),
                started_at=datetime.fromisoformat(str(row["started_at"])),
                ended_at=(
                    datetime.fromisoformat(str(row["ended_at"]))
                    if row["ended_at"] is not None
                    else None
                ),
                final_state=(
                    str(row["final_state"])
                    if row["final_state"] is not None
                    else None
                ),
                progress=int(row["progress"]),
                layer=int(row["layer"]),
                total_layers=int(row["total_layers"]),
            )
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connect() as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    file TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    final_state TEXT,
                    progress INTEGER NOT NULL DEFAULT 0,
                    layer INTEGER NOT NULL DEFAULT 0,
                    total_layers INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _load_active(self) -> _ActiveRun | None:
        with self._connect() as database:
            row = database.execute(
                """
                SELECT id, name, file, started_at, progress, layer, total_layers
                FROM runs
                WHERE ended_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        filename = str(row["file"])
        name = str(row["name"])
        return _ActiveRun(
            id=int(row["id"]),
            identifier=filename or name,
            name=name,
            file=filename,
            started_at=datetime.fromisoformat(str(row["started_at"])),
            progress=int(row["progress"]),
            layer=int(row["layer"]),
            total_layers=int(row["total_layers"]),
        )

    def _begin(
        self, identifier: str, name: str, filename: str, started_at: datetime
    ) -> _ActiveRun:
        with self._connect() as database:
            cursor = database.execute(
                "INSERT INTO runs (name, file, started_at) VALUES (?, ?, ?)",
                (name, filename, started_at.isoformat()),
            )
            run_id = cursor.lastrowid
        if run_id is None:
            raise RuntimeError("SQLite did not return a run ID")
        return _ActiveRun(run_id, identifier, name, filename, started_at)

    def _finish(self, state: str, ended_at: datetime) -> None:
        active = self._active
        if active is None:
            return
        with self._connect() as database:
            database.execute(
                """
                UPDATE runs
                SET ended_at = ?, final_state = ?, progress = ?, layer = ?,
                    total_layers = ?
                WHERE id = ?
                """,
                (
                    ended_at.isoformat(),
                    state,
                    active.progress,
                    active.layer,
                    active.total_layers,
                    active.id,
                ),
            )
        self._active = None

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path, timeout=5)
        database.row_factory = sqlite3.Row
        return database
