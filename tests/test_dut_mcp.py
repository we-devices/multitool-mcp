import base64
import json
import socket

from dut_mcp.device import DeviceClient
from dut_mcp.server import DutTools, McpServer
from dut_mcp.websocket import WebSocket


class FakeTransport:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.sent = []
        self.connected = False

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def send_text(self, text):
        self.connected = True
        self.sent.append(json.loads(text))

    def receive_text(self, timeout=None):
        if not self.responses:
            raise socket.timeout()
        return json.dumps(self.responses.pop(0))


class FakeSocket:
    def __init__(self, incoming=b""):
        self.incoming = bytearray(incoming)
        self.outgoing = b""
        self.timeout = None

    def recv(self, length):
        result = bytes(self.incoming[:length])
        del self.incoming[:length]
        return result

    def sendall(self, data):
        self.outgoing += data

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout


def make_server(responses=()):
    transport = FakeTransport(responses)
    client = DeviceClient("ws://test/ws", timeout=0.01, transport=transport)
    return McpServer(DutTools(client)), transport


def test_initialize_and_tool_annotations():
    server, _ = make_server()
    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert initialized["result"]["protocolVersion"] == "2025-06-18"
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
    assert tools["gpio_read"]["annotations"]["readOnlyHint"] is True
    assert tools["gpio_write"]["annotations"]["readOnlyHint"] is False
    assert tools["i2c_read"]["inputSchema"]["additionalProperties"] is False


def test_i2c_read_translates_wire_contract():
    server, transport = make_server([
        {"type": "uart_response", "data": base64.b64encode(b"boot").decode()},
        {"type": "i2c_response", "result_type": 2, "addr": 0x48, "data": [1, 2]},
    ])
    response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "i2c_read", "arguments": {"address": 0x48, "register": 1, "length": 2}}})
    assert transport.sent == [{"type": "i2c_read", "addr": 0x48, "reg": 1, "len": 2}]
    assert response["result"]["structuredContent"]["data"] == [1, 2]


def test_uart_rx_is_buffered_while_waiting_for_ack():
    server, _ = make_server([
        {"type": "uart_response", "data": base64.b64encode(b"hello").decode()},
        {"type": "uart_response", "data": "Data has been sent", "error": False},
    ])
    write = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "uart_write", "arguments": {"text": "AT\r\n"}}})
    assert write["result"].get("isError") is not True
    read = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "uart_read", "arguments": {"timeout_ms": 0, "max_bytes": 20}}})
    assert read["result"]["structuredContent"]["text_utf8"] == "hello"


def test_gpio_write_reports_unverified_dispatch():
    server, transport = make_server()
    response = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "gpio_write", "arguments": {"pin": 4, "level": 1}}})
    assert transport.sent == [{"type": "gpio_set", "pin": 4, "state": 1}]
    assert response["result"]["structuredContent"]["verified"] is False


def test_device_error_becomes_mcp_tool_error():
    server, _ = make_server([{"type": "i2c_response", "result_type": 0, "data": "not connected"}])
    response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "i2c_scan", "arguments": {}}})
    assert response["result"]["isError"] is True
    assert "not connected" in response["result"]["content"][0]["text"]


def test_uart_write_requires_exactly_one_encoding():
    server, _ = make_server()
    response = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "uart_write", "arguments": {"text": "x", "bytes": [1]}}})
    assert response["result"]["isError"] is True


def test_arguments_are_validated_before_device_access():
    server, transport = make_server()
    response = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "gpio_write", "arguments": {"pin": -1, "level": 2}}})
    assert response["result"]["isError"] is True
    assert transport.sent == []


def test_websocket_decodes_server_text_frame():
    websocket = WebSocket("ws://test/ws")
    websocket._socket = FakeSocket(b"\x81\x05hello")
    assert websocket.receive_text() == "hello"


def test_websocket_masks_client_text_frame():
    websocket = WebSocket("ws://test/ws")
    sock = FakeSocket()
    websocket._socket = sock
    websocket.send_text("hello")
    assert sock.outgoing[0] == 0x81
    assert sock.outgoing[1] & 0x80
    length = sock.outgoing[1] & 0x7F
    mask = sock.outgoing[2:6]
    encoded = sock.outgoing[6:6 + length]
    assert bytes(value ^ mask[index % 4] for index, value in enumerate(encoded)) == b"hello"
