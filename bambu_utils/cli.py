from __future__ import annotations

import argparse
import ftplib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from bambu_utils.client import BambuPrinter, PrintOptions
from bambu_utils.config import PrinterConfig


def _parser() -> argparse.ArgumentParser:
    file_defaults = dotenv_values(Path.cwd() / ".env")

    def default(name: str, fallback: str | None = None) -> str | None:
        return os.environ.get(name) or file_defaults.get(name) or fallback

    parser = argparse.ArgumentParser(
        prog="bambu-utils",
        description="Control a Bambu Lab printer directly over LAN Developer Mode.",
    )
    parser.add_argument("--host", default=default("BAMBU_HOST"))
    parser.add_argument("--serial", default=default("BAMBU_SERIAL"))
    parser.add_argument("--access-code", default=default("BAMBU_ACCESS_CODE"))
    parser.add_argument("--printer-model", default=default("BAMBU_PRINTER_MODEL"))
    parser.add_argument("--timeout", type=float, default=default("BAMBU_TIMEOUT", "15"))
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="request and print current printer status")
    commands.add_parser("pause", help="pause the active print")
    commands.add_parser("resume", help="resume the paused print")
    commands.add_parser("stop", help="stop the active print")

    upload = commands.add_parser("upload", help="upload a file over implicit FTPS")
    upload.add_argument("local", type=Path)
    upload.add_argument("--remote", help="destination relative to the FTPS root")

    listing = commands.add_parser("files", help="list files on the printer")
    listing.add_argument("remote", nargs="?", default="/")

    download = commands.add_parser("download", help="download a printer file")
    download.add_argument("remote")
    download.add_argument("local", type=Path)

    delete = commands.add_parser("delete", help="delete a printer file")
    delete.add_argument("remote")

    send = commands.add_parser("print", help="upload and print sliced 3MF or G-code")
    send.add_argument("local", type=Path)
    send.add_argument("--remote", help="destination relative to the FTPS root")
    send.add_argument("--plate", type=int, default=1)
    send.add_argument(
        "--ams",
        type=_ams_slots,
        metavar="SLOTS",
        help="'auto' or comma-separated absolute AMS tray IDs, such as 0,1,4",
    )
    send.add_argument(
        "--bed-leveling", action=argparse.BooleanOptionalAction, default=True
    )
    send.add_argument(
        "--flow-calibration", action=argparse.BooleanOptionalAction, default=True
    )
    send.add_argument(
        "--vibration-calibration", action=argparse.BooleanOptionalAction, default=True
    )
    send.add_argument(
        "--layer-inspection", action=argparse.BooleanOptionalAction, default=True
    )
    send.add_argument("--timelapse", action="store_true")
    return parser


def _ams_slots(value: str) -> tuple[int, ...] | Literal["auto"]:
    if value.strip().lower() == "auto":
        return "auto"
    try:
        slots = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("AMS slots must be comma-separated integers") from error
    if not slots:
        raise argparse.ArgumentTypeError("at least one AMS slot is required")
    return slots


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = PrinterConfig(
            host=_required(parser, "--host", "BAMBU_HOST", args.host),
            serial=_required(parser, "--serial", "BAMBU_SERIAL", args.serial),
            access_code=_required(
                parser, "--access-code", "BAMBU_ACCESS_CODE", args.access_code
            ),
            printer_model=args.printer_model,
            timeout=args.timeout,
        )
        printer = BambuPrinter(config)

        match args.command:
            case "status":
                print(json.dumps(printer.status(), indent=2, sort_keys=True))
            case "pause":
                printer.pause()
                print("Print paused")
            case "resume":
                printer.resume()
                print("Print resumed")
            case "stop":
                printer.stop()
                print("Print stopped")
            case "upload":
                remote = printer.upload(args.local, args.remote)
                print(remote)
            case "files":
                for name in printer.files.list(args.remote):
                    print(name)
            case "download":
                printer.files.download(args.remote, args.local)
                print(args.local)
            case "delete":
                printer.files.delete(args.remote)
                print(f"Deleted {args.remote}")
            case "print":
                options = PrintOptions(
                    plate=args.plate,
                    ams_slots=args.ams,
                    bed_leveling=args.bed_leveling,
                    flow_calibration=args.flow_calibration,
                    vibration_calibration=args.vibration_calibration,
                    layer_inspection=args.layer_inspection,
                    timelapse=args.timelapse,
                )
                submission = printer.send(
                    args.local, remote_path=args.remote, options=options
                )
                ams = (
                    f", AMS slots {','.join(str(slot) for slot in submission.ams_slots)}"
                    if submission.ams_slots is not None
                    else ""
                )
                print(
                    f"Print confirmed running: {submission.remote_path} "
                    f"(plate {options.plate}{ams})"
                )
            case _:
                raise AssertionError(f"unhandled command: {args.command}")
        return 0
    except (
        ConnectionError,
        ftplib.Error,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _required(
    parser: argparse.ArgumentParser, option: str, environment: str, value: str | None
) -> str:
    if value:
        return value
    parser.error(f"{option} is required (or set {environment})")
