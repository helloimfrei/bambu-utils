from datetime import UTC, datetime

from bambu_utils.mqtt import JsonObject
from bambu_utils.status import normalize_status


def _raw_status(state: str = "RUNNING", progress: int = 42) -> JsonObject:
    return {
        "print": {
            "gcode_state": state,
            "gcode_file": "cache/part.gcode.3mf",
            "subtask_name": "part",
            "mc_percent": progress,
            "mc_remaining_time": 17,
            "layer_num": 23,
            "total_layer_num": 100,
            "nozzle_temper": 220.2,
            "nozzle_target_temper": 220,
            "bed_temper": 60.1,
            "bed_target_temper": 60,
            "chamber_temper": 5,
            "cooling_fan_speed": "15",
            "big_fan1_speed": "15",
            "wifi_signal": "-61dBm",
            "sdcard": True,
            "nozzle_diameter": "0.4",
            "nozzle_type": "stainless_steel",
            "ipcam": {"ipcam_dev": "1", "resolution": "1080p"},
            "lights_report": [{"node": "chamber_light", "mode": "on"}],
            "ams": {
                "tray_now": "1",
                "tray_tar": "1",
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "1",
                                "tray_type": "PLA",
                                "tray_sub_brands": "PLA Basic",
                                "tray_color": "000000FF",
                                "tray_info_idx": "GFA00",
                                "remain": 80,
                                "k": 0.02,
                            }
                        ],
                    }
                ],
            },
        }
    }


def test_normalize_status_builds_a1_dashboard_snapshot() -> None:
    snapshot = normalize_status(
        _raw_status(),
        connected=True,
        model="A1",
        updated_at=datetime(2026, 8, 5, tzinfo=UTC),
    )

    assert snapshot.run.active is True
    assert snapshot.run.progress == 42
    assert snapshot.temperatures.nozzle.current == 220.2
    assert snapshot.temperatures.chamber is None
    assert snapshot.fans.part == 100
    assert snapshot.fans.auxiliary is None
    assert snapshot.device.wifi_signal_dbm == -61
    assert snapshot.device.camera_available is True
    assert snapshot.ams.trays[0].color == "#000000"
    assert snapshot.ams.trays[0].active is True


def test_normalize_status_marks_a_paused_job() -> None:
    snapshot = normalize_status(
        _raw_status("PAUSE", 61),
        connected=True,
        model="A1",
        updated_at=datetime.now(UTC),
    )

    assert snapshot.run.active is True
    assert snapshot.run.paused is True
    assert snapshot.run.progress == 61
