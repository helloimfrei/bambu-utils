import json
import zipfile
from pathlib import Path

import pytest

from bambu_utils.project import (
    SliceMetadata,
    gcode_metadata,
    sliced_3mf_metadata,
    sliced_3mf_plates,
    validate_slice_for_printer,
)


def _write_sliced_3mf(
    path: Path,
    *,
    model: str = "Bambu Lab A1",
    model_id: str = "N2S",
    project_nozzle: str = "0.4",
    slice_nozzle: str = "0.4",
    gcode_model: str | None = None,
    gcode_nozzle: str = "0.4",
    project_nozzle_type: str = "stainless_steel",
    gcode_nozzle_type: str = "stainless_steel",
) -> None:
    project_settings = {
        "printer_model": model,
        "nozzle_diameter": [project_nozzle],
        "nozzle_type": [project_nozzle_type],
    }
    slice_info = (
        '<config><plate><metadata key="printer_model_id" '
        f'value="{model_id}"/><metadata key="nozzle_diameters" '
        f'value="{slice_nozzle}"/></plate></config>'
    )
    gcode = (
        f"; printer_model = {gcode_model or model}\n"
        f"; nozzle_diameter = {gcode_nozzle}\n"
        f"; nozzle_type = {gcode_nozzle_type}\n"
        "G28\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(project_settings))
        archive.writestr("Metadata/slice_info.config", slice_info)
        archive.writestr("Metadata/plate_1.gcode", gcode)


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


def test_sliced_3mf_metadata_cross_checks_three_sources(tmp_path: Path) -> None:
    project = tmp_path / "a1.gcode.3mf"
    _write_sliced_3mf(project)

    assert sliced_3mf_metadata(project, "Metadata/plate_1.gcode") == SliceMetadata(
        printer_model="Bambu Lab A1",
        printer_model_id="N2S",
        nozzle_diameters=(0.4,),
        nozzle_types=("stainless_steel",),
    )


def test_sliced_3mf_metadata_rejects_conflicting_gcode_model(
    tmp_path: Path,
) -> None:
    project = tmp_path / "conflict.gcode.3mf"
    _write_sliced_3mf(project, gcode_model="Bambu Lab P1S")

    with pytest.raises(ValueError, match="conflicting printer metadata"):
        sliced_3mf_metadata(project, "Metadata/plate_1.gcode")


def test_sliced_3mf_metadata_rejects_missing_compatibility_data(
    tmp_path: Path,
) -> None:
    project = tmp_path / "missing.gcode.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G28\n")

    with pytest.raises(ValueError, match="lacks required compatibility metadata"):
        sliced_3mf_metadata(project, "Metadata/plate_1.gcode")


def test_validate_slice_rejects_p1s_gcode_for_a1() -> None:
    metadata = SliceMetadata("Bambu Lab P1S", "C12", (0.4,), ("stainless_steel",))

    with pytest.raises(ValueError, match="slice targets Bambu Lab P1S"):
        validate_slice_for_printer(
            metadata,
            configured_printer_model="A1",
            printer_serial="03900A000000000",
            reported_nozzle_diameter=0.4,
            reported_nozzle_type="stainless_steel",
        )


def test_validate_slice_rejects_config_that_conflicts_with_serial() -> None:
    metadata = SliceMetadata("Bambu Lab P1S", "C12", (0.4,), ("stainless_steel",))

    with pytest.raises(ValueError, match="conflicts with serial family"):
        validate_slice_for_printer(
            metadata,
            configured_printer_model="P1S",
            printer_serial="03900A000000000",
            reported_nozzle_diameter=0.4,
            reported_nozzle_type="stainless_steel",
        )


def test_validate_slice_rejects_nozzle_mismatch() -> None:
    metadata = SliceMetadata("Bambu Lab A1", "N2S", (0.6,), ("stainless_steel",))

    with pytest.raises(ValueError, match="slice targets a 0.6 mm nozzle"):
        validate_slice_for_printer(
            metadata,
            configured_printer_model="A1",
            printer_serial="03900A000000000",
            reported_nozzle_diameter=0.4,
            reported_nozzle_type="stainless_steel",
        )


def test_validate_slice_rejects_nozzle_type_mismatch() -> None:
    metadata = SliceMetadata("Bambu Lab A1", "N2S", (0.4,), ("hardened_steel",))

    with pytest.raises(ValueError, match="slice targets nozzle type 'hardened_steel'"):
        validate_slice_for_printer(
            metadata,
            configured_printer_model="A1",
            printer_serial="03900A000000000",
            reported_nozzle_diameter=0.4,
            reported_nozzle_type="stainless_steel",
        )


@pytest.mark.parametrize(
    ("setting", "model", "model_id", "serial"),
    [
        ("X1C", "Bambu Lab X1 Carbon", "BL-P001", "00M00A000000000"),
        ("X1E", "Bambu Lab X1E", "C13", "03W00A000000000"),
        ("P1S", "Bambu Lab P1S", "C12", "01P00A000000000"),
        ("P1P", "Bambu Lab P1P", "C11", "01S00A000000000"),
        ("A1 Mini", "Bambu Lab A1 mini", "N1", "03000A000000000"),
        ("A1", "Bambu Lab A1", "N2S", "03900A000000000"),
    ],
)
def test_validate_slice_supports_registered_printer_families(
    setting: str, model: str, model_id: str, serial: str
) -> None:
    validate_slice_for_printer(
        SliceMetadata(model, model_id, (0.4,), ("stainless_steel",)),
        configured_printer_model=setting,
        printer_serial=serial,
        reported_nozzle_diameter=0.4,
        reported_nozzle_type="stainless_steel",
    )


def test_gcode_metadata_requires_slicer_target_comments(tmp_path: Path) -> None:
    gcode = tmp_path / "manual.gcode"
    gcode.write_text("G28\n")

    with pytest.raises(ValueError, match="has no printer_model"):
        gcode_metadata(gcode)
