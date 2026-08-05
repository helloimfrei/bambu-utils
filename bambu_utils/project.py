from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

_PLATE_GCODE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
_GCODE_SETTING = re.compile(
    r"^; (?P<key>printer_model|nozzle_diameter|nozzle_type)\s*=\s*(?P<value>.+?)\s*$"
)


@dataclass(frozen=True, slots=True)
class SliceMetadata:
    printer_model: str
    printer_model_id: str | None
    nozzle_diameters: tuple[float, ...]
    nozzle_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SliceFilament:
    id: int
    profile_id: str | None
    filament_type: str
    color: str


@dataclass(frozen=True, slots=True)
class PrinterTarget:
    setting: str
    name: str
    model_id: str
    serial_prefixes: tuple[str, ...]


_TARGETS = (
    PrinterTarget("X1C", "Bambu Lab X1 Carbon", "BL-P001", ("00M",)),
    PrinterTarget("X1E", "Bambu Lab X1E", "C13", ("03W",)),
    PrinterTarget("P1S", "Bambu Lab P1S", "C12", ("01P",)),
    PrinterTarget("P1P", "Bambu Lab P1P", "C11", ("01S",)),
    PrinterTarget("A1 Mini", "Bambu Lab A1 mini", "N1", ("030",)),
    PrinterTarget("A1", "Bambu Lab A1", "N2S", ("039",)),
)
_TARGET_ALIASES = {
    "x1c": _TARGETS[0],
    "x1 carbon": _TARGETS[0],
    "bambu lab x1 carbon": _TARGETS[0],
    "x1e": _TARGETS[1],
    "bambu lab x1e": _TARGETS[1],
    "p1s": _TARGETS[2],
    "bambu lab p1s": _TARGETS[2],
    "p1p": _TARGETS[3],
    "bambu lab p1p": _TARGETS[3],
    "a1 mini": _TARGETS[4],
    "a1mini": _TARGETS[4],
    "bambu lab a1 mini": _TARGETS[4],
    "a1": _TARGETS[5],
    "bambu lab a1": _TARGETS[5],
}
_SERIAL_TARGETS = {
    prefix: target for target in _TARGETS for prefix in target.serial_prefixes
}


def file_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def sliced_3mf_plates(path: Path) -> dict[int, str]:
    """Return plate numbers and embedded G-code paths from a sliced 3MF."""

    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path} is not a valid 3MF archive")

    plates: dict[int, str] = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            match = _PLATE_GCODE.fullmatch(name)
            if match:
                plates[int(match.group(1))] = name

    if not plates:
        raise ValueError(
            f"{path} is not sliced: no Metadata/plate_N.gcode entry was found"
        )
    return plates


def sliced_3mf_metadata(path: Path, plate_path: str) -> SliceMetadata:
    """Read and cross-check compatibility metadata from a sliced 3MF."""

    try:
        with zipfile.ZipFile(path) as archive:
            project_settings = _json_object(
                archive.read("Metadata/project_settings.config"),
                "Metadata/project_settings.config",
            )
            slice_info = ElementTree.fromstring(
                archive.read("Metadata/slice_info.config")
            )
            gcode = archive.read(plate_path)
    except KeyError as error:
        missing = str(error).strip("'")
        raise ValueError(
            f"{path} lacks required compatibility metadata {missing}; refusing to print"
        ) from error
    except (ElementTree.ParseError, json.JSONDecodeError) as error:
        raise ValueError(
            f"{path} has invalid compatibility metadata; refusing to print"
        ) from error

    project_model = _required_string(
        project_settings.get("printer_model"), "printer_model", path
    )
    project_nozzles = _nozzle_values(
        project_settings.get("nozzle_diameter"), "project settings", path
    )
    project_nozzle_types = _string_values(
        project_settings.get("nozzle_type"), "nozzle type in project settings", path
    )

    plate = _slice_plate(slice_info, plate_path, path)
    slice_values = {
        element.get("key"): element.get("value")
        for element in plate.iter("metadata")
    }
    model_id = _required_string(
        slice_values.get("printer_model_id"), "printer_model_id", path
    )
    slice_nozzles = _nozzle_values(
        slice_values.get("nozzle_diameters"), "slice info", path
    )

    gcode_values = _gcode_settings(gcode)
    gcode_model = _required_string(
        gcode_values.get("printer_model"), "G-code printer_model", path
    )
    gcode_nozzles = _nozzle_values(
        gcode_values.get("nozzle_diameter"), "G-code", path
    )
    gcode_nozzle_types = _string_values(
        gcode_values.get("nozzle_type"), "G-code nozzle type", path
    )

    if project_model != gcode_model:
        raise ValueError(
            f"{path} has conflicting printer metadata ({project_model!r} vs "
            f"{gcode_model!r}); refusing to print"
        )
    if project_nozzles != slice_nozzles or project_nozzles != gcode_nozzles:
        raise ValueError(
            f"{path} has conflicting nozzle metadata; refusing to print"
        )
    if project_nozzle_types != gcode_nozzle_types:
        raise ValueError(
            f"{path} has conflicting nozzle type metadata; refusing to print"
        )
    return SliceMetadata(
        project_model, model_id, project_nozzles, project_nozzle_types
    )


def sliced_3mf_filaments(path: Path, plate_path: str) -> tuple[SliceFilament, ...]:
    """Read the filaments used by one sliced plate."""

    try:
        with zipfile.ZipFile(path) as archive:
            slice_info = ElementTree.fromstring(
                archive.read("Metadata/slice_info.config")
            )
    except KeyError as error:
        raise ValueError(
            f"{path} lacks Metadata/slice_info.config; cannot map AMS filaments"
        ) from error
    except ElementTree.ParseError as error:
        raise ValueError(
            f"{path} has invalid slice metadata; cannot map AMS filaments"
        ) from error

    plate = _slice_plate(slice_info, plate_path, path)
    filaments: list[SliceFilament] = []
    seen_ids: set[int] = set()
    for element in plate.iter("filament"):
        used_for_object = element.get("used_for_object")
        used_for_support = element.get("used_for_support")
        if used_for_object == "false" and used_for_support == "false":
            continue

        raw_id = element.get("id")
        try:
            filament_id = int(raw_id) if raw_id is not None else 0
        except ValueError as error:
            raise ValueError(
                f"{path} has an invalid filament ID; cannot map AMS filaments"
            ) from error
        if filament_id < 1 or filament_id in seen_ids:
            raise ValueError(
                f"{path} has invalid or duplicate filament ID {filament_id}; "
                "cannot map AMS filaments"
            )

        filament_type = element.get("type")
        color = element.get("color")
        if (
            not filament_type
            or not filament_type.strip()
            or not color
            or not color.strip()
        ):
            raise ValueError(
                f"{path} lacks type or color metadata for filament {filament_id}; "
                "cannot map AMS filaments"
            )
        raw_profile = element.get("tray_info_idx")
        profile = raw_profile.strip() if raw_profile and raw_profile.strip() else None
        seen_ids.add(filament_id)
        filaments.append(
            SliceFilament(
                id=filament_id,
                profile_id=profile,
                filament_type=filament_type.strip(),
                color=color.strip(),
            )
        )

    if not filaments:
        raise ValueError(f"{path} has no used filament metadata; cannot map AMS")
    return tuple(sorted(filaments, key=lambda filament: filament.id))


def gcode_metadata(path: Path) -> SliceMetadata:
    """Read compatibility metadata from a standalone slicer G-code file."""

    values = _gcode_settings(path.read_bytes())
    model = _required_string(values.get("printer_model"), "printer_model", path)
    nozzles = _nozzle_values(values.get("nozzle_diameter"), "G-code", path)
    nozzle_types = _string_values(
        values.get("nozzle_type"), "G-code nozzle type", path
    )
    return SliceMetadata(model, None, nozzles, nozzle_types)


def validate_slice_for_printer(
    metadata: SliceMetadata,
    *,
    configured_printer_model: str,
    printer_serial: str,
    reported_nozzle_diameter: float,
    reported_nozzle_type: str,
) -> None:
    """Fail closed unless a slice matches the connected printer and nozzle."""

    target = _TARGET_ALIASES.get(configured_printer_model.strip().lower())
    supported = ", ".join(item.setting for item in _TARGETS)
    if target is None:
        raise ValueError(
            f"configured printer model {configured_printer_model!r} is unsupported; "
            f"choose one of {supported}"
        )
    prefix = printer_serial[:3].upper()
    serial_target = _SERIAL_TARGETS.get(prefix)
    if serial_target is None:
        raise ValueError(
            f"printer serial family {prefix!r} is unsupported; refusing to print"
        )
    if serial_target != target:
        raise ValueError(
            f"BAMBU_PRINTER_MODEL={target.setting!r} conflicts with serial family "
            f"{prefix!r} ({serial_target.setting}); refusing to print"
        )
    if metadata.printer_model != target.name:
        raise ValueError(
            f"slice targets {metadata.printer_model}, but connected printer is "
            f"{target.name}; refusing to upload or print"
        )
    if metadata.printer_model_id not in {None, target.model_id}:
        raise ValueError(
            f"slice model ID {metadata.printer_model_id} does not match connected "
            f"printer model ID {target.model_id}; refusing to upload or print"
        )
    if len(metadata.nozzle_diameters) != 1:
        raise ValueError("multi-nozzle slices are not supported; refusing to print")
    sliced_nozzle = metadata.nozzle_diameters[0]
    if abs(sliced_nozzle - reported_nozzle_diameter) > 0.001:
        raise ValueError(
            f"slice targets a {sliced_nozzle:g} mm nozzle, but connected printer "
            f"reports {reported_nozzle_diameter:g} mm; refusing to upload or print"
        )
    if len(metadata.nozzle_types) != 1:
        raise ValueError(
            "multi-nozzle type metadata is not supported; refusing to print"
        )
    sliced_nozzle_type = metadata.nozzle_types[0]
    live_nozzle_type = reported_nozzle_type.strip().lower()
    if sliced_nozzle_type != live_nozzle_type:
        raise ValueError(
            f"slice targets nozzle type {sliced_nozzle_type!r}, but connected printer "
            f"reports {live_nozzle_type!r}; refusing to upload or print"
        )


def _json_object(data: bytes, source: str) -> dict[str, object]:
    value: object = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"{source} must contain a JSON object")
    return cast(dict[str, object], value)


def _required_string(value: object, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} has no {field}; refusing to print")
    return value.strip()


def _nozzle_values(value: object, source: str, path: Path) -> tuple[float, ...]:
    raw_values: Sequence[object]
    if isinstance(value, list):
        raw_values = cast(list[object], value)
    elif isinstance(value, str):
        raw_values = value.split(",")
    else:
        raise ValueError(f"{path} has no nozzle diameter in {source}; refusing to print")

    try:
        nozzles = tuple(float(str(item).strip()) for item in raw_values)
    except ValueError as error:
        raise ValueError(
            f"{path} has an invalid nozzle diameter in {source}; refusing to print"
        ) from error
    if not nozzles:
        raise ValueError(f"{path} has no nozzle diameter in {source}; refusing to print")
    return nozzles


def _string_values(value: object, source: str, path: Path) -> tuple[str, ...]:
    raw_values: Sequence[object]
    if isinstance(value, list):
        raw_values = cast(list[object], value)
    elif isinstance(value, str):
        raw_values = value.split(",")
    else:
        raise ValueError(f"{path} has no {source}; refusing to print")

    values = tuple(str(item).strip().lower() for item in raw_values)
    if not values or any(not item for item in values):
        raise ValueError(f"{path} has no {source}; refusing to print")
    return values


def _gcode_settings(gcode: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in gcode.splitlines():
        line = raw_line.decode("utf-8", errors="replace")
        match = _GCODE_SETTING.fullmatch(line)
        if match:
            values[match.group("key")] = match.group("value")
    return values


def _slice_plate(
    slice_info: ElementTree.Element, plate_path: str, path: Path
) -> ElementTree.Element:
    match = _PLATE_GCODE.fullmatch(plate_path)
    if match is None:
        raise ValueError(f"invalid sliced plate path {plate_path!r}")
    plate_number = int(match.group(1))
    plates = list(slice_info.iter("plate"))
    for plate in plates:
        for metadata in plate.iter("metadata"):
            if metadata.get("key") == "index" and metadata.get("value") == str(
                plate_number
            ):
                return plate
    if len(plates) == 1 and plate_number == 1:
        return plates[0]
    if not plates and plate_number == 1:
        return slice_info
    raise ValueError(
        f"{path} lacks slice metadata for plate {plate_number}; refusing to print"
    )
