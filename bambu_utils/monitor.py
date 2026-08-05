from __future__ import annotations

import json
import logging
import ssl
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from typing import Any, cast

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from bambu_utils.config import PrinterConfig
from bambu_utils.mqtt import JsonObject, JsonValue
from bambu_utils.status import PrinterStatus, normalize_status

_LOGGER = logging.getLogger(__name__)


class PrinterMonitor:
    """Persistent MQTT status stream with a thread-safe normalized snapshot."""

    def __init__(self, config: PrinterConfig) -> None:
        self._config = config
        self._condition = threading.Condition()
        self._raw: JsonObject = {}
        self._connected = False
        self._error: str | None = None
        self._updated_at: datetime | None = None
        self._snapshot = normalize_status(
            self._raw,
            connected=False,
            model=config.printer_model,
            updated_at=None,
        )
        self._version = 0
        self._client: mqtt.Client | None = None
        self._sequences = count(1)
        self._observers: list[Callable[[PrinterStatus], None]] = []
        self._stopping = False

    def add_observer(self, observer: Callable[[PrinterStatus], None]) -> None:
        self._observers.append(observer)

    def start(self) -> None:
        with self._condition:
            if self._client is not None:
                return
            self._stopping = False

        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"bambu-monitor-{self._config.serial[-8:]}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set("bblp", self._config.access_code)
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls_context.check_hostname = False
        tls_context.verify_mode = ssl.CERT_NONE
        set_tls_context = cast(
            Callable[[ssl.SSLContext], None], getattr(client, "tls_set_context")
        )
        set_tls_context(tls_context)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        with self._condition:
            self._client = client
        client.connect_async(
            self._config.host,
            self._config.mqtt_port,
            keepalive=max(5, int(self._config.timeout)),
        )
        client.loop_start()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            client = self._client
            self._client = None
        if client is not None:
            client.disconnect()
            client.loop_stop()
        self._set_connection(False, None)

    def snapshot(self) -> tuple[int, PrinterStatus]:
        with self._condition:
            return self._version, self._snapshot

    def wait_for_update(
        self, version: int, timeout: float = 15.0
    ) -> tuple[int, PrinterStatus]:
        with self._condition:
            self._condition.wait_for(lambda: self._version != version, timeout=timeout)
            return self._version, self._snapshot

    def ingest(self, message: JsonObject) -> None:
        """Merge one printer report; public to support protocol fixtures and tests."""

        detail = message.get("print")
        if not isinstance(detail, dict):
            return
        now = datetime.now(UTC)
        with self._condition:
            raw_detail = self._raw.get("print")
            if not isinstance(raw_detail, dict):
                raw_detail = {}
                self._raw["print"] = raw_detail
            _merge_json(raw_detail, detail)
            self._connected = True
            self._error = None
            self._updated_at = now
            snapshot = self._replace_snapshot_locked()
        self._notify_observers(snapshot)

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        del userdata, flags, properties
        if reason_code.is_failure:
            self._set_connection(False, str(reason_code))
            return
        self._set_connection(True, None)
        client.subscribe(f"device/{self._config.serial}/report")
        payload = {
            "pushing": {
                "sequence_id": str(next(self._sequences)),
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        client.publish(
            f"device/{self._config.serial}/request",
            json.dumps(payload, separators=(",", ":")),
        )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        del client, userdata, disconnect_flags, properties
        error = None if self._stopping else str(reason_code)
        self._set_connection(False, error)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        del client, userdata
        try:
            decoded: object = json.loads(message.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if isinstance(decoded, dict):
            self.ingest(cast(JsonObject, decoded))

    def _set_connection(self, connected: bool, error: str | None) -> None:
        with self._condition:
            if self._connected == connected and self._error == error:
                return
            self._connected = connected
            self._error = error
            snapshot = self._replace_snapshot_locked()
        self._notify_observers(snapshot)

    def _replace_snapshot_locked(self) -> PrinterStatus:
        self._snapshot = normalize_status(
            self._raw,
            connected=self._connected,
            model=self._config.printer_model,
            updated_at=self._updated_at,
            connection_error=self._error,
        )
        self._version += 1
        self._condition.notify_all()
        return self._snapshot

    def _notify_observers(self, snapshot: PrinterStatus) -> None:
        for observer in self._observers:
            try:
                observer(snapshot)
            except Exception:
                # Monitoring must survive a history or presentation-layer failure.
                _LOGGER.exception("status observer failed")
                continue


def _merge_json(target: dict[str, JsonValue], update: dict[str, JsonValue]) -> None:
    for key, value in update.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _merge_json(current, value)
        else:
            target[key] = value
