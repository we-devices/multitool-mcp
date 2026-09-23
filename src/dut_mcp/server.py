"""Dependency-free STDIO MCP server for debugging DUTs through WE Multitool."""

from __future__ import annotations

import base64
import json
import os
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from .device import DeviceClient, DeviceError

JsonObject = dict[str, Any]


def _object_schema(properties: JsonObject, required: list[str] | None = None) -> JsonObject:
    schema: JsonObject = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


PIN = {"type": "integer", "minimum": 0, "maximum": 63, "description": "ESP32 GPIO number."}
OPTIONAL_PIN = {"type": "integer", "minimum": -1, "maximum": 63, "description": "ESP32 GPIO number, or -1 when unused."}
BYTE = {"type": "integer", "minimum": 0, "maximum": 255}
BYTES = {"type": "array", "items": BYTE, "maxItems": 1024}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: JsonObject
    handler: Callable[[JsonObject], Any]
    read_only: bool = False
    idempotent: bool = False

    def definition(self) -> JsonObject:
        annotations: JsonObject = {"readOnlyHint": self.read_only, "openWorldHint": False}
        if not self.read_only:
            annotations.update({"destructiveHint": False, "idempotentHint": self.idempotent})
        return {"name": self.name, "description": self.description, "inputSchema": self.schema, "annotations": annotations}


class DutTools:
    def __init__(self, client: DeviceClient) -> None:
        self.client = client
        self.tools = {tool.name: tool for tool in self._build_tools()}

    @staticmethod
    def _type(name: str) -> Callable[[JsonObject], bool]:
        return lambda message: message.get("type") == name

    @staticmethod
    def _check_error(message: JsonObject) -> JsonObject:
        if message.get("error") is True or message.get("result_type") == 0:
            raise DeviceError(str(message.get("data", "Device command failed")))
        return message

    def _command(self, command: JsonObject, response_type: str) -> JsonObject:
        return self._check_error(self.client.request(command, self._type(response_type)))

    def _build_tools(self) -> list[Tool]:
        return [
            Tool("device_status", "Connect to WE Multitool and return its URL and GPIO ownership map.", _object_schema({}), self.device_status, True),
            Tool("pins_get", "Read the current GPIO ownership map before assigning pins.", _object_schema({}), self.pins_get, True),
            Tool("gpio_configure", "Configure a free GPIO as input, push-pull output, or open-drain output.", _object_schema({"pin": PIN, "direction": {"type": "string", "enum": ["input", "output", "open-drain"]}}, ["pin", "direction"]), self.gpio_configure, False, True),
            Tool("gpio_read", "Read a GPIO that is configured as input.", _object_schema({"pin": PIN}, ["pin"]), self.gpio_read, True),
            Tool("gpio_write", "Drive a GPIO configured as output. Firmware dispatches this command without an acknowledgement.", _object_schema({"pin": PIN, "level": {"type": "integer", "enum": [0, 1]}}, ["pin", "level"]), self.gpio_write, False, True),
            Tool("i2c_configure", "Configure WE Multitool as an I2C master.", _object_schema({"sda_pin": PIN, "scl_pin": PIN, "frequency_hz": {"type": "integer", "minimum": 1000, "maximum": 1000000}}, ["sda_pin", "scl_pin", "frequency_hz"]), self.i2c_configure, False, True),
            Tool("i2c_scan", "Scan the configured I2C bus and return responding 7-bit addresses.", _object_schema({}), self.i2c_scan, True),
            Tool("i2c_read", "Read bytes from a register on the configured I2C bus.", _object_schema({"address": {"type": "integer", "minimum": 1, "maximum": 127}, "register": BYTE, "length": {"type": "integer", "minimum": 1, "maximum": 128}}, ["address", "register", "length"]), self.i2c_read, True),
            Tool("i2c_write", "Write bytes to a register on the configured I2C bus.", _object_schema({"address": {"type": "integer", "minimum": 1, "maximum": 127}, "register": BYTE, "data": BYTES}, ["address", "register", "data"]), self.i2c_write),
            Tool("spi_configure", "Configure WE Multitool as an SPI master.", _object_schema({"cs_pin": PIN, "sck_pin": PIN, "mosi_pin": PIN, "miso_pin": PIN, "frequency_hz": {"type": "integer", "minimum": 1000}, "mode": {"type": "integer", "minimum": 0, "maximum": 3}}, ["cs_pin", "sck_pin", "mosi_pin", "miso_pin", "frequency_hz", "mode"]), self.spi_configure, False, True),
            Tool("spi_transfer", "Write bytes and optionally read bytes in one SPI master transaction.", _object_schema({"write_data": BYTES, "read_length": {"type": "integer", "minimum": 0, "maximum": 1024}}, ["write_data", "read_length"]), self.spi_transfer),
            Tool("uart_configure", "Configure UART2 pins and framing. Use -1 for unused CTS/RTS pins.", _object_schema({"tx_pin": PIN, "rx_pin": PIN, "baudrate": {"type": "integer", "minimum": 300, "maximum": 5000000}, "data_bits": {"type": "integer", "enum": [5, 6, 7, 8]}, "parity": {"type": "string", "enum": ["none", "even", "odd"]}, "stop_bits": {"type": "number", "enum": [1, 1.5, 2]}, "flow_control": {"type": "string", "enum": ["none", "rts", "cts", "cts_rts"]}, "cts_pin": OPTIONAL_PIN, "rts_pin": OPTIONAL_PIN}, ["tx_pin", "rx_pin"]), self.uart_configure, False, True),
            Tool("uart_write", "Send text, base64, or byte-array data to the configured DUT UART.", _object_schema({"text": {"type": "string"}, "base64_data": {"type": "string"}, "bytes": BYTES}), self.uart_write),
            Tool("uart_read", "Collect buffered UART bytes from the DUT for a bounded time.", _object_schema({"timeout_ms": {"type": "integer", "minimum": 0, "maximum": 30000}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 10240}}), self.uart_read, True),
            Tool("pwm_configure", "Configure PWM on a free pin and channel.", _object_schema({"pin": PIN, "frequency_hz": {"type": "integer", "minimum": 1}, "channel": {"type": "integer", "minimum": 0, "maximum": 7}}, ["pin", "frequency_hz", "channel"]), self.pwm_configure, False, True),
            Tool("pwm_set", "Set PWM duty cycle in percent on a configured pin.", _object_schema({"pin": PIN, "duty_percent": {"type": "number", "minimum": 0, "maximum": 100}}, ["pin", "duty_percent"]), self.pwm_set, False, True),
        ]

    def device_status(self, _: JsonObject) -> JsonObject:
        return {"url": self.client.url, "connected": True, **self.pins_get({})}

    def pins_get(self, _: JsonObject) -> JsonObject:
        return {"pins": self._command({"type": "pins_get"}, "pins_get").get("pins", [])}

    def gpio_configure(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "gpio_setup", "pin": args["pin"], "dir": args["direction"]}, "status")

    def gpio_read(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "gpio_get", "pin": args["pin"]}, "gpio_state")

    def gpio_write(self, args: JsonObject) -> JsonObject:
        self.client.send({"type": "gpio_set", "pin": args["pin"], "state": args["level"]})
        return {"dispatched": True, "verified": False, "note": "Current firmware does not acknowledge gpio_set."}

    def i2c_configure(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "i2c_setup", "mode": "master", "sda": args["sda_pin"], "scl": args["scl_pin"], "freq": args["frequency_hz"]}, "i2c_response")

    def i2c_scan(self, _: JsonObject) -> JsonObject:
        return {"addresses": self._command({"type": "i2c_detect"}, "i2c_response").get("data", [])}

    def i2c_read(self, args: JsonObject) -> JsonObject:
        response = self._command({"type": "i2c_read", "addr": args["address"], "reg": args["register"], "len": args["length"]}, "i2c_response")
        return {"address": args["address"], "register": args["register"], "data": response.get("data", [])}

    def i2c_write(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "i2c_write", "addr": args["address"], "reg": args["register"], "data": args["data"]}, "i2c_response")

    def spi_configure(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "spi_setup", "mode": "Master", "conn_mode": args["mode"] + 1, "cs": args["cs_pin"], "sck": args["sck_pin"], "mosi": args["mosi_pin"], "miso": args["miso_pin"], "freq": args["frequency_hz"]}, "spi_response")

    def spi_transfer(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "spi_write", "word_length": 8, "data": args["write_data"], "data_read": args["read_length"]}, "spi_response")

    def uart_configure(self, args: JsonObject) -> JsonObject:
        command = {
            "type": "uart_setup", "tx": args["tx_pin"], "rx": args["rx_pin"],
            "cts": args.get("cts_pin", -1), "rts": args.get("rts_pin", -1),
            "baudrate": args.get("baudrate", 115200), "databits": {5: 0, 6: 1, 7: 2, 8: 3}[args.get("data_bits", 8)],
            "parity": {"none": 0, "even": 2, "odd": 3}[args.get("parity", "none")],
            "stopbits": {1: 1, 1.5: 2, 2: 3}[args.get("stop_bits", 1)],
            "flow": {"none": 0, "rts": 1, "cts": 2, "cts_rts": 3}[args.get("flow_control", "none")],
        }
        response = self.client.request(command, lambda message: message.get("type") == "uart_response" and "error" in message)
        return self._check_error(response)

    def uart_write(self, args: JsonObject) -> JsonObject:
        choices = [key for key in ("text", "base64_data", "bytes") if key in args]
        if len(choices) != 1:
            raise DeviceError("Provide exactly one of text, base64_data, or bytes")
        if choices[0] == "text":
            data: Any = args["text"]
        elif choices[0] == "base64_data":
            try:
                data = list(base64.b64decode(args["base64_data"], validate=True))
            except (ValueError, TypeError) as exc:
                raise DeviceError("base64_data is not valid base64") from exc
        else:
            data = args["bytes"]
        response = self.client.request({"type": "uart_write", "data": data}, lambda message: message.get("type") == "uart_response" and "error" in message)
        return self._check_error(response)

    def uart_read(self, args: JsonObject) -> JsonObject:
        data = self.client.read_uart(args.get("timeout_ms", 1000) / 1000, args.get("max_bytes", 4096))
        return {"length": len(data), "base64_data": base64.b64encode(data).decode("ascii"), "text_utf8": data.decode("utf-8", errors="replace")}

    def pwm_configure(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "pwm_setup", "pin": args["pin"], "freq": args["frequency_hz"], "channel": args["channel"]}, "pwm_response")

    def pwm_set(self, args: JsonObject) -> JsonObject:
        return self._command({"type": "pwm_set", "pin": args["pin"], "dutyCycle": args["duty_percent"]}, "pwm_response")


class McpServer:
    PROTOCOL_VERSION = "2025-06-18"

    def __init__(self, tools: DutTools) -> None:
        self.tools = tools

    def handle(self, request: JsonObject) -> JsonObject | None:
        if "id" not in request:
            return None
        request_id, method = request["id"], request.get("method")
        try:
            if method == "initialize":
                requested = request.get("params", {}).get("protocolVersion")
                return self._result(request_id, {
                    "protocolVersion": requested or self.PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "multitool-mcp", "version": "0.1.0"},
                    "instructions": "Debug DUTs through WE Multitool. Read pins_get before configuring pins. Prefer read-only observations first. Confirm wiring, voltage, bus role, and target limits before changing GPIO/PWM or writing UART/I2C/SPI. Commands are serialized because firmware responses have no request IDs.",
                })
            if method == "ping":
                return self._result(request_id, {})
            if method == "tools/list":
                return self._result(request_id, {"tools": [tool.definition() for tool in self.tools.tools.values()]})
            if method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                tool = self.tools.tools.get(name)
                if tool is None:
                    return self._error(request_id, -32602, f"Unknown tool: {name}")
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    return self._error(request_id, -32602, "Tool arguments must be an object")
                try:
                    self._validate(arguments, tool.schema)
                    value = tool.handler(arguments)
                    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                    return self._result(request_id, {"content": [{"type": "text", "text": text}], "structuredContent": value})
                except (DeviceError, KeyError, ValueError, TypeError) as exc:
                    return self._result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return self._error(request_id, -32603, f"Internal error: {exc}")

    @staticmethod
    def _result(request_id: Any, result: JsonObject) -> JsonObject:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> JsonObject:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    @classmethod
    def _validate(cls, value: Any, schema: JsonObject, path: str = "arguments") -> None:
        expected = schema.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                raise ValueError(f"{path} must be an object")
            missing = [name for name in schema.get("required", []) if name not in value]
            if missing:
                raise ValueError(f"{path} is missing required field(s): {', '.join(missing)}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extras = [name for name in value if name not in properties]
                if extras:
                    raise ValueError(f"{path} has unknown field(s): {', '.join(extras)}")
            for name, item in value.items():
                if name in properties:
                    cls._validate(item, properties[name], f"{path}.{name}")
            return
        if expected == "array":
            if not isinstance(value, list):
                raise ValueError(f"{path} must be an array")
            if len(value) > schema.get("maxItems", len(value)):
                raise ValueError(f"{path} has too many items")
            for index, item in enumerate(value):
                cls._validate(item, schema.get("items", {}), f"{path}[{index}]")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{path} must be an integer")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{path} must be a number")
        elif expected == "string" and not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path} must be one of {schema['enum']}")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} must be at most {schema['maximum']}")


def main() -> None:
    url = os.environ.get("WE_MULTITOOL_URL", "ws://we-multitool.local/ws")
    timeout = float(os.environ.get("WE_MULTITOOL_TIMEOUT_SECONDS", "5"))
    client = DeviceClient(url, timeout)
    server = McpServer(DutTools(client))
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Request must be a JSON object")
                response = server.handle(request)
            except (json.JSONDecodeError, ValueError) as exc:
                response = McpServer._error(None, -32700, str(exc))
            if response is not None:
                print(json.dumps(response, separators=(",", ":"), ensure_ascii=False), flush=True)
    finally:
        client.close()
