from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

_PLATE_GCODE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")


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
