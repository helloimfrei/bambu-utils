import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from bambu_utils.client import BambuPrinter
from bambu_utils.config import PrinterConfig
from bambu_utils.ftps import FileTransferClient
from bambu_utils.mqtt import MqttClient


def test_incompatible_slice_is_rejected_before_upload(tmp_path: Path) -> None:
    project = tmp_path / "p1s.gcode.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {"printer_model": "Bambu Lab P1S", "nozzle_diameter": ["0.4"]}
            ),
        )
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><metadata key="printer_model_id" value="C12"/>'
            '<metadata key="nozzle_diameters" value="0.4"/></config>',
        )
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; printer_model = Bambu Lab P1S\n; nozzle_diameter = 0.4\nG28\n",
        )

    printer = BambuPrinter(
        PrinterConfig(
            host="printer",
            access_code="code",
            serial="03900A000000000",
            printer_model="A1",
            nozzle_diameter=0.4,
        )
    )
    status = {"print": {"nozzle_diameter": "0.4"}}
    with (
        patch.object(MqttClient, "status", return_value=status),
        patch.object(FileTransferClient, "upload") as upload,
        pytest.raises(ValueError, match="slice targets Bambu Lab P1S"),
    ):
        printer.send(project)

    upload.assert_not_called()
