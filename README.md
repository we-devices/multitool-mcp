# WE Multitool DUT MCP

`we-multitool-dut` is a dependency-free [Model Context Protocol](https://modelcontextprotocol.io/) server for inspecting and debugging a device under test (DUT) through a Wise Electronics WE Multitool.

It runs over standard input/output and bridges the Multitool's JSON-over-WebSocket API. It exposes bounded MCP tools for GPIO, I2C, SPI, UART, PWM, and GPIO ownership.

## Requirements

- Python 3.11 or later
- A WE Multitool reachable over WebSocket

## Install

Install from PyPI once published:

```shell
python -m pip install we-multitool-dut
```

For development from a clone:

```shell
python -m pip install -e .
```

## Configure an MCP client

Run the installed command:

```toml
[mcp_servers.we_multitool_dut]
command = "we-multitool-dut"
env_vars = ["WE_MULTITOOL_URL", "WE_MULTITOOL_TIMEOUT_SECONDS"]
startup_timeout_sec = 10
tool_timeout_sec = 35
default_tools_approval_mode = "prompt"
```

You can also use `python -m dut_mcp`.

By default, the server connects to `ws://we-multitool.local/ws`. Set these environment variables before starting the MCP client to change that:

```shell
WE_MULTITOOL_URL=ws://10.0.0.1/ws
WE_MULTITOOL_TIMEOUT_SECONDS=8
```

On PowerShell:

```powershell
$env:WE_MULTITOOL_URL = "ws://10.0.0.1/ws"
$env:WE_MULTITOOL_TIMEOUT_SECONDS = "8"
```

## Safety and firmware behavior

Read `pins_get` before configuring pins, and verify wiring, voltage, bus role, and DUT limits before driving signals.

- Commands are serialized because firmware responses do not carry request IDs.
- UART data received while another command awaits a response is buffered.
- The `gpio_set` firmware command has no acknowledgement; `gpio_write` reports it as dispatched but unverified.
- The firmware tracks one active WebSocket response socket. Do not use the browser UI and MCP bridge concurrently.

## Development

```shell
python -m pip install -e ".[test]"
python -m pytest
```

The runtime has no third-party dependencies. The optional `test` extra installs pytest.

## License

MIT. See [LICENSE](LICENSE).
