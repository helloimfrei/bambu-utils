import hashlib
from pathlib import Path

import pytest

from bambu_utils.client import (
    PrintOptions,
    gcode_file_command,
    print_control_command,
    project_file_command,
)


def test_print_control_command() -> None:
    assert print_control_command("8", "pause") == {
        "print": {"sequence_id": "8", "command": "pause", "param": ""}
    }


def test_print_control_rejects_unknown_command() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        print_control_command("1", "explode")


def test_gcode_file_command_uses_remote_path() -> None:
    assert gcode_file_command("2", "cache/test.gcode") == {
        "print": {
            "sequence_id": "2",
            "command": "gcode_file",
            "param": "cache/test.gcode",
        }
    }


def test_project_file_command_for_external_spool(tmp_path: Path) -> None:
    project = tmp_path / "test.gcode.3mf"
    project.write_bytes(b"printer-ready")

    payload = project_file_command(
        sequence="3",
        serial="00M000000000000",
        local_path=project,
        remote_path="cache/test.gcode.3mf",
        plate_path="Metadata/plate_1.gcode",
        options=PrintOptions(),
    )

    assert payload["print"] == {
        "sequence_id": "3",
        "command": "project_file",
        "param": "Metadata/plate_1.gcode",
        "project_id": "0",
        "profile_id": "0",
        "task_id": "0",
        "subtask_id": "0",
        "subtask_name": "test",
        "file": "cache/test.gcode.3mf",
        "url": "ftp:///cache/test.gcode.3mf",
        "md5": hashlib.md5(b"printer-ready", usedforsecurity=False).hexdigest(),
        "bed_type": "auto",
        "bed_leveling": True,
        "flow_cali": True,
        "vibration_cali": True,
        "layer_inspect": True,
        "timelapse": False,
        "use_ams": False,
        "ams_mapping": [-1],
        "ams_mapping2": [{"ams_id": 255, "slot_id": 0}],
    }


def test_project_file_command_maps_absolute_ams_trays(tmp_path: Path) -> None:
    project = tmp_path / "multi.3mf"
    project.write_bytes(b"data")

    payload = project_file_command(
        sequence="4",
        serial="00M000000000000",
        local_path=project,
        remote_path="cache/multi.3mf",
        plate_path="Metadata/plate_2.gcode",
        options=PrintOptions(plate=2, ams_slots=(0, 5, -1, 128)),
    )
    detail = payload["print"]

    assert isinstance(detail, dict)
    assert detail["use_ams"] is True
    assert detail["ams_mapping"] == [0, 5, -1, 128]
    assert detail["ams_mapping2"] == [
        {"ams_id": 0, "slot_id": 0},
        {"ams_id": 1, "slot_id": 1},
        {"ams_id": 255, "slot_id": 255},
        {"ams_id": 128, "slot_id": 0},
    ]


def test_project_file_command_uses_sdcard_url_for_a1(tmp_path: Path) -> None:
    project = tmp_path / "a1.gcode.3mf"
    project.write_bytes(b"data")

    payload = project_file_command(
        sequence="5",
        serial="03900A000000000",
        local_path=project,
        remote_path="cache/a1.gcode.3mf",
        plate_path="Metadata/plate_1.gcode",
        options=PrintOptions(),
    )
    detail = payload["print"]

    assert isinstance(detail, dict)
    assert detail["url"] == "file:///sdcard/cache/a1.gcode.3mf"
