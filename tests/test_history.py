from datetime import UTC, datetime, timedelta
from pathlib import Path

from bambu_utils.history import RunHistory
from bambu_utils.mqtt import JsonObject
from bambu_utils.status import PrinterStatus, normalize_status


def _snapshot(state: str, progress: int, moment: datetime) -> PrinterStatus:
    raw: JsonObject = {
        "print": {
            "gcode_state": state,
            "gcode_file": "cache/part.gcode.3mf",
            "subtask_name": "part",
            "mc_percent": progress,
            "layer_num": progress,
            "total_layer_num": 100,
        }
    }
    return normalize_status(raw, connected=True, model="A1", updated_at=moment)


def test_run_history_writes_only_one_completed_run(tmp_path: Path) -> None:
    history = RunHistory(tmp_path / "runs.sqlite3")
    started = datetime(2026, 8, 5, 12, tzinfo=UTC)

    history.observe(_snapshot("RUNNING", 5, started))
    history.observe(_snapshot("RUNNING", 65, started + timedelta(minutes=10)))
    history.observe(_snapshot("FINISH", 100, started + timedelta(minutes=20)))

    runs = history.recent()
    assert len(runs) == 1
    assert runs[0].name == "part"
    assert runs[0].final_state == "FINISH"
    assert runs[0].progress == 100
    assert runs[0].ended_at == started + timedelta(minutes=20)
