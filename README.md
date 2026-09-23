# WE Multitool DUT MCP

`multitool-mcp` is a dependency-free [Model Context Protocol](https://modelcontextprotocol.io/) server for inspecting and debugging a device under test (DUT) through a Wise Electronics WE Multitool.

It runs over standard input/output and bridges the Multitool's JSON-over-WebSocket API. It exposes bounded MCP tools for GPIO, I2C, SPI, UART, PWM, and GPIO ownership.

## Requirements

- Python 3.11 or later
- A WE Multitool reachable over WebSocket

## Install

Install from PyPI once published:

```shell
python -m pip install multitool-mcp
```

For development from a clone:

```shell
python -m pip install -e .
```

## Configure an MCP client

Run the installed command:

```toml
[mcp_servers.we_multitool_dut]
command = "multitool-mcp"
env_vars = ["WE_MULTITOOL_URL", "WE_MULTITOOL_TIMEOUT_SECONDS"]
startup_timeout_sec = 10
tool_timeout_sec = 35
default_tools_approval_mode = "prompt"
```

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

`gpio_configure` accepts `input`, `output`, and `open-drain` directions. Open-drain mode requires firmware with `open-drain` GPIO setup support and an appropriate external pull-up.

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

Licensed under the GNU General Public License, version 3 or later
(GPL-3.0-or-later). See [LICENSE](LICENSE).
