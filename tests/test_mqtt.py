from bambu_utils.mqtt import JsonObject, _print_start_matcher  # pyright: ignore[reportPrivateUsage]


def test_print_start_matcher_confirms_the_expected_running_file() -> None:
    matches = _print_start_matcher("part.gcode.3mf")

    assert not matches(
        {
            "print": {
                "gcode_file": "part.gcode.3mf",
                "gcode_state": "PREPARE",
            }
        }
    )
    assert matches({"print": {"gcode_state": "RUNNING"}})


def test_print_start_matcher_rejects_another_running_file() -> None:
    matches = _print_start_matcher("expected.gcode.3mf")
    message: JsonObject = {
        "print": {
            "gcode_file": "cache/another.gcode.3mf",
            "gcode_state": "RUNNING",
        }
    }

    assert not matches(message)
