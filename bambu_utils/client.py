from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from bambu_utils.config import PrinterConfig
from bambu_utils.ftps import FileTransferClient, default_remote_path, normalize_remote_path
from bambu_utils.mqtt import JsonObject, MqttClient
from bambu_utils.project import (
    SliceMetadata,
    file_md5,
    gcode_metadata,
    sliced_3mf_metadata,
    sliced_3mf_plates,
    validate_slice_for_printer,
)


@dataclass(frozen=True, slots=True)
class PrintOptions:
    plate: int = 1
    ams_slots: tuple[int, ...] | None = None
    bed_leveling: bool = True
    flow_calibration: bool = True
    vibration_calibration: bool = True
    layer_inspection: bool = True
    timelapse: bool = False

    def __post_init__(self) -> None:
        if self.plate < 1:
            raise ValueError("plate must be at least 1")
        if self.ams_slots is not None and not self.ams_slots:
            raise ValueError("ams_slots must contain at least one slot")
        if self.ams_slots is not None and any(slot < -1 for slot in self.ams_slots):
            raise ValueError("AMS slots must be -1 (unmapped) or non-negative")


class BambuPrinter:
    """Direct LAN operations for a Bambu Lab printer."""

    def __init__(self, config: PrinterConfig) -> None:
        self.config = config
        self.files = FileTransferClient(config)
        self._mqtt = MqttClient(config)

    def upload(self, local_path: Path, remote_path: str | None = None) -> str:
        local = local_path.expanduser().resolve(strict=True)
        remote = normalize_remote_path(remote_path or default_remote_path(local))
        self.files.upload(local, remote)
        return remote

    def send(
        self,
        local_path: Path,
        *,
        remote_path: str | None = None,
        options: PrintOptions | None = None,
    ) -> str:
        """Upload a sliced 3MF or G-code file and start it."""

        local = local_path.expanduser().resolve(strict=True)
        selected = options or PrintOptions()
        remote = normalize_remote_path(remote_path or default_remote_path(local))

        if local.name.lower().endswith(".3mf"):
            plates = sliced_3mf_plates(local)
            if selected.plate not in plates:
                available = ", ".join(str(plate) for plate in sorted(plates))
                raise ValueError(
                    f"plate {selected.plate} is not present in {local}; available: {available}"
                )
            metadata = sliced_3mf_metadata(local, plates[selected.plate])
            payload = project_file_command(
                sequence=self._mqtt.next_sequence(),
                serial=self.config.serial,
                local_path=local,
                remote_path=remote,
                plate_path=plates[selected.plate],
                options=selected,
            )
            command = "project_file"
        elif local.name.lower().endswith(".gcode"):
            metadata = gcode_metadata(local)
            payload = gcode_file_command(
                sequence=self._mqtt.next_sequence(), remote_path=remote
            )
            command = "gcode_file"
        else:
            raise ValueError("print input must be a sliced .3mf or a .gcode file")

        self._validate_compatibility(metadata)
        self.files.upload(local, remote)
        self._mqtt.request(payload, section="print", command=command)
        self._mqtt.wait_for_print_start(remote)
        return remote

    def status(self) -> JsonObject:
        return self._mqtt.status()

    def pause(self) -> JsonObject:
        return self._print_control("pause")

    def resume(self) -> JsonObject:
        return self._print_control("resume")

    def stop(self) -> JsonObject:
        return self._print_control("stop")

    def _print_control(self, command: str) -> JsonObject:
        payload = print_control_command(self._mqtt.next_sequence(), command)
        return self._mqtt.request(payload, section="print", command=command, qos=1)

    def _validate_compatibility(self, metadata: SliceMetadata) -> None:
        if self.config.printer_model is None:
            raise ValueError(
                "BAMBU_PRINTER_MODEL is required for safe print submission"
            )
        status = self._mqtt.status()
        detail = status.get("print")
        nozzle = detail.get("nozzle_diameter") if isinstance(detail, dict) else None
        nozzle_type = detail.get("nozzle_type") if isinstance(detail, dict) else None
        try:
            nozzle_diameter = float(nozzle) if isinstance(nozzle, (int, float, str)) else None
        except ValueError as error:
            raise ValueError(
                "printer reported an invalid nozzle diameter; refusing to print"
            ) from error
        if nozzle_diameter is None:
            raise ValueError("printer did not report its nozzle diameter; refusing to print")
        if not isinstance(nozzle_type, str) or not nozzle_type.strip():
            raise ValueError("printer did not report its nozzle type; refusing to print")
        validate_slice_for_printer(
            metadata,
            configured_printer_model=self.config.printer_model,
            printer_serial=self.config.serial,
            reported_nozzle_diameter=nozzle_diameter,
            reported_nozzle_type=nozzle_type,
        )


def print_control_command(sequence: str, command: str) -> JsonObject:
    if command not in {"pause", "resume", "stop"}:
        raise ValueError(f"unsupported print control command: {command}")
    return {
        "print": {
            "sequence_id": sequence,
            "command": command,
            "param": "",
        }
    }


def gcode_file_command(sequence: str, remote_path: str) -> JsonObject:
    return {
        "print": {
            "sequence_id": sequence,
            "command": "gcode_file",
            "param": normalize_remote_path(remote_path),
        }
    }


def project_file_command(
    *,
    sequence: str,
    serial: str,
    local_path: Path,
    remote_path: str,
    plate_path: str,
    options: PrintOptions,
) -> JsonObject:
    remote = normalize_remote_path(remote_path)
    ams_mapping, ams_mapping2 = _ams_mapping(options.ams_slots)
    name = Path(remote).name
    subtask_name = name.removesuffix(".3mf").removesuffix(".gcode")
    return cast(JsonObject, {
        "print": {
            "sequence_id": sequence,
            "command": "project_file",
            "param": plate_path,
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "subtask_name": subtask_name,
            "file": remote,
            "url": _project_file_url(remote, serial),
            "md5": file_md5(local_path),
            "bed_type": "auto",
            "bed_leveling": options.bed_leveling,
            "flow_cali": options.flow_calibration,
            "vibration_cali": options.vibration_calibration,
            "layer_inspect": options.layer_inspection,
            "timelapse": options.timelapse,
            "use_ams": options.ams_slots is not None,
            "ams_mapping": ams_mapping,
            "ams_mapping2": ams_mapping2,
        }
    })


def _project_file_url(remote_path: str, serial: str) -> str:
    # A1/A1 Mini and P1 firmware address FTPS-uploaded files through the
    # mounted SD-card path when dispatching a project_file command.
    if serial[:3].upper() in {"01P", "01S", "030", "039"}:
        return f"file:///sdcard/{remote_path}"
    return f"ftp:///{remote_path}"


def _ams_mapping(
    slots: tuple[int, ...] | None,
) -> tuple[list[int], list[dict[str, int]]]:
    if slots is None:
        return [-1], [{"ams_id": 255, "slot_id": 0}]

    mapping2: list[dict[str, int]] = []
    for tray_id in slots:
        if tray_id < 0:
            mapping2.append({"ams_id": 255, "slot_id": 255})
        elif 128 <= tray_id <= 253:
            mapping2.append({"ams_id": tray_id, "slot_id": 0})
        else:
            mapping2.append({"ams_id": tray_id // 4, "slot_id": tray_id % 4})
    return list(slots), mapping2
