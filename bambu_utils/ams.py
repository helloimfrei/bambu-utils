from __future__ import annotations

import re
from dataclasses import dataclass

from bambu_utils.mqtt import JsonObject
from bambu_utils.project import SliceFilament

_COLOR = re.compile(r"#?(?P<rgb>[0-9A-Fa-f]{6})(?P<alpha>[0-9A-Fa-f]{2})?")


@dataclass(frozen=True, slots=True)
class AmsTray:
    id: int
    profile_id: str | None
    filament_type: str
    color: str


def resolve_ams_slots(
    filaments: tuple[SliceFilament, ...], status: JsonObject
) -> tuple[int, ...]:
    """Map sliced filaments to unique, exact matches in live AMS telemetry."""

    trays = _ams_trays(status)
    if not trays:
        raise ValueError("printer reports no populated AMS trays; refusing to print")

    mapping = [-1] * max(filament.id for filament in filaments)
    for filament in filaments:
        color = _normalized_color(filament.color, f"sliced filament {filament.id}")
        candidates = [
            tray
            for tray in trays
            if tray.filament_type.casefold() == filament.filament_type.casefold()
            and tray.color == color
            and (
                filament.profile_id is None
                or tray.profile_id == filament.profile_id
            )
        ]
        wanted = _filament_description(
            filament.filament_type, color, filament.profile_id
        )
        if not candidates:
            available = ", ".join(
                f"{tray.id}: {_filament_description(tray.filament_type, tray.color, tray.profile_id)}"
                for tray in trays
            )
            raise ValueError(
                f"no exact AMS match for sliced filament {filament.id} ({wanted}); "
                f"available trays: {available}; use --ams with explicit tray IDs to override"
            )
        if len(candidates) > 1:
            slots = ", ".join(str(tray.id) for tray in candidates)
            raise ValueError(
                f"AMS match for sliced filament {filament.id} ({wanted}) is ambiguous "
                f"across trays {slots}; use --ams with explicit tray IDs"
            )
        mapping[filament.id - 1] = candidates[0].id
    return tuple(mapping)


def _ams_trays(status: JsonObject) -> tuple[AmsTray, ...]:
    detail = status.get("print")
    ams_status = detail.get("ams") if isinstance(detail, dict) else None
    units = ams_status.get("ams") if isinstance(ams_status, dict) else None
    if not isinstance(units, list):
        return ()

    trays: list[AmsTray] = []
    seen_ids: set[int] = set()
    for raw_unit in units:
        if not isinstance(raw_unit, dict):
            continue
        unit_id = _integer(raw_unit.get("id"))
        raw_trays = raw_unit.get("tray")
        if unit_id is None or not isinstance(raw_trays, list):
            continue
        for raw_tray in raw_trays:
            if not isinstance(raw_tray, dict):
                continue
            slot_id = _integer(raw_tray.get("id"))
            filament_type = raw_tray.get("tray_type")
            raw_color = raw_tray.get("tray_color")
            if (
                slot_id is None
                or not isinstance(filament_type, str)
                or not filament_type.strip()
                or not isinstance(raw_color, str)
                or not raw_color.strip()
            ):
                continue
            tray_id = unit_id * 4 + slot_id
            if tray_id in seen_ids:
                raise ValueError(
                    f"printer reported duplicate AMS tray ID {tray_id}; refusing to print"
                )
            profile_value = raw_tray.get("tray_info_idx")
            profile = (
                profile_value.strip()
                if isinstance(profile_value, str) and profile_value.strip()
                else None
            )
            seen_ids.add(tray_id)
            trays.append(
                AmsTray(
                    id=tray_id,
                    profile_id=profile,
                    filament_type=filament_type.strip(),
                    color=_normalized_color(raw_color, f"AMS tray {tray_id}"),
                )
            )
    return tuple(sorted(trays, key=lambda tray: tray.id))


def _integer(value: object) -> int | None:
    if not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _normalized_color(value: str, source: str) -> str:
    match = _COLOR.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"{source} has invalid color {value!r}; refusing to print")
    return f"{match.group('rgb').upper()}{(match.group('alpha') or 'FF').upper()}"


def _filament_description(
    filament_type: str, color: str, profile_id: str | None
) -> str:
    profile = f", profile {profile_id}" if profile_id else ""
    return f"{filament_type}, #{color}{profile}"
