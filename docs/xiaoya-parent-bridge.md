# 小芽 parent bridge (ticket 24)

Decision: use a separate loopback-only token API, CLI and optional stdio MCP
wrapper (option D). There is no web app or web page.

Options considered:

| Option | Trade-off |
| --- | --- |
| A: bot writes a watched settings file | Simple, but weak validation and no reliable acknowledgement. |
| B: extend device HTTP :8766 | Already available, but binds 0.0.0.0 and mixes child/device and parent authority. |
| C: MCP inside the existing stdio gateway | Couples parent access to ownership of the running gateway process. |
| D: separate loopback API + CLI + optional MCP | Chosen: explicit parent authority and independent clients. |

The API binds **127.0.0.1 only** and is off unless
`STACKCHAN_PARENT_API=1`. Default port: 18772 (`STACKCHAN_PARENT_API_PORT`).
It rejects non-loopback peers, Host values other than localhost/127.0.0.1
(with optional port), browser Origin headers and the device token.
A separate bearer token comes from `STACKCHAN_PARENT_TOKEN` or
`STACKCHAN_PARENT_TOKEN_FILE` (default `~/.stackchan/parent_token`). When
missing, the enabled API generates a token file with mode 0600 and logs only
its path. Never put tokens in the repository, persona or chat. Local processes
with the parent's account privileges can read that file; loopback is not an
isolation boundary against malicious software on the Mac.

The CLI reads the token itself. The child voice path has neither a settings
tool nor the parent token. **The 小芽 child agent must not be given Mac shell
access.** Use a separate parent bot with approval for shell commands.
Status deliberately excludes transcripts, tool history, device IDs/MAC,
agent IDs and tokens. Voice errors expose the message only.

## Reference

All HTTP calls require `Authorization: Bearer <parent token>`:

| Endpoint | Result |
| --- | --- |
| GET /parent/v1/status | Allowlisted device, voice, wake and settings status; summary_zh |
| GET /parent/v1/settings | Settings, validation schema and device_support |
| POST /parent/v1/settings | JSON object of settings; atomic validation; changed old/new values |

CLI (default URL `http://127.0.0.1:18772`; override with `--url`):

```sh
stackchan-parent status
stackchan-parent status --text
stackchan-parent settings
stackchan-parent set volume 40
stackchan-parent set camera_presence off
stackchan-parent mcp
```

The optional stdio MCP wrapper exposes `xiaoya_status`, `xiaoya_get_settings`
and `xiaoya_set_setting(key, value)` over the same HTTP API. It does not start
the gateway. Bad authentication and unavailable API cause nonzero CLI exits.

| Setting | Type / range | Default | Consumer ticket |
| --- | --- | --- | --- |
| volume | integer 0–100 | 60 | 16 |
| rest_after_min | integer 1–60 minutes without interaction before resting/Sleepy face | 5 | 12/15 |
| camera_presence | boolean | false | 23 |
| notifications | boolean | false | 19 |
| battery (status only) | percent or null when unreported | null | 19 |

Settings persist at `~/.stackchan/parent_settings.json` (override
`STACKCHAN_PARENT_SETTINGS_PATH`), atomically with file mode 0600 and directory
mode 0700. **Stored does not mean applied to the robot.** Protocol v1 currently
has no messages for these settings; device_support reports the pending ticket.
No new protocol message types are introduced.

Ticket 16 should consume volume through the pusher registry when firmware has
an approved mechanism. Ticket 19 should call `DebugStatus.on_device_battery`
when battery reporting exists and consume notifications from this store.
Ticket 23 should consume camera_presence, preserving its off default. Ticket 21
should install `~/.stackchan/bin/stackchan-parent`, configure the separate
parent bot and opt in to API startup; it must keep the parent token out of the
child process/agent context. Future async pushers register with
`ParentSettings.register_pusher(key, async_callable)` and receive the current
value on change and ESP32 connection. No pusher is registered by default.

## Why not the other options

- **A (watched file):** the bot would have to edit JSON by hand, typos are only
  noticed later, there is no immediate "changed / refused" answer and no way to
  read live status (connection, wake word, battery). It is not safer either:
  the file is writable by the same Mac account that would hold the token.
- **B (port 8766):** that server listens on the home network for the device;
  parent authority must never be reachable from the LAN.
- **C (MCP in the gateway process):** the gateway's stdio MCP belongs to
  whichever client started it (or the launchd FIFO); a second MCP client
  cannot attach to it.

## Not in this batch

Wake word phrase, KWS threshold and listening mode (wake word / button only)
are still environment settings read at gateway start. The parent bot can show
them (`status`) but changing them means editing the service environment and
restarting the gateway (ticket 21 guide). The child profile switch itself is
deliberately not exposed through this API.
