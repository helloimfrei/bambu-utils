import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
SCRIPT = REPO_ROOT / "scripts" / "send-slice"


def _fake_uv(tmp_path: Path) -> tuple[Path, Path]:
    capture = tmp_path / "arguments"
    executable = tmp_path / "uv"
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$PWD\" \"$@\" > \"$CAPTURE\"\n"
    )
    executable.chmod(0o755)
    return executable, capture


def _run_script(tmp_path: Path, *arguments: str) -> list[str]:
    fake_uv, capture = _fake_uv(tmp_path)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_uv.parent}:{environment['PATH']}"
    environment["CAPTURE"] = str(capture)
    subprocess.run(
        [SCRIPT, *arguments],
        cwd=tmp_path,
        env=environment,
        check=True,
    )
    return capture.read_text().splitlines()


def test_send_slice_uploads_without_starting(tmp_path: Path) -> None:
    sliced_file = tmp_path / "part.gcode.3mf"
    sliced_file.write_bytes(b"slice")

    invocation = _run_script(tmp_path, sliced_file.name)

    assert invocation == [
        str(REPO_ROOT),
        "run",
        "bambu-utils",
        "upload",
        str(sliced_file),
    ]


def test_send_slice_can_start_print_and_forward_options(tmp_path: Path) -> None:
    sliced_file = tmp_path / "multi.gcode.3mf"
    sliced_file.write_bytes(b"slice")

    invocation = _run_script(
        tmp_path, "--print", sliced_file.name, "--plate", "2", "--ams", "0,1"
    )

    assert invocation == [
        str(REPO_ROOT),
        "run",
        "bambu-utils",
        "print",
        str(sliced_file),
        "--plate",
        "2",
        "--ams",
        "0,1",
    ]


def test_send_slice_rejects_a_missing_file(tmp_path: Path) -> None:
    result = subprocess.run(
        [SCRIPT, "missing.gcode.3mf"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "sliced file does not exist" in result.stderr


def test_send_slice_help_does_not_require_a_file(tmp_path: Path) -> None:
    result = subprocess.run(
        [SCRIPT, "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "The default is upload only" in result.stdout
