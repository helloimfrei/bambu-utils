from __future__ import annotations

import json
import ssl
import threading
from collections.abc import Callable
from itertools import count
from pathlib import PurePosixPath
from typing import Any, cast

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from bambu_utils.config import PrinterConfig

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class CommandError(RuntimeError):
    """The printer rejected a command or did not answer it."""


class MqttClient:
    """Synchronous request/response wrapper around the printer's MQTT broker."""

    def __init__(self, config: PrinterConfig) -> None:
        self._config = config
        self._sequences = count(1)

    def next_sequence(self) -> str:
        return str(next(self._sequences))

    def request(
        self,
        payload: JsonObject,
        *,
        section: str,
        command: str,
        qos: int = 0,
    ) -> JsonObject:
        sequence = _command_sequence(payload, section)

        def matches(message: JsonObject) -> bool:
            candidate = message.get(section)
            if not isinstance(candidate, dict):
                return False
            return (
                candidate.get("command") == command
                and candidate.get("sequence_id") == sequence
            )

        response = self._exchange(payload, matches, qos=qos)
        detail = response.get(section)
        if isinstance(detail, dict):
            result = detail.get("result")
            if isinstance(result, str) and result.lower() not in {"success", "ok"}:
                reason = detail.get("reason", result)
                raise CommandError(f"printer rejected {command}: {reason}")
        return response

    def status(self) -> JsonObject:
        payload = _pushall_command(self.next_sequence())

        def is_status(message: JsonObject) -> bool:
            detail = message.get("print")
            return isinstance(detail, dict) and detail.get("command") == "push_status"

        return self._exchange(payload, is_status)

    def wait_for_print_start(self, remote_path: str) -> JsonObject:
        """Wait until telemetry confirms that the submitted file is running."""

        filename = PurePosixPath(remote_path).name
        payload = _pushall_command(self.next_sequence())
        try:
            return self._exchange(payload, _print_start_matcher(filename))
        except TimeoutError as error:
            raise TimeoutError(
                f"print was submitted, but {filename} did not reach RUNNING "
                f"within {self._config.timeout:g} seconds; check the printer"
            ) from error

    def _exchange(
        self,
        payload: JsonObject,
        matches: Callable[[JsonObject], bool],
        *,
        qos: int = 0,
    ) -> JsonObject:
        connected = threading.Event()
        answered = threading.Event()
        response: list[JsonObject] = []
        connection_error: list[str] = []

        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"bambu-utils-{self._config.serial[-8:]}",
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

        report_topic = f"device/{self._config.serial}/report"
        request_topic = f"device/{self._config.serial}/request"

        def on_connect(
            connected_client: mqtt.Client,
            userdata: Any,
            flags: mqtt.ConnectFlags,
            reason_code: ReasonCode,
            properties: Properties | None,
        ) -> None:
            del userdata, flags, properties
            if reason_code.is_failure:
                connection_error.append(str(reason_code))
            else:
                connected_client.subscribe(report_topic)
            connected.set()

        def on_message(
            connected_client: mqtt.Client,
            userdata: Any,
            message: mqtt.MQTTMessage,
        ) -> None:
            del connected_client, userdata
            try:
                decoded = json.loads(message.payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            if isinstance(decoded, dict):
                candidate = cast(JsonObject, decoded)
                if matches(candidate):
                    response.append(candidate)
                    answered.set()

        client.on_connect = on_connect
        client.on_message = on_message

        try:
            client.connect(
                self._config.host,
                self._config.mqtt_port,
                keepalive=max(5, int(self._config.timeout)),
            )
            client.loop_start()
            if not connected.wait(self._config.timeout):
                raise TimeoutError("timed out connecting to the printer MQTT broker")
            if connection_error:
                raise ConnectionError(f"printer MQTT connection failed: {connection_error[0]}")

            publish = client.publish(
                request_topic,
                json.dumps(payload, separators=(",", ":")),
                qos=qos,
            )
            if publish.rc != mqtt.MQTT_ERR_SUCCESS:
                raise ConnectionError(f"failed to publish MQTT command: error {publish.rc}")
            publish.wait_for_publish(timeout=self._config.timeout)
            if not answered.wait(self._config.timeout):
                raise TimeoutError("printer did not answer the MQTT command")
            return response[0]
        finally:
            client.disconnect()
            client.loop_stop()


def _command_sequence(payload: JsonObject, section: str) -> str:
    detail = payload.get(section)
    if not isinstance(detail, dict):
        raise ValueError(f"payload has no {section!r} section")
    sequence = detail.get("sequence_id")
    if not isinstance(sequence, str):
        raise ValueError("command payload has no string sequence_id")
    return sequence


def _pushall_command(sequence: str) -> JsonObject:
    return {
        "pushing": {
            "sequence_id": sequence,
            "command": "pushall",
            "version": 1,
            "push_target": 1,
        }
    }


def _print_start_matcher(filename: str) -> Callable[[JsonObject], bool]:
    file_seen = False

    def matches(message: JsonObject) -> bool:
        nonlocal file_seen
        detail = message.get("print")
        if not isinstance(detail, dict):
            return False

        for key in ("gcode_file", "subtask_name"):
            candidate = detail.get(key)
            if isinstance(candidate, str) and PurePosixPath(candidate).name == filename:
                file_seen = True

        state = detail.get("gcode_state")
        return file_seen and isinstance(state, str) and state.upper() == "RUNNING"

    return matches
