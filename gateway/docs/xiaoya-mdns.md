# 鈴鈴 gateway mDNS — ticket 35

When the Mac LAN IP rotates (historically hard-coded
`ws://192.168.1.167:8765` on the board), firmware should discover the
gateway instead of depending on a sticky DHCP lease. This document is the
**gateway announce + firmware browse contract**. No UX copy is required for
IP/mDNS (parent-facing screens stay silent).

Gateway announce is **opt-in and default off** so macOS `launchd` does not
open multicast sockets until dad enables it:

```bash
# gateway/.env (or launchd EnvironmentVariables)
STACKCHAN_MDNS=1
# optional: uv sync --extra mdns   # installs pure-Python zeroconf
```

Library choice: **[python-zeroconf](https://github.com/python-zeroconf/python-zeroconf)**
(`zeroconf` package, optional extra `mdns`). On macOS it speaks Bonjour /
mDNSResponder multicast; on Linux it is Avahi-compatible. No native
framework binding is required.

## Service contract

| Field | Value |
| --- | --- |
| Service type | `_xiaoya-gateway._tcp` (DNS-SD: `_xiaoya-gateway._tcp.local.`) |
| Default instance | `xiaoya-gateway` (override with `STACKCHAN_MDNS_INSTANCE`) |
| SRV port | `WS_PORT` (default `8765`) — the ESP32 XiaoZhi WebSocket |
| Host / A record | LAN IPv4 of the Mac (from non-wildcard `HOST`, else `VISION_HOST`, else default-route interface) |
| TXT `txtvers` | `1` |
| TXT `proto` | `ws` |
| TXT `path` | `/` (local gateway accepts WS at the root of `WS_PORT`; not `/xiaozhi/v1/`) |

Resolved WebSocket URL for firmware:

```text
ws://<resolved-ipv4>:<SRV-port><path>
```

Example: `ws://192.168.1.169:8765/`

`STACKCHAN_TOKEN` continues to ride in the WebSocket `Authorization: Bearer …`
upgrade header; it is **not** placed in TXT (TXT is visible on the LAN).

Related but **different** board services (firmware already publishes these
from `wifi_config_service`; do not confuse them with the Mac gateway):

- `_stackchan-config._tcp` / `_http._tcp` — board settings HTTP :80
- `_stackchan-audio._udp` — board RTP audio :6970

## Gateway behaviour

- Module: `stackchan_mcp/mdns_announce.py`
- Gate: `STACKCHAN_MDNS` in `{1,true,yes,on}` (case-insensitive); default `0`
- Start: after the ESP32 WebSocket server binds successfully
- Stop: unregister + close on gateway shutdown
- Missing `zeroconf`, no usable IPv4, or register errors → log a warning and
  continue; the WS server still runs for static-URL clients

Enable on the Mac (after pull, no flash):

```bash
cd gateway
uv sync --extra mdns
# add to .env:
echo 'STACKCHAN_MDNS=1' >> .env
# restart gateway / launchd service (dad-owned step; this ticket does not deploy)
```

## Firmware follow-up (no flash in this ticket)

Goal: stop depending on a hard-coded Mac IP when NVS has no override.

1. **Keep NVS / settings override** — `DeviceConfig::xiaozhi_url` (Wi-Fi
   settings page / BLE / `/api/xiaozhi-url`) remains authoritative when
   non-empty. Static `ws://192.168.x.y:8765` must keep working for rollback
   and for networks that filter multicast.
2. **Browse when URL empty (or sentinel)** — after STA is up, use existing
   ESP-IDF `mdns` (already linked via `wifi_config_service` /
   `espressif/mdns`) to query PTR `_xiaoya-gateway` / `_tcp`, then SRV + A.
   Suggested API: `mdns_query_ptr("_xiaoya-gateway", "_tcp", …)` then build
   `ws://<ip>:<port><path>` from the result + TXT `path` (default `/`).
3. **Where to plug in** — today `main/app_main.cpp` copies
   `cfg.xiaozhi_url` into `conversation_task` → `XiaoZhiClient(url, token)`.
   Resolve before constructing the client (or retry on connect failure with
   a fresh browse). Do **not** replace the board’s own
   `_stackchan-config` advertiser.
4. **Reconnect** — on WS failure after Mac sleep / IP change, re-browse
   rather than sticking to the last IP forever; still prefer a non-empty
   NVS URL if the parent set one deliberately.
5. **Acceptance** — board connects after Mac IP `.167→.169` with empty NVS
   URL and gateway `STACKCHAN_MDNS=1`; with NVS static URL set, board ignores
   mDNS and still connects.

## Rollback

- Gateway: unset `STACKCHAN_MDNS` or set `0`, restart — no multicast announce
- Firmware: leave / restore a static XiaoZhi URL in NVS / settings page
- UX: none (no parent-facing strings for this path)

## Manual test on Mac (announce only)

```bash
cd gateway
uv sync --extra mdns
STACKCHAN_MDNS=1 WS_PORT=8765 uv run stackchan-mcp --check   # optional preflight
STACKCHAN_MDNS=1 uv run stackchan-mcp                        # or launchd after .env edit

# another terminal — Bonjour browse
dns-sd -B _xiaoya-gateway._tcp local.
dns-sd -L xiaoya-gateway _xiaoya-gateway._tcp local.
# expect port 8765 and txt path=/ proto=ws txtvers=1
```

Unit tests mock `Zeroconf` / `ServiceInfo` and do not require a real LAN
(`gateway/tests/test_mdns_announce.py`).

## Out of scope here

- Flashing firmware
- Changing launchd plists without dad approval (env flag alone is enough)
- Advertising capture HTTP `:8766` (board WS is the breakage; vision URL
  remains `VISION_HOST` / NVS if needed later)
