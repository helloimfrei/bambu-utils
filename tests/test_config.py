import pytest

from bambu_utils.config import PrinterConfig


def test_config_rejects_missing_credentials() -> None:
    with pytest.raises(ValueError, match="access_code"):
        PrinterConfig(host="192.0.2.10", serial="SERIAL", access_code="")


def test_config_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        PrinterConfig(
            host="192.0.2.10", serial="SERIAL", access_code="CODE", timeout=0
        )


def test_config_rejects_non_positive_nozzle_diameter() -> None:
    with pytest.raises(ValueError, match="nozzle_diameter"):
        PrinterConfig(
            host="192.0.2.10",
            serial="SERIAL",
            access_code="CODE",
            nozzle_diameter=0,
        )
