from pathlib import Path

from fastapi.testclient import TestClient

from bambu_utils.config import PrinterConfig
from bambu_utils.monitor import PrinterMonitor
from bambu_utils.web import DashboardSettings, create_app


def test_dashboard_serves_normalized_status(tmp_path: Path) -> None:
    config = PrinterConfig(
        host="printer",
        access_code="code",
        serial="serial",
        printer_model="A1",
    )
    monitor = PrinterMonitor(config)
    monitor.ingest(
        {
            "print": {
                "gcode_state": "RUNNING",
                "subtask_name": "part",
                "mc_percent": 37,
            }
        }
    )
    settings = DashboardSettings(
        printer=config,
        data_dir=tmp_path,
        static_dir=tmp_path / "missing-static",
    )
    app = create_app(settings, monitor=monitor, start_services=False)

    with TestClient(app) as client:
        response = client.get("/api/status")
        frontend = client.get("/")
        rejected_control = client.post("/api/control/pause")
        missing_api = client.get("/api/missing")

    assert response.status_code == 200
    assert response.json()["run"]["progress"] == 37
    assert frontend.status_code == 503
    assert rejected_control.status_code == 403
    assert missing_api.status_code == 404
