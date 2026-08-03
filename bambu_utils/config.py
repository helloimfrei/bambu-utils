from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrinterConfig:
    """Connection settings for a printer in LAN Developer Mode."""

    host: str
    access_code: str
    serial: str
    printer_model: str | None = None
    nozzle_diameter: float | None = None
    timeout: float = 15.0
    mqtt_port: int = 8883
    ftps_port: int = 990

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host must not be empty")
        if not self.access_code:
            raise ValueError("access_code must not be empty")
        if not self.serial:
            raise ValueError("serial must not be empty")
        if self.nozzle_diameter is not None and self.nozzle_diameter <= 0:
            raise ValueError("nozzle_diameter must be greater than zero")
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
