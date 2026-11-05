# Homework session protocol (28)

The gateway uses the existing forms Sender and ready handshake:
`{"type":"xiaoya","cmd":"session","active":true}` (or false).
A homework list ask or voice session armed with unfinished homework starts the session. `end_homework_session` is the
voice tool for 收工. Any user input/tool interaction refreshes a 30 minute
monotonic inactivity timer. Dad can change the safety timeout with
`stackchan-parent set session_idle_min 30` (1–240 minutes); the gateway poll
applies the current value to active sessions too. Reconnect re-sends the current flag after form_state.
The flag survives a connection loss in gateway memory, not a gateway restart.
Short breaks keep it active; firmware must also retain it across local breaks.

Firmware parsing may arrive separately (wt-40): while active, suspend only
rest_after_min Sleepy decay, preserve disconnect indication, and restart the
rest timer when active becomes false. No mDNS setting is changed.

# Completion celebration (29)

Successful mark_done (including homework_photo completion without a usable photo)
returns device_messages and sends celebrate through the same ready-gated Sender:
`{"type":"xiaoya","cmd":"celebrate","level":"item"}` or `"all"`.
The final completion sends celebrate before session active:false. A persisted
Hong Kong calendar-day flag allows all only once, including across gateway
restart. Redo never clears that flag; completing it again receives item.
Duplicate completion of an already-done item does not animate again.

Firmware follow-up: reuse Happy/Excited overlays (item <=3s; all 5–8s plus
sfx.celebrate_all when available), then return to companion. No new art/persona.
The completion tool exposes an effort praise line; the agent should speak it.
