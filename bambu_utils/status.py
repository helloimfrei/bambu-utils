from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import cast

from pydantic import BaseModel, Field

from bambu_utils.mqtt import JsonObject, JsonValue


class RunStatus(BaseModel):
    state: str = "UNKNOWN"
    active: bool = False
    paused: bool = False
    name: str = ""
    file: str = ""
    progress: int = Field(default=0, ge=0, le=100)
    remaining_minutes: int = Field(default=0, ge=0)
    layer: int = Field(default=0, ge=0)
    total_layers: int = Field(default=0, ge=0)
    stage: str | None = None
    speed_level: int = 0
    speed_percent: int = 100
    error_code: int = 0


class TemperatureStatus(BaseModel):
    current: float | None = None
    target: float | None = None


class TemperaturesStatus(BaseModel):
    nozzle: TemperatureStatus
    bed: TemperatureStatus
    chamber: TemperatureStatus | None = None


class FansStatus(BaseModel):
    part: int | None = Field(default=None, ge=0, le=100)
    heatbreak: int | None = Field(default=None, ge=0, le=100)
    auxiliary: int | None = Field(default=None, ge=0, le=100)
    chamber: int | None = Field(default=None, ge=0, le=100)


class DeviceStatus(BaseModel):
    model: str | None = None
    nozzle_diameter: float | None = None
    nozzle_type: str | None = None
    wifi_signal_dbm: int | None = None
    sd_card: bool | None = None
    light_on: bool = False
    camera_available: bool = False
    camera_resolution: str | None = None


class AmsTrayStatus(BaseModel):
    id: int
    unit_id: int
    slot_id: int
    present: bool
    active: bool
    target: bool
    filament_type: str | None = None
    subtype: str | None = None
    color: str | None = None
    profile_id: str | None = None
    remaining_percent: int | None = Field(default=None, ge=0, le=100)
    pressure_advance: float | None = None


class AmsStatus(BaseModel):
    connected: bool = False
    current_tray: int | None = None
    target_tray: int | None = None
    trays: list[AmsTrayStatus] = Field(
        default_factory=lambda: list[AmsTrayStatus]()
    )


class AlertStatus(BaseModel):
    code: str
    message: str | None = None


class PrinterStatus(BaseModel):
    connected: bool = False
    updated_at: datetime | None = None
    connection_error: str | None = None
    run: RunStatus = Field(default_factory=RunStatus)
    temperatures: TemperaturesStatus = Field(
        default_factory=lambda: TemperaturesStatus(
            nozzle=TemperatureStatus(), bed=TemperatureStatus()
        )
    )
    fans: FansStatus = Field(default_factory=FansStatus)
    device: DeviceStatus = Field(default_factory=DeviceStatus)
    ams: AmsStatus = Field(default_factory=AmsStatus)
    alerts: list[AlertStatus] = Field(
        default_factory=lambda: list[AlertStatus]()
    )


def normalize_status(
    raw: JsonObject,
    *,
    connected: bool,
    model: str | None,
    updated_at: datetime | None,
    connection_error: str | None = None,
) -> PrinterStatus:
    detail = _object(raw.get("print"))
    state = (_text(detail.get("gcode_state")) or "UNKNOWN").upper()
    active = state in {"RUNNING", "PAUSE"}
    filename = _text(detail.get("gcode_file")) or ""
    name = _text(detail.get("subtask_name")) or ""
    if not name and filename:
        name = PurePosixPath(filename).name.removesuffix(".gcode.3mf")

    print_error = _integer(detail.get("print_error")) or 0
    run = RunStatus(
        state=state,
        active=active,
        paused=state == "PAUSE",
        name=name,
        file=filename,
        progress=_bounded_integer(detail.get("mc_percent"), 0, 100),
        remaining_minutes=max(0, _integer(detail.get("mc_remaining_time")) or 0),
        layer=max(0, _integer(detail.get("layer_num")) or 0),
        total_layers=max(0, _integer(detail.get("total_layer_num")) or 0),
        stage=_text(detail.get("mc_print_stage")) if active else None,
        speed_level=_integer(detail.get("spd_lvl")) or 0,
        speed_percent=_integer(detail.get("spd_mag")) or 100,
        error_code=print_error,
    )

    enclosed = (model or "").strip().upper() in {"X1C", "X1E", "P1S"}
    temperatures = TemperaturesStatus(
        nozzle=TemperatureStatus(
            current=_number(detail.get("nozzle_temper")),
            target=_number(detail.get("nozzle_target_temper")),
        ),
        bed=TemperatureStatus(
            current=_number(detail.get("bed_temper")),
            target=_number(detail.get("bed_target_temper")),
        ),
        chamber=(
            TemperatureStatus(current=_number(detail.get("chamber_temper")))
            if enclosed
            else None
        ),
    )
    fans = FansStatus(
        part=_fan_percent(detail.get("cooling_fan_speed")),
        heatbreak=_fan_percent(detail.get("heatbreak_fan_speed")),
        auxiliary=_fan_percent(detail.get("big_fan1_speed")) if enclosed else None,
        chamber=_fan_percent(detail.get("big_fan2_speed")) if enclosed else None,
    )

    ipcam = _object(detail.get("ipcam"))
    device = DeviceStatus(
        model=model,
        nozzle_diameter=_number(detail.get("nozzle_diameter")),
        nozzle_type=_text(detail.get("nozzle_type")),
        wifi_signal_dbm=_wifi_signal(detail.get("wifi_signal")),
        sd_card=_boolean(detail.get("sdcard")),
        light_on=_light_is_on(detail.get("lights_report")),
        camera_available=(_text(ipcam.get("ipcam_dev")) not in {None, "0", "disable"}),
        camera_resolution=_text(ipcam.get("resolution")),
    )

    return PrinterStatus(
        connected=connected,
        updated_at=updated_at,
        connection_error=connection_error,
        run=run,
        temperatures=temperatures,
        fans=fans,
        device=device,
        ams=_ams_status(detail),
        alerts=_alerts(detail, print_error),
    )


def _ams_status(detail: dict[str, JsonValue]) -> AmsStatus:
    ams = _object(detail.get("ams"))
    units = _array(ams.get("ams"))
    current = _ams_tray_id(ams.get("tray_now"))
    target = _ams_tray_id(ams.get("tray_tar"))
    trays: list[AmsTrayStatus] = []
    for raw_unit in units:
        unit = _object(raw_unit)
        unit_id = _integer(unit.get("id"))
        if unit_id is None:
            continue
        for raw_tray in _array(unit.get("tray")):
            tray = _object(raw_tray)
            slot_id = _integer(tray.get("id"))
            if slot_id is None:
                continue
            tray_id = unit_id * 4 + slot_id
            filament_type = _text(tray.get("tray_type"))
            trays.append(
                AmsTrayStatus(
                    id=tray_id,
                    unit_id=unit_id,
                    slot_id=slot_id,
                    present=filament_type is not None,
                    active=tray_id == current,
                    target=tray_id == target,
                    filament_type=filament_type,
                    subtype=_text(tray.get("tray_sub_brands")),
                    color=_color(tray.get("tray_color")),
                    profile_id=_text(tray.get("tray_info_idx")),
                    remaining_percent=_optional_bounded_integer(
                        tray.get("remain"), 0, 100
                    ),
                    pressure_advance=_number(tray.get("k")),
                )
            )
    return AmsStatus(
        connected=bool(units),
        current_tray=current,
        target_tray=target,
        trays=trays,
    )


def _alerts(detail: dict[str, JsonValue], print_error: int) -> list[AlertStatus]:
    alerts: list[AlertStatus] = []
    if print_error:
        alerts.append(AlertStatus(code=str(print_error), message="Printer error"))
    for raw_alert in _array(detail.get("hms")):
        alert = _object(raw_alert)
        parts: list[str] = []
        for key in ("attr", "code"):
            part = _text(alert.get(key))
            if part is not None:
                parts.append(part)
        if parts:
            alerts.append(
                AlertStatus(
                    code="-".join(parts),
                    message=_text(alert.get("msg")),
                )
            )
    return alerts


def _object(value: JsonValue | object) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return cast(dict[str, JsonValue], value)
    return {}


def _array(value: JsonValue | object) -> list[JsonValue]:
    if isinstance(value, list):
        return cast(list[JsonValue], value)
    return []


def _text(value: JsonValue | object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _integer(value: JsonValue | object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _number(value: JsonValue | object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _boolean(value: JsonValue | object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, str)):
        return str(value).strip().lower() in {"1", "true", "on", "enable"}
    return None


def _bounded_integer(value: JsonValue | object, lower: int, upper: int) -> int:
    parsed = _integer(value)
    return min(upper, max(lower, parsed or 0))


def _optional_bounded_integer(
    value: JsonValue | object, lower: int, upper: int
) -> int | None:
    parsed = _integer(value)
    return min(upper, max(lower, parsed)) if parsed is not None else None


def _ams_tray_id(value: JsonValue | object) -> int | None:
    tray_id = _integer(value)
    return tray_id if tray_id is not None and tray_id != 255 else None


def _fan_percent(value: JsonValue | object) -> int | None:
    level = _integer(value)
    if level is None:
        return None
    if 0 <= level <= 15:
        return round(level * 100 / 15)
    return min(100, max(0, level))


def _wifi_signal(value: JsonValue | object) -> int | None:
    text = _text(value)
    if text is None:
        return _integer(value)
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


def _light_is_on(value: JsonValue | object) -> bool:
    for raw_light in _array(value):
        light = _object(raw_light)
        if light.get("node") == "chamber_light":
            return _text(light.get("mode")) == "on"
    return False


def _color(value: JsonValue | object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    raw = text.removeprefix("#")
    if len(raw) not in {6, 8} or not all(char in "0123456789abcdefABCDEF" for char in raw):
        return None
    return f"#{raw[:6].upper()}"
