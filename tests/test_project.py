import zipfile
from pathlib import Path

import pytest

from bambu_utils.project import sliced_3mf_plates


def test_sliced_3mf_plates_finds_embedded_gcode(tmp_path: Path) -> None:
    project = tmp_path / "part.gcode.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/plate_2.gcode", "G28\n")
        archive.writestr("Metadata/plate_1.gcode", "G28\n")

    assert sliced_3mf_plates(project) == {
        1: "Metadata/plate_1.gcode",
        2: "Metadata/plate_2.gcode",
    }


def test_sliced_3mf_plates_rejects_unsliced_project(tmp_path: Path) -> None:
    project = tmp_path / "model.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model />")

    with pytest.raises(ValueError, match="is not sliced"):
        sliced_3mf_plates(project)
