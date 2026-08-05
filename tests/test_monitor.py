from bambu_utils.config import PrinterConfig
from bambu_utils.monitor import PrinterMonitor


def test_monitor_merges_delta_status_updates() -> None:
    monitor = PrinterMonitor(
        PrinterConfig(
            host="printer",
            access_code="code",
            serial="serial",
            printer_model="A1",
        )
    )
    monitor.ingest(
        {
            "print": {
                "gcode_state": "RUNNING",
                "subtask_name": "part",
                "mc_percent": 10,
                "layer_num": 2,
                "total_layer_num": 20,
            }
        }
    )
    version, first = monitor.snapshot()
    monitor.ingest({"print": {"mc_percent": 15, "layer_num": 3}})
    next_version, second = monitor.snapshot()

    assert next_version > version
    assert first.run.progress == 10
    assert second.run.progress == 15
    assert second.run.name == "part"
    assert second.run.total_layers == 20
