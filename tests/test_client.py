import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from bambu_utils.client import BambuPrinter, PrintOptions, PrintSubmission
from bambu_utils.config import PrinterConfig
from bambu_utils.ftps import FileTransferClient
from bambu_utils.mqtt import MqttClient


def test_incompatible_slice_is_rejected_before_upload(tmp_path: Path) -> None:
    project = tmp_path / "p1s.gcode.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {
                    "printer_model": "Bambu Lab P1S",
                    "nozzle_diameter": ["0.4"],
                    "nozzle_type": ["stainless_steel"],
                }
            ),
        )
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><metadata key="printer_model_id" value="C12"/>'
            '<metadata key="nozzle_diameters" value="0.4"/></config>',
        )
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; printer_model = Bambu Lab P1S\n"
            "; nozzle_diameter = 0.4\n"
            "; nozzle_type = stainless_steel\nG28\n",
        )

    printer = BambuPrinter(
        PrinterConfig(
            host="printer",
            access_code="code",
            serial="03900A000000000",
            printer_model="A1",
        )
    )
    status = {
        "print": {"nozzle_diameter": "0.4", "nozzle_type": "stainless_steel"}
    }
    with (
        patch.object(MqttClient, "status", return_value=status),
        patch.object(FileTransferClient, "upload") as upload,
        pytest.raises(ValueError, match="slice targets Bambu Lab P1S"),
    ):
        printer.send(project)

    upload.assert_not_called()


def test_auto_ams_mapping_is_applied_to_the_print_command(tmp_path: Path) -> None:
    project = tmp_path / "a1.gcode.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {
                    "printer_model": "Bambu Lab A1",
                    "nozzle_diameter": ["0.4"],
                    "nozzle_type": ["stainless_steel"],
                }
            ),
        )
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><plate><metadata key="index" value="1"/>'
            '<metadata key="printer_model_id" value="N2S"/>'
            '<metadata key="nozzle_diameters" value="0.4"/>'
            '<filament id="1" tray_info_idx="GFA00" type="PLA" '
            'color="#FFFFFF" used_for_object="true" '
            'used_for_support="false"/></plate></config>',
        )
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; printer_model = Bambu Lab A1\n"
            "; nozzle_diameter = 0.4\n"
            "; nozzle_type = stainless_steel\nG28\n",
        )

    printer = BambuPrinter(
        PrinterConfig(
            host="printer",
            access_code="code",
            serial="03900A000000000",
            printer_model="A1",
        )
    )
    status = {
        "print": {
            "nozzle_diameter": "0.4",
            "nozzle_type": "stainless_steel",
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_info_idx": "GFA00",
                                "tray_type": "PLA",
                                "tray_color": "FFFFFFFF",
                            }
                        ],
                    }
                ]
            },
        }
    }
    with (
        patch.object(MqttClient, "status", return_value=status),
        patch.object(FileTransferClient, "upload"),
        patch.object(MqttClient, "request") as request,
        patch.object(MqttClient, "wait_for_print_start"),
    ):
        submission = printer.send(project, options=PrintOptions(ams_slots="auto"))

    assert submission == PrintSubmission("cache/a1.gcode.3mf", (0,))
    payload = request.call_args.args[0]
    assert payload["print"]["use_ams"] is True
    assert payload["print"]["ams_mapping"] == [0]
