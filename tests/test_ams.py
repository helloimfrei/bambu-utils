import pytest

from bambu_utils.ams import resolve_ams_slots
from bambu_utils.mqtt import JsonObject
from bambu_utils.project import SliceFilament


def _status(*trays: tuple[int, str, str, str]) -> JsonObject:
    return {
        "print": {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": str(slot),
                                "tray_info_idx": profile,
                                "tray_type": filament_type,
                                "tray_color": color,
                            }
                            for slot, profile, filament_type, color in trays
                        ],
                    }
                ]
            }
        }
    }


def test_auto_mapping_matches_profile_type_and_exact_color() -> None:
    filaments = (
        SliceFilament(1, "GFA00", "PLA", "#FFFFFF"),
        SliceFilament(2, "GFA00", "PLA", "#000000FF"),
    )
    status = _status(
        (0, "GFA00", "PLA", "FFFFFFFF"),
        (1, "GFA00", "PLA", "000000FF"),
    )

    assert resolve_ams_slots(filaments, status) == (0, 1)


def test_auto_mapping_preserves_unused_filament_positions() -> None:
    filaments = (SliceFilament(2, None, "PETG", "#112233"),)

    assert resolve_ams_slots(
        filaments, _status((3, "GFG99", "PETG", "112233FF"))
    ) == (-1, 3)


def test_auto_mapping_rejects_a_missing_exact_match() -> None:
    filaments = (SliceFilament(1, "GFA00", "PLA", "#FFFFFF"),)

    with pytest.raises(ValueError, match="no exact AMS match"):
        resolve_ams_slots(
            filaments, _status((0, "GFA00", "PLA", "000000FF"))
        )


def test_auto_mapping_rejects_an_ambiguous_match() -> None:
    filaments = (SliceFilament(1, "GFA00", "PLA", "#FFFFFF"),)

    with pytest.raises(ValueError, match="ambiguous across trays 0, 1"):
        resolve_ams_slots(
            filaments,
            _status(
                (0, "GFA00", "PLA", "FFFFFFFF"),
                (1, "GFA00", "PLA", "FFFFFFFF"),
            ),
        )
