# Local commands and recovery (44d)

`GeminiVoiceProxy.handle_local_text_before_session(text)` is an explicit hook,
not a working pre-gbot ASR route. Firmware currently streams audio; gbot
transcripts arrive after session creation. The cmux VoiceInputBridge is a
separate cloud-STT terminal feature. Future local ASR must call the hook before
opening any gbot session/turn and forward only when it returns None. No new ASR
backend is installed. Replies use existing Edge TTS with HiuMaan (requires its
existing network service), not a new AI backend. Timer state is in memory and
supports start/query/stop only; no automatic expiry announcement yet.

The parent setting `local_commands_enabled` defaults true and uses the existing
settings API, `stackchan-parent set local_commands_enabled off`, and
`xiaoya_set_setting` MCP tool. All added reply templates live in
`data/local_lines_placeholder.json`, marked as placeholders.

Break reuses `XiaoyaGames.on_double_press()` (exit/decline). This gateway has no
standalone firmware break-start command, so full BOOT break-state parity remains
open. Games use only `start_pending()` and cannot grant their own unlock.
Homework completion accepts one exact unfinished title, then uses the existing
forms store. It does not initiate the separate photo capture dialogue.

Authenticated parent recovery routes:

- GET `/parent/v1/photos`: indexed photos, availability and review status.
- GET `/parent/v1/photos/{id}/path`: metadata and local path; no image download.
- POST `/parent/v1/photos/{id}/done`: mark reviewed, without deleting the image.
- GET `/parent/v1/events?after=0`: monotonically numbered recovery events.

The private journal under STACKCHAN_STATE_DIR retains the latest 500 photo
records/events. Capture files retain their existing 24-hour expiry; unavailable
paths are explicitly reported. New homework completions and consent-gated help
send outcomes are recorded; this does not backfill historical files/events.
IDs, never client paths, select photos. Existing loopback/Host/Bearer guard applies.

#32: No existing `display notification`/terminal-notifier fallback helper exists.
The osascript wrappers control Mac apps/media, not notifications. Per scope, no
new outbound channel or notification wrapper was added. Existing CLI retries
and truthful failure replies remain; Mac notification fallback is still open.
