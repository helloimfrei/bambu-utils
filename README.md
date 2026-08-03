# bambu-utils

Direct LAN control for Bambu Lab printers without Bambu's proprietary network
plugin or cloud service. The CLI talks to the printer itself:

- implicit FTPS for upload, download, list, and delete;
- TLS MQTT for status, print start, pause, resume, and stop;
- validation that a `.3mf` actually contains sliced plate G-code before starting it.

This is an independent client for Bambu's unsupported Developer Mode protocol,
not an official SDK. The protocol surface comes from
[Bambu Studio's open command code](https://github.com/bambulab/BambuStudio) and
the community-maintained
[OpenBambuAPI protocol reference](https://github.com/Doridian/OpenBambuAPI).
Bambu's own security paper says Developer Mode leaves MQTT and FTP available to
third-party software and makes the user responsible for network security:
[Bambu Lab Security White Paper](https://cdn1.bambulab.com/trust-center/file/bambulab-security-whitepaper-en.pdf).

## Printer setup

1. Reserve a stable LAN IP for the printer in DHCP.
2. On the printer, enable **LAN Only Mode**, then **Developer Mode**.
3. Record the LAN access code and printer serial from the printer's settings.
4. Keep the printer on a trusted/isolated network. Never expose its ports with
   router port forwarding.

Developer Mode is required on current firmware. It may disable cloud/Handy
features, and Bambu does not promise compatibility or support for this API.
This implementation targets the local protocol used by the X1, P1, A1, and A1
Mini families. It has not yet been verified against newer H2/P2/X2-family
firmware.

## Install and configure

Python 3.12 and [`uv`](https://docs.astral.sh/uv/) are required.

```sh
uv sync
$EDITOR .env
```

The repository already contains a gitignored `.env` template:

```dotenv
BAMBU_HOST=192.168.1.50
BAMBU_SERIAL=01P00A000000000
BAMBU_ACCESS_CODE=12345678
BAMBU_PRINTER_MODEL=P1S
BAMBU_NOZZLE_DIAMETER=0.4
BAMBU_TIMEOUT=15
```

The CLI reads `.env` from the current working directory. Precedence is CLI
flag, shell environment, `.env`, then built-in default. The access code is a
password: never commit the real `.env`. `.env.example` is the safe template for
other checkouts.

`BAMBU_PRINTER_MODEL` accepts `X1C`, `X1E`, `P1S`, `P1P`, `A1 Mini`, or
`A1`. Newer printer families are intentionally rejected until their different
print payloads are implemented and hardware-validated.

## Send a sliced file

Export a sliced plate from Bambu Studio without installing its optional network
plugin, or from OrcaSlicer. The usual output is `.gcode.3mf`.

```sh
# Safe default: upload to the printer, but do not start it.
./scripts/send-slice ~/Downloads/part.gcode.3mf

# Explicitly upload and start plate 1 from the external spool.
./scripts/send-slice --print ~/Downloads/part.gcode.3mf
```

The wrapper runs from the repository so it picks up the local `.env`. With no
flag it only uploads to `cache/` over FTPS. `--print` additionally validates the
archive, computes its MD5, sends the MQTT `project_file` command, and waits for
telemetry to confirm that the uploaded filename reached `RUNNING`. It prints
plate 1 from the external spool by default. If confirmation does not arrive
within `BAMBU_TIMEOUT`, the command exits nonzero without submitting a second
print request.

Before uploading a print, the command fails closed unless all of these agree:

- configured model and nozzle in `.env`;
- model inferred from the connected printer serial;
- nozzle diameter reported live by the printer;
- model name, model ID, and nozzle metadata embedded independently in the 3MF
  project, slice information, and selected plate G-code.

Missing or conflicting metadata is rejected before FTPS upload. The upload-only
form does not execute a file and therefore does not apply this gate. Starting a
file manually from the printer screen also bypasses this utility's validation.

Select another plate or map project filaments to AMS trays:

```sh
./scripts/send-slice --print multi-color.gcode.3mf --plate 2 --ams 0,1,4
```

AMS tray IDs are absolute: `0..3` are slots in the first AMS, `4..7` in the
second, and `-1` means unmapped. The order must match the filament order in the
sliced project.

Bed leveling, flow calibration, vibration calibration, and layer inspection
default on to match the common local-print payload. Each can be disabled:

```sh
./scripts/send-slice --print part.gcode.3mf \
  --no-flow-calibration --no-layer-inspection --timelapse
```

Plain `.gcode` is also accepted and started with the printer's `gcode_file`
command. This utility does not slice STL/STEP/model-only 3MF files.

## Other operations

```sh
uv run bambu-utils status
uv run bambu-utils pause
uv run bambu-utils resume
uv run bambu-utils stop

uv run bambu-utils upload local.gcode.3mf
uv run bambu-utils files cache
uv run bambu-utils download cache/old.gcode.3mf ./old.gcode.3mf
uv run bambu-utils delete cache/old.gcode.3mf
```

Global settings can also be passed before the subcommand, for example
`bambu-utils --host 192.168.1.50 ... status`, but environment variables avoid
putting the access code in shell history.

## Tailscale

The printer cannot run Tailscale itself. Put a Tailscale node on the printer's
LAN and configure it as a
[subnet router](https://tailscale.com/kb/1019/subnets), then continue using the
printer's LAN IP as `BAMBU_HOST`. No multicast discovery is needed because this
CLI always uses an explicit address.

MQTT uses TCP 8883. FTPS uses TCP 990 for its control connection plus a
printer-selected passive TCP data port for every list or transfer. Bambu does
not publish a stable passive-port range, so a restrictive tailnet policy must
allow the initiating user/device TCP access to the printer IP, not just ports
8883 and 990. Scope that rule to this one destination and trusted principals;
Tailscale recommends its deny-by-default
[grants policy](https://tailscale.com/docs/features/access-control/grants) for
new configurations.

Both printer services use TLS, but the printer presents a certificate that
cannot be validated for its LAN IP. This client therefore encrypts the
connection without authenticating that certificate. Tailscale/LAN access
control and protection of the access code remain the trust boundary.

## Development

```sh
uv sync --dev
uv run pytest
uv run pyright
uv build --no-sources
```
